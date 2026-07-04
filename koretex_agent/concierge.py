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

from .artifacts import detect_primary_artifact, file_url
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
    tier_models: dict = {}        # tier name → the model that served it (for --verbose)
    workdir: str = ""             # where task/mission output landed
    artifact: str = ""            # absolute path to the browser-openable deliverable, if any


# ── human-facing rendering: talk to it, don't read its JSON ─────────────────
def _fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def render_reply(result: ConciergeResult, color: bool | None = None,
                 verbose: bool = False) -> str:
    """Format a ConciergeResult for a person: chat → just the reply; task/mission
    → a short outcome line + a dim status footer. `verbose` appends an insights
    block (routing rationale, ladder path + escalations, orchestrator thinking,
    per-tier→model token spend). Raw JSON stays available via --json."""
    if color is None:
        color = sys.stdout.isatty()
    dim = "\033[2m" if color else ""
    grn = "\033[32m" if color else ""
    red = "\033[31m" if color else ""
    rst = "\033[0m" if color else ""
    tok = _fmt_tokens((result.ledger or {}).get("total_tokens", 0))

    if result.route == "chat":
        head = result.reply or "(no reply)"
    elif result.route == "task":
        wh = result.handoff or {}
        ok = wh.get("done")
        badge = f"{grn}✓{rst}" if ok else f"{red}✗{rst}"
        parts = [(wh.get("report") or "").strip() or ("done" if ok else "could not complete")]
        if wh.get("files_touched"):
            parts.append(f"{dim}files: {', '.join(wh['files_touched'])}{rst}")
        if result.artifact:
            parts.append(f"{grn}▶ open in your browser:{rst} {file_url(result.artifact)}")
        elif result.workdir:
            parts.append(f"{dim}📁 {result.workdir}{rst}")
        parts.append(f"{dim}{badge} task · {tok} tokens{rst}")
        head = "\n".join(parts)
    else:  # task->mission or mission
        ms = result.mission or {}
        tasks = ms.get("tasks", [])
        cleared = sum(1 for t in tasks if t.get("status") == "cleared")
        done = ms.get("status") == "done"
        badge = f"{grn}✓{rst}" if done else f"{red}✗{rst}"
        parts = []
        if result.route == "task->mission":
            parts.append(f"{dim}(a quick attempt fell short — ran it as a full mission){rst}")
        parts.append(f"{badge} mission {ms.get('status', '?')} — {cleared}/{len(tasks)} tasks cleared")
        if result.artifact:
            parts.append(f"{grn}▶ open in your browser:{rst} {file_url(result.artifact)}")
        elif result.workdir:
            parts.append(f"{dim}📁 {result.workdir}{rst}")
        extra = " · escalated to a stronger model" if ms.get("escalations") else ""
        parts.append(f"{dim}· {tok} tokens{extra}{rst}")
        head = "\n".join(parts)

    if not verbose:
        return head
    return head + "\n\n" + _insights(result, dim, grn, red, rst)


_LADDER = {
    "chat": "concierge (answered on-device)",
    "task": "concierge → task (tier 1)",
    "task->mission": "concierge → task (tier 1) → mission (tier 2)",
    "mission": "concierge → mission (tier 2)",
}


def _insights(result: ConciergeResult, dim: str, grn: str, red: str, rst: str) -> str:
    ms = result.mission or {}
    escs = ms.get("escalations") or []
    L = [f"{dim}── how it was handled ──{rst}"]
    L.append(f"routed: {result.route}" + (f"  {dim}(why: {result.reason}){rst}" if result.reason else ""))
    path = _LADDER.get(result.route, result.route) + (" → escalation (tier 3)" if escs else "")
    L.append(f"ladder: {path}")

    tasks = ms.get("tasks") or []
    if tasks:
        marks = " · ".join(f"{t['task_id']} " + (f"{grn}✓{rst}" if t.get("status") == "cleared" else f"{red}✗{rst}")
                           + f"({t.get('attempts', '?')})" for t in tasks)
        L.append(f"tasks:  {marks}")
    for e in escs:
        L.append(f"  escalated {e.get('task_id')}: {e.get('trigger', '')} → "
                 + ("cleared" if e.get("cleared") else "not cleared"))

    thinking = (ms.get("planning") or {}).get("reasoning")
    if thinking:
        L.append(f"\n{dim}── thinking (orchestrator) ──{rst}")
        L.append(f"{dim}{thinking}{rst}")

    by_tier = (result.ledger or {}).get("by_tier") or {}
    if by_tier:
        L.append(f"\n{dim}── tokens ──{rst}")
        for tier, toks in by_tier.items():
            model = (result.tier_models or {}).get(tier, "")
            label = f"{tier} · {model}" if model else tier
            L.append(f"  {label:<42} {toks:>8,}")
        total = (result.ledger or {}).get("total_tokens", 0)
        kpi = "✓ within KPI" if (result.ledger or {}).get("within_kpi") else "⚠ escalation-heavy"
        L.append(f"  {dim}{'total':<42}{rst} {total:>8,}   {kpi}")
    return "\n".join(L)


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
           log_routing: bool = True, progress=None) -> ConciergeResult:
    """Route a user message and dispatch it. `client` runs the concierge (small,
    local); `work_client` runs tier-1/2 work (defaults to `client`). `progress`
    (optional) is called with short strings at each step for a live view.
    Task/mission work lands in a dedicated subdir of `workdir` (never clobbers
    the parent), returned on the result."""
    work_client = work_client or client
    ledger = TierLedger()
    usage: list[dict] = []
    r = decide(message, client, usage)
    for u in usage:  # tier-0: the routing call itself
        ledger.add(Tier.CONCIERGE, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
    if progress:
        progress(f"routed → {r.decision}")
    # Small models reliably pick the route but sometimes leave `work` blank; the
    # raw message is always a safe work order, so fall back to it.
    work = r.work.strip() or message

    esc_client = escalation_client_from_env()

    # Work runs in the directory the caller chose (their cwd by default, or an
    # explicit --workdir) — no auto-generated hidden folder. Chat produces no
    # files, so it gets no dir at all.
    job_dir = ""
    if r.decision in ("task", "mission"):
        p = Path(workdir).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        job_dir = str(p.resolve())

    def _mission(brief):
        from .mission import Mission
        return Mission(brief, job_dir, client=work_client, skills_dir=skills_dir,
                       escalation_client=esc_client, progress=progress).run()

    def _model_of(c):  # tier → model label for the insights view; tolerant of fakes
        return getattr(getattr(c, "cfg", None), "model", None)
    tier_models = {"concierge": _model_of(client), "task": _model_of(work_client),
                   "mission": _model_of(work_client), "escalation": _model_of(esc_client)}
    tier_models = {k: v for k, v in tier_models.items() if v}

    if r.decision == "chat":
        result = ConciergeResult(route="chat", reason=r.reason, reply=r.reply)
    elif r.decision == "task":
        if progress:
            progress("working…")
        sr = _run_worker(work, job_dir, work_client, skills_dir)
        ledger.add(Tier.TASK, sr.prompt_tokens, sr.completion_tokens)
        wh = WorkHandoff.model_validate(sr.handoff)
        if wh.done and not wh.request_attention:
            result = ConciergeResult(route="task", reason=r.reason, work=work,
                                     handoff=wh.model_dump(), workdir=job_dir)
        else:  # tier-1 fell short → escalate to a full mission (tier 2)
            if progress:
                progress("quick attempt fell short → running a full mission")
            state = _mission(work)
            _merge_mission_ledger(ledger, state)
            result = ConciergeResult(route="task->mission", reason=r.reason, work=work,
                                     handoff=wh.model_dump(), mission=state.model_dump(),
                                     workdir=job_dir)
    else:  # mission
        state = _mission(work)
        _merge_mission_ledger(ledger, state)
        result = ConciergeResult(route="mission", reason=r.reason, work=work,
                                 mission=state.model_dump(), workdir=job_dir)

    result.ledger = ledger.report()
    result.tier_models = tier_models

    # Preview: point the user at the browser-runnable deliverable (the CLI opens
    # it for them on a TTY). Prefer the run's own touched files so a pre-existing
    # .html in the user's cwd is never mistaken for the output; scan otherwise.
    if job_dir and r.decision in ("task", "mission"):
        touched = (result.handoff or {}).get("files_touched")
        art = detect_primary_artifact(job_dir, touched=touched)
        if art:
            result.artifact = str(art)

    if log_routing:
        _log_route(message, r.decision, work, result)
    return result
