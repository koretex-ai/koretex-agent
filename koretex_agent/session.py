"""The kernel session loop: one profile, one work order, one bounded conversation.

Long-horizon state lives outside (mission coordinator, disk) — a session only ever
sees its own small context. When the model stops calling tools, we demand the
terminal handoff as schema-constrained JSON: malformed output is not a failure
mode we accept from the serving layer."""
from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from .client import ChatResult, Client
from .schemas import WorkOrder, response_schema
from .tools import Toolbox
from .trajectory import TrajectoryRecorder


def constrained_call(
    client: Client,
    messages: list[dict],
    model_cls: type[BaseModel],
    usage_sink: list[dict] | None = None,
    attempts: int = 3,
    reasoning_effort: str | None = None,
):
    """Chat with grammar-constrained decoding, validate client-side (the wire
    schema is sanitized — pydantic holds the full contract), retry on mismatch
    with the validation error fed back."""
    msgs = list(messages)
    last: ValidationError | None = None
    for _ in range(attempts):
        res = client.chat(
            msgs,
            response_format=response_schema(model_cls),
            reasoning_effort=reasoning_effort,
        )
        if usage_sink is not None:
            usage_sink.append(res.usage)
        try:
            return model_cls.model_validate_json(res.message.get("content") or "{}"), res
        except ValidationError as e:
            last = e
            msgs = msgs + [
                res.message,
                {"role": "user", "content": f"Invalid {model_cls.__name__}: {e}. Emit corrected JSON only."},
            ]
    raise RuntimeError(f"model failed to produce valid {model_cls.__name__}: {last}")


class SessionResult(BaseModel):
    handoff: dict
    turns: int
    prompt_tokens: int
    completion_tokens: int
    session_id: str
    hit_turn_cap: bool = False  # ran out of turns without ever stopping cleanly —
    # the handoff is then produced under duress and its verdict is unreliable
    # (a cut-off validator emits false FAILs — see mission run m2, 2026-07-03).


def strip_thinking(msg: dict) -> dict:
    """Drop reasoning fields before appending to history — they'd blow the budget."""
    return {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")}


# Keep the most recent N tool results in full on the wire; elide older ones.
# Re-sending every prior file dump and command output on every turn is the
# dominant prompt-token cost — it compounds ~O(turns^2). The model acts on
# recent state, and the assistant tool_call it made is preserved either way, so
# it still sees *what* it ran; only the stale *output* is dropped. The full
# history is still recorded to the trajectory — this trims only what goes on the
# wire, never the record.
TOOL_ELIDE_KEEP = 3


def _elide_old_tool_results(messages: list[dict], keep_last: int = TOOL_ELIDE_KEEP) -> list[dict]:
    tool_idxs = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    if len(tool_idxs) <= keep_last:
        return messages
    stale = set(tool_idxs[:-keep_last])
    out = []
    for i, m in enumerate(messages):
        if i in stale:
            n = len(m.get("content") or "")
            out.append({**m, "content": f"[earlier tool output elided — {n} chars]"})
        else:
            out.append(m)
    return out


# Reasoning-mode policy → OpenAI `reasoning_effort`. Thinking on: leave it
# unset (model's default). Off: "none" — honored by the dispatcher's llama.cpp
# and local Ollama alike, and applied to *every* call in the session (agentic
# turns and the terminal handoff) so no reasoning leaks back in.
def _effort(thinking: bool) -> str | None:
    return None if thinking else "none"


def run_session(
    profile_name: str,
    system_prompt: str,
    order: WorkOrder,
    toolbox: Toolbox,
    handoff_model: type[BaseModel],
    client: Client | None = None,
    max_turns: int = 20,
    thinking: bool = True,
) -> SessionResult:
    client = client or Client()
    rec = TrajectoryRecorder(profile_name, order.model_dump())
    effort = _effort(thinking)

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _render_order(order)},
    ]
    rec.message(messages[0])
    rec.message(messages[1])

    ptok = ctok = 0
    turns = 0
    stopped_cleanly = False
    for turns in range(1, max_turns + 1):
        res: ChatResult = client.chat(
            _elide_old_tool_results(messages), tools=toolbox.schemas(), reasoning_effort=effort
        )
        ptok += res.usage.get("prompt_tokens", 0)
        ctok += res.usage.get("completion_tokens", 0)
        rec.usage(res.usage)
        msg = strip_thinking(res.message)
        messages.append(msg)
        rec.message(msg)

        calls = msg.get("tool_calls") or []
        if not calls:
            stopped_cleanly = True
            break  # model believes it is finished
        for call in calls:
            fn = call["function"]
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = toolbox.call(fn["name"], args)
            rec.tool_result(fn["name"], args, result)
            messages.append(
                {"role": "tool", "tool_call_id": call.get("id", ""), "content": result}
            )

    # Terminal handoff: schema-enforced, no tools offered.
    messages.append(
        {
            "role": "user",
            "content": (
                f"Emit your final {handoff_model.__name__} as JSON now. "
                f"order_id={order.order_id}. Base every field on what you actually "
                "executed and observed above — never on intention."
            ),
        }
    )
    usage_sink: list[dict] = []
    handoff, _ = constrained_call(
        client, _elide_old_tool_results(messages), handoff_model, usage_sink, reasoning_effort=effort
    )
    for u in usage_sink:
        ptok += u.get("prompt_tokens", 0)
        ctok += u.get("completion_tokens", 0)
    rec.verdict(handoff.model_dump())

    return SessionResult(
        handoff=handoff.model_dump(),
        turns=turns,
        prompt_tokens=ptok,
        completion_tokens=ctok,
        session_id=rec.session_id,
        hit_turn_cap=not stopped_cleanly,
    )


def _render_order(order: WorkOrder) -> str:
    parts = [
        f"WORK ORDER {order.order_id}",
        "Your tools already run inside the work directory — use paths relative to it "
        "(e.g. `cli.py`, `tests/`, `.`). Do not invent absolute paths like /workdir.",
        f"Task: {order.task}",
    ]
    if order.assertions:
        parts.append("Contract assertions:")
        for a in order.assertions:
            cmd = f" (command: {a.command})" if a.command else ""
            parts.append(f"- {a.item_id}: {a.statement}{cmd}")
    if order.context:
        parts.append(f"Context:\n{order.context}")
    if order.skills:
        parts.append("Skills available via use_skill: " + ", ".join(order.skills))
    return "\n".join(parts)
