"""Loop 3 — turning the logged (contract, trajectory, verdict) triples into
per-role post-training data. Because tiers only ever communicate through
schemas, the labels come for free:
- **worker** — the gate outcome says whether a trajectory was good.
- **validator** — the gate is ground truth for a verdict's correctness; where
  the two lanes dissent, exactly one is wrong.
- **routing (concierge)** — the downstream outcome grades the tier it chose (a
  route that had to escalate was too low).

`harvest()` returns a dict of named datasets. Everything is deterministic and
runs over the on-disk stores; no model calls. Realizes the repo's `training/`
concept inside the package."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

DEFAULT_STORE = Path.home() / ".koretex-agent" / "trajectories"
ROUTING_STORE = Path.home() / ".koretex-agent" / "routing"


class SessionRecord(BaseModel):
    session_id: str
    profile: str
    task: str
    order_id: str
    mission_id: str | None
    messages: list[dict]
    verdict: dict | None
    label: str  # "pass" | "fail" | "unknown"  (worker self-report / gate)
    prompt_tokens: int = 0
    completion_tokens: int = 0


# ── parsing ──────────────────────────────────────────────────────────────
def _mission_id(order_id: str) -> str | None:
    if order_id.startswith("m-"):
        parts = order_id.split("-")
        if len(parts) >= 3:
            return "-".join(parts[:2])
    return None


def _worker_label(verdict: dict | None) -> str:
    if not verdict:
        return "unknown"
    return "pass" if verdict.get("done") and not verdict.get("request_attention") else "fail"


def _stopped_cleanly(messages: list[dict]) -> bool:
    """A session that ended with a tool call ran out of turns — its verdict is
    unreliable (see step 2b). We only keep cleanly-terminated validator verdicts."""
    asst = [m for m in messages if m.get("role") == "assistant"]
    return bool(asst) and not asst[-1].get("tool_calls")


def _verdict_pass(verdict: dict | None) -> bool:
    return bool(verdict and verdict.get("overall_passed"))


def _parse(path: Path) -> SessionRecord | None:
    events = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                return None
    if not events or events[0].get("event") != "start":
        return None
    start = events[0]
    contract = start.get("contract") or {}
    order_id = contract.get("order_id", "")
    messages = [e["msg"] for e in events if e.get("event") == "message"]
    verdict = next((e["verdict"] for e in reversed(events) if e.get("event") == "verdict"), None)
    ptok = sum(e["usage"].get("prompt_tokens", 0) for e in events if e.get("event") == "usage")
    ctok = sum(e["usage"].get("completion_tokens", 0) for e in events if e.get("event") == "usage")
    profile = start.get("profile", "?")
    label = _worker_label(verdict) if profile == "worker" else "unknown"
    return SessionRecord(
        session_id=path.stem, profile=profile, task=contract.get("task", ""),
        order_id=order_id, mission_id=_mission_id(order_id), messages=messages,
        verdict=verdict, label=label, prompt_tokens=ptok, completion_tokens=ctok,
    )


def load_sessions(store: Path | str = DEFAULT_STORE) -> list[SessionRecord]:
    return [r for f in sorted(Path(store).glob("*.jsonl")) if (r := _parse(f)) is not None]


# ── gate ground truth ────────────────────────────────────────────────────
def apply_gate_labels(sessions: list[SessionRecord],
                      mission_labels: dict[tuple[str, str], bool]) -> None:
    for s in sessions:
        if s.mission_id is None:
            continue
        cleared = mission_labels.get((s.mission_id, s.task))
        if cleared is not None:
            s.label = "pass" if cleared else "fail"


def load_mission_labels(workdirs: list[Path | str]) -> dict[tuple[str, str], bool]:
    labels: dict[tuple[str, str], bool] = {}
    for wd in workdirs:
        state_path = Path(wd) / ".mission" / "state.json"
        if not state_path.exists():
            continue
        state = json.loads(state_path.read_text())
        mid = state.get("mission_id")
        for t in state.get("tasks", []):
            labels[(mid, t["description"])] = (t.get("status") == "cleared")
    return labels


# ── worker datasets ──────────────────────────────────────────────────────
def build_sft(sessions: list[SessionRecord]) -> list[dict]:
    """One chat-format SFT example per passing worker trajectory."""
    return [
        {"profile": "worker", "task": s.task, "session_id": s.session_id,
         "messages": s.messages, "source": "koretex-trajectory"}
        for s in sessions
        if s.profile == "worker" and s.label == "pass" and len(s.messages) >= 2
    ]


def build_dpo(sessions: list[SessionRecord]) -> list[dict]:
    """Pair a failed and a passed worker attempt at the same task."""
    groups: dict[tuple[str | None, str], dict[str, list[SessionRecord]]] = {}
    for s in sessions:
        if s.profile != "worker" or len(s.messages) < 2 or s.label not in ("pass", "fail"):
            continue
        groups.setdefault((s.mission_id, s.task), {"pass": [], "fail": []})[s.label].append(s)
    out = []
    for (mission_id, task), bylabel in groups.items():
        if bylabel["pass"] and bylabel["fail"]:
            chosen, rejected = bylabel["pass"][0], bylabel["fail"][0]
            out.append({"profile": "worker", "task": task, "mission_id": mission_id,
                        "prompt": chosen.messages[:2], "chosen": chosen.messages[2:],
                        "rejected": rejected.messages[2:], "source": "koretex-trajectory"})
    return out


# ── validator datasets ───────────────────────────────────────────────────
# Mission._validate hands a lane its work as "Validate the work for: <task>", so
# the validator session's contract task is prefixed; strip it to recover the plan
# task that the gate labels are keyed on. (Terminal-review sessions use a
# different phrasing and simply won't match — they're a separate judgment.)
_VALIDATE_PREFIX = "Validate the work for: "


def _underlying_task(task: str) -> str:
    return task[len(_VALIDATE_PREFIX):] if task.startswith(_VALIDATE_PREFIX) else task


def build_validator_sft(sessions: list[SessionRecord],
                        task_cleared: dict[tuple[str, str], bool]) -> tuple[list[dict], int]:
    """Positive examples of good validation: a lane's *final* verdict on a task
    (the one that judged the resolved state) that matches the gate ground truth
    and terminated cleanly. Returns (examples, dissent_count) — dissent is where
    the two lanes' final verdicts disagreed (one was provably wrong)."""
    lanes = ("validator", "scrutiny")
    groups: dict[tuple[str, str], dict[str, list[SessionRecord]]] = {}
    for s in sessions:
        if s.profile in lanes and s.verdict is not None and s.mission_id is not None:
            key = (s.mission_id, _underlying_task(s.task))
            if key in task_cleared:
                groups.setdefault(key, {}).setdefault(s.profile, []).append(s)

    sft, dissent = [], 0
    for (mid, task), bylane in groups.items():
        cleared = task_cleared[(mid, task)]
        finals = {lane: max(sess, key=lambda x: x.session_id) for lane, sess in bylane.items()}
        for lane, s in finals.items():
            if _verdict_pass(s.verdict) == cleared and _stopped_cleanly(s.messages) and len(s.messages) >= 2:
                sft.append({"profile": lane, "task": task, "session_id": s.session_id,
                            "messages": s.messages, "verdict_correct": True,
                            "source": "koretex-trajectory"})
        if len(finals) == 2:
            v, sc = finals.get("validator"), finals.get("scrutiny")
            if v and sc and _verdict_pass(v.verdict) != _verdict_pass(sc.verdict):
                dissent += 1
    return sft, dissent


# ── routing datasets ─────────────────────────────────────────────────────
def load_routing(store: Path | str = ROUTING_STORE) -> list[dict]:
    store = Path(store)
    if not store.exists():
        return []
    out = []
    for f in sorted(store.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _route_correct(entry: dict) -> str | None:
    """The decision that turned out right, or None if unverifiable.
    - chat: unverifiable from logs → None (don't train on it).
    - task that stayed at tier 1 and finished → task was right.
    - task that escalated to mission → should have been mission.
    - mission that cleared → mission was right; failed → unverifiable.
    """
    route = entry.get("route")
    if route == "task" and entry.get("worker_done"):
        return "task"
    if route == "task->mission":
        return "mission" if entry.get("mission_status") == "done" else None
    if route == "mission":
        return "mission" if entry.get("mission_status") == "done" else None
    return None


def _route_msgs(system: str, message: str, decision: str, work: str = "") -> list[dict]:
    from .schemas import Route
    target = Route(decision=decision, work=work if decision != "chat" else "",
                   reason="").model_dump_json()
    return [{"role": "system", "content": system}, {"role": "user", "content": message},
            {"role": "assistant", "content": target}]


def build_routing(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """SFT: (message → verified-correct route). DPO: escalation corrections
    (a task that had to become a mission) as clean same-prompt preference pairs."""
    from .profiles import CONCIERGE
    system = CONCIERGE.system_prompt()
    sft, dpo = [], []
    for e in entries:
        correct = _route_correct(e)
        msg = e.get("message", "")
        if correct:
            sft.append({"profile": "concierge", "message": msg,
                        "messages": _route_msgs(system, msg, correct, e.get("work", "")),
                        "source": "koretex-routing"})
        if e.get("route") == "task->mission" and e.get("mission_status") == "done":
            prompt = [{"role": "system", "content": system}, {"role": "user", "content": msg}]
            dpo.append({"profile": "concierge", "message": msg, "prompt": prompt,
                        "chosen": _route_msgs(system, msg, "mission", e.get("work", ""))[2:],
                        "rejected": _route_msgs(system, msg, "task", e.get("work", ""))[2:],
                        "source": "koretex-routing"})
    return sft, dpo


# ── top-level harvest ────────────────────────────────────────────────────
def harvest(store: Path | str = DEFAULT_STORE,
            mission_workdirs: list[Path | str] | None = None,
            routing_store: Path | str = ROUTING_STORE) -> dict:
    sessions = load_sessions(store)
    task_labels = load_mission_labels(mission_workdirs) if mission_workdirs else {}
    if task_labels:
        apply_gate_labels(sessions, task_labels)

    workers = [s for s in sessions if s.profile == "worker"]
    worker_sft, worker_dpo = build_sft(workers), build_dpo(workers)
    val_sft, dissent = build_validator_sft(sessions, task_labels)
    routing_entries = load_routing(routing_store)
    route_sft, route_dpo = build_routing(routing_entries)

    datasets = {
        "worker_sft": worker_sft, "worker_dpo": worker_dpo,
        "validator_sft": val_sft,
        "routing_sft": route_sft, "routing_dpo": route_dpo,
    }
    stats = {
        "sessions": len(sessions),
        "by_profile": {p: sum(1 for s in sessions if s.profile == p)
                       for p in sorted({s.profile for s in sessions})},
        "worker_pass": sum(1 for s in workers if s.label == "pass"),
        "worker_fail": sum(1 for s in workers if s.label == "fail"),
        "validator_dissent_cases": dissent,
        "routing_entries": len(routing_entries),
        "gate_linked": bool(task_labels),
        "counts": {k: len(v) for k, v in datasets.items()},
    }
    return {"datasets": datasets, "stats": stats}


def write_datasets(out_dir: Path | str, harvested: dict) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, examples in harvested["datasets"].items():
        p = out_dir / f"{name}.jsonl"
        p.write_text("".join(json.dumps(ex) + "\n" for ex in examples))
        paths[name] = str(p)
    (out_dir / "stats.json").write_text(json.dumps(harvested["stats"], indent=2))
    return paths
