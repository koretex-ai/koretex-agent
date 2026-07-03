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
):
    """Chat with grammar-constrained decoding, validate client-side (the wire
    schema is sanitized — pydantic holds the full contract), retry on mismatch
    with the validation error fed back."""
    msgs = list(messages)
    last: ValidationError | None = None
    for _ in range(attempts):
        res = client.chat(msgs, response_format=response_schema(model_cls))
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


def strip_thinking(msg: dict) -> dict:
    """Drop reasoning fields before appending to history — they'd blow the budget."""
    return {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")}


def run_session(
    profile_name: str,
    system_prompt: str,
    order: WorkOrder,
    toolbox: Toolbox,
    handoff_model: type[BaseModel],
    client: Client | None = None,
    max_turns: int = 20,
) -> SessionResult:
    client = client or Client()
    rec = TrajectoryRecorder(profile_name, order.model_dump())

    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": _render_order(order)},
    ]
    rec.message(messages[0])
    rec.message(messages[1])

    ptok = ctok = 0
    turns = 0
    for turns in range(1, max_turns + 1):
        res: ChatResult = client.chat(messages, tools=toolbox.schemas())
        ptok += res.usage.get("prompt_tokens", 0)
        ctok += res.usage.get("completion_tokens", 0)
        rec.usage(res.usage)
        msg = strip_thinking(res.message)
        messages.append(msg)
        rec.message(msg)

        calls = msg.get("tool_calls") or []
        if not calls:
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
    handoff, _ = constrained_call(client, messages, handoff_model, usage_sink)
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
