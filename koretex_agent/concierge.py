"""Tier 0 — the concierge: the on-device front door that drives the escalation
ladder. A tiny local model answers the cheapest requests itself and routes
everything heavier down the ladder — chat (here) → task (one worker, tier 1) →
mission (full coordinator, tier 2). It never does task/mission work itself; it
decides and restates. A tier-1 worker that falls short escalates to a mission.

The concierge is a consumer-side latency/cost optimization, not a network node:
the routing call runs on the small local model (`client`); the actual work runs
wherever `work_client` points (the Koretex network in deployment)."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

from pydantic import BaseModel

from .client import Client, escalation_client_from_env
from .profiles import CONCIERGE, WORKER
from .schemas import Route, WorkHandoff, WorkOrder
from .session import SessionResult, _effort, constrained_call, run_session
from .tiers import Tier, TierLedger
from .tools import Toolbox

ROUTING_STORE = Path.home() / ".koretex-agent" / "routing"


class ConciergeResult(BaseModel):
    route: str                    # chat | task | task->mission | mission
    reason: str = ""
    reply: str = ""               # chat answer
    work: str = ""                # the instruction/brief handed down
    handoff: dict | None = None   # tier-1 worker handoff, if a task ran
    mission: dict | None = None   # mission state, if a mission ran (routed or escalated)
    ledger: dict | None = None    # per-tier token accounting across the whole ladder


# ── human-facing rendering: talk to it, don't read its JSON ─────────────────
def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def render_reply(result: ConciergeResult, color: bool | None = None) -> str:
    """Format a ConciergeResult for a person: chat → just the reply; task/mission
    → a short outcome line + a dim status footer. The raw JSON stays available
    via --json for scripts/debugging."""
    if color is None:
        color = sys.stdout.isatty()
    dim = "\033[2m" if color else ""
    grn = "\033[32m" if color else ""
    red = "\033[31m" if color else ""
    rst = "\033[0m" if color else ""
    tok = _fmt_tokens((result.ledger or {}).get("total_tokens", 0))

    if result.route == "chat":
        return result.reply or "(no reply)"

    if result.route == "task":
        wh = result.handoff or {}
        ok = wh.get("done")
        badge = f"{grn}✓{rst}" if ok else f"{red}✗{rst}"
        out = [(wh.get("report") or "").strip() or ("done" if ok else "could not complete")]
        if wh.get("files_touched"):
            out.append(f"{dim}files: {', '.join(wh['files_touched'])}{rst}")
        out.append(f"{dim}{badge} task · {tok} tokens{rst}")
        return "\n".join(out)

    # task->mission or mission
    ms = result.mission or {}
    tasks = ms.get("tasks", [])
    cleared = sum(1 for t in tasks if t.get("status") == "cleared")
    done = ms.get("status") == "done"
    badge = f"{grn}✓{rst}" if done else f"{red}✗{rst}"
    out = []
    if result.route == "task->mission":
        out.append(f"{dim}(a quick attempt fell short — ran it as a full mission){rst}")
    out.append(f"{badge} mission {ms.get('status', '?')} — {cleared}/{len(tasks)} tasks cleared")
    extra = " · escalated to a stronger model" if ms.get("escalations") else ""
    out.append(f"{dim}· {tok} tokens{extra}{rst}")
    return "\n".join(out)


def decide(message: str, client: Client, usage: list[dict] | None = None) -> Route:
    """One constrained call on the small local model — the routing decision.
    Appends its token usage to `usage` (tier-0 accounting) when provided."""
    msgs = [
        {"role": "system", "content": CONCIERGE.system_prompt()},
        {"role": "user", "content": message},
    ]
    route, _ = constrained_call(client, msgs, Route, usage,
                                reasoning_effort=_effort(CONCIERGE.thinking))
    return route


def _run_worker(work: str, workdir: str, client: Client, skills_dir=None) -> SessionResult:
    """Tier 1: a single bounded worker, no contract (light-touch — a mission is the
    heavier, verified path). Returns the full session so its tokens are counted."""
    order = WorkOrder(order_id=f"t1-{uuid.uuid4().hex[:8]}", task=work, workdir=workdir)
    toolbox = Toolbox(workdir, skills_dir=skills_dir, allowed=list(WORKER.tools))
    return run_session(WORKER.name, WORKER.system_prompt(), order, toolbox,
                       WorkHandoff, client=client, max_turns=WORKER.max_turns,
                       thinking=WORKER.thinking)


def _merge_mission_ledger(ledger: TierLedger, state) -> None:
    """Fold a run mission's per-tier tokens (tier 2 + any tier-3) into the ladder
    ledger. Guarded so a stubbed mission without a ledger is simply skipped."""
    ml = getattr(state, "ledger", None)
    if isinstance(ml, TierLedger):
        ledger.merge(ml)


def _log_route(message: str, decision: str, work: str, result: ConciergeResult,
               store: Path = ROUTING_STORE) -> None:
    """Record the routing decision + its downstream outcome. This is loop-3 data:
    the outcome grades whether the chosen tier was right (an escalation means the
    route was too low). Local-only, like trajectories; scrubbed at export time."""
    store.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "message": message, "decision": decision, "route": result.route,
        "reason": result.reason, "work": work,
        "worker_done": (result.handoff or {}).get("done"),
        "mission_status": (result.mission or {}).get("status"),
    }
    (store / f"{result.route.replace('->', '_')}-{uuid.uuid4().hex[:8]}.jsonl").write_text(
        json.dumps(entry) + "\n")


def handle(message: str, *, workdir: str, client: Client,
           work_client: Client | None = None, skills_dir=None,
           log_routing: bool = True) -> ConciergeResult:
    """Route a user message and dispatch it. `client` runs the concierge (small,
    local); `work_client` runs tier-1/2 work (defaults to `client`)."""
    work_client = work_client or client
    ledger = TierLedger()
    usage: list[dict] = []
    r = decide(message, client, usage)
    for u in usage:  # tier-0: the routing call itself
        ledger.add(Tier.CONCIERGE, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    # Small models reliably pick the route but sometimes leave `work` blank; the
    # raw message is always a safe work order, so fall back to it.
    work = r.work.strip() or message

    def _mission(brief):
        from .mission import Mission
        return Mission(brief, workdir, client=work_client, skills_dir=skills_dir,
                       escalation_client=escalation_client_from_env()).run()

    if r.decision == "chat":
        result = ConciergeResult(route="chat", reason=r.reason, reply=r.reply)
    elif r.decision == "task":
        sr = _run_worker(work, workdir, work_client, skills_dir)
        ledger.add(Tier.TASK, sr.prompt_tokens, sr.completion_tokens)
        wh = WorkHandoff.model_validate(sr.handoff)
        if wh.done and not wh.request_attention:
            result = ConciergeResult(route="task", reason=r.reason, work=work,
                                     handoff=wh.model_dump())
        else:  # tier-1 fell short → escalate to a full mission (tier 2)
            state = _mission(work)
            _merge_mission_ledger(ledger, state)
            result = ConciergeResult(route="task->mission", reason=r.reason, work=work,
                                     handoff=wh.model_dump(), mission=state.model_dump())
    else:  # mission
        state = _mission(work)
        _merge_mission_ledger(ledger, state)
        result = ConciergeResult(route="mission", reason=r.reason, work=work,
                                 mission=state.model_dump())

    result.ledger = ledger.report()

    if log_routing:
        _log_route(message, r.decision, work, result)
    return result
