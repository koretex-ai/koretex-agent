"""Tier 0 — the concierge: the on-device front door that drives the escalation
ladder. A tiny local model answers the cheapest requests itself and routes
everything heavier down the ladder — chat (here) → task (one worker, tier 1) →
mission (full coordinator, tier 2). It never does task/mission work itself; it
decides and restates. A tier-1 worker that falls short escalates to a mission.

The concierge is a consumer-side latency/cost optimization, not a network node:
the routing call runs on the small local model (`client`); the actual work runs
wherever `work_client` points (the Koretex network in deployment)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel

from .client import Client
from .profiles import CONCIERGE, WORKER
from .schemas import Route, WorkHandoff, WorkOrder
from .session import _effort, constrained_call, run_session
from .tools import Toolbox


class ConciergeResult(BaseModel):
    route: str                    # chat | task | task->mission | mission
    reason: str = ""
    reply: str = ""               # chat answer
    work: str = ""                # the instruction/brief handed down
    handoff: dict | None = None   # tier-1 worker handoff, if a task ran
    mission: dict | None = None   # mission state, if a mission ran (routed or escalated)


def decide(message: str, client: Client) -> Route:
    """One constrained call on the small local model — the routing decision."""
    msgs = [
        {"role": "system", "content": CONCIERGE.system_prompt()},
        {"role": "user", "content": message},
    ]
    route, _ = constrained_call(client, msgs, Route, reasoning_effort=_effort(CONCIERGE.thinking))
    return route


def _run_worker(work: str, workdir: str, client: Client, skills_dir=None) -> WorkHandoff:
    """Tier 1: a single bounded worker, no contract (light-touch — a mission is the
    heavier, verified path)."""
    order = WorkOrder(order_id=f"t1-{uuid.uuid4().hex[:8]}", task=work, workdir=workdir)
    toolbox = Toolbox(workdir, skills_dir=skills_dir, allowed=list(WORKER.tools))
    res = run_session(WORKER.name, WORKER.system_prompt(), order, toolbox,
                      WorkHandoff, client=client, max_turns=WORKER.max_turns,
                      thinking=WORKER.thinking)
    return WorkHandoff.model_validate(res.handoff)


def handle(message: str, *, workdir: str, client: Client,
           work_client: Client | None = None, skills_dir=None) -> ConciergeResult:
    """Route a user message and dispatch it. `client` runs the concierge (small,
    local); `work_client` runs tier-1/2 work (defaults to `client`)."""
    work_client = work_client or client
    r = decide(message, client)

    if r.decision == "chat":
        return ConciergeResult(route="chat", reason=r.reason, reply=r.reply)

    # Small models reliably pick the route but sometimes leave `work` blank; the
    # raw message is always a safe work order, so fall back to it.
    work = r.work.strip() or message

    if r.decision == "task":
        wh = _run_worker(work, workdir, work_client, skills_dir)
        if wh.done and not wh.request_attention:
            return ConciergeResult(route="task", reason=r.reason, work=work,
                                   handoff=wh.model_dump())
        # tier-1 fell short → escalate to a full mission (tier 2)
        from .mission import Mission
        state = Mission(work, workdir, client=work_client, skills_dir=skills_dir).run()
        return ConciergeResult(route="task->mission", reason=r.reason, work=work,
                               handoff=wh.model_dump(), mission=state.model_dump())

    # mission
    from .mission import Mission
    state = Mission(work, workdir, client=work_client, skills_dir=skills_dir).run()
    return ConciergeResult(route="mission", reason=r.reason, work=work,
                           mission=state.model_dump())
