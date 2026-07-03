"""Loop 3 — turning the logged (contract, trajectory, verdict) triples into
per-role post-training data. Because tiers only ever communicate through
schemas, the labels come for free: a worker's handoff and the gate outcome say
whether its trajectory was good.

v0 scope: worker datasets.
- **SFT**: successful worker trajectories (rejection sampling) — the system
  prompt + work order + the executed action sequence that passed.
- **DPO**: a failed and a passed attempt at the *same task* become a
  (rejected, chosen) preference pair.

Labels default to the worker's self-reported handoff (done ∧ ¬attention). Pass
`mission_labels` (built from mission state.json files) to use the authoritative
gate outcome instead — that is the real "rejection-sample gate-passed" signal.

Everything here is deterministic and runs over the trajectory store on disk; no
model calls. It realizes the repo's planned `training/` concept inside the
package."""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

DEFAULT_STORE = Path.home() / ".koretex-agent" / "trajectories"


class SessionRecord(BaseModel):
    session_id: str
    profile: str
    task: str
    order_id: str
    mission_id: str | None
    messages: list[dict]
    verdict: dict | None
    label: str  # "pass" | "fail" | "unknown"
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _mission_id(order_id: str) -> str | None:
    # order_id is "{mission_id}-{hex6}" and mission_id is "m-{hex8}"
    if order_id.startswith("m-"):
        parts = order_id.split("-")
        if len(parts) >= 3:
            return "-".join(parts[:2])
    return None


def _worker_label(verdict: dict | None) -> str:
    if not verdict:
        return "unknown"
    if verdict.get("done") and not verdict.get("request_attention"):
        return "pass"
    return "fail"


def _parse(path: Path) -> SessionRecord | None:
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
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
    store = Path(store)
    out = []
    for f in sorted(store.glob("*.jsonl")):
        rec = _parse(f)
        if rec is not None:
            out.append(rec)
    return out


def apply_gate_labels(sessions: list[SessionRecord],
                      mission_labels: dict[tuple[str, str], bool]) -> None:
    """Override self-reported labels with authoritative gate outcomes, keyed by
    (mission_id, task) → cleared. Sessions without a match keep their label."""
    for s in sessions:
        if s.mission_id is None:
            continue
        cleared = mission_labels.get((s.mission_id, s.task))
        if cleared is not None:
            s.label = "pass" if cleared else "fail"


def load_mission_labels(workdirs: list[Path | str]) -> dict[tuple[str, str], bool]:
    """Build (mission_id, task) → cleared from mission state.json files."""
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


def build_sft(sessions: list[SessionRecord]) -> list[dict]:
    """One chat-format SFT example per passing worker trajectory."""
    out = []
    for s in sessions:
        if s.profile == "worker" and s.label == "pass" and len(s.messages) >= 2:
            out.append({
                "profile": "worker", "task": s.task, "session_id": s.session_id,
                "messages": s.messages, "source": "koretex-trajectory",
            })
    return out


def build_dpo(sessions: list[SessionRecord]) -> list[dict]:
    """Pair a failed and a passed worker attempt at the same task into a
    (rejected, chosen) preference example. The shared prompt is the passing
    session's system + work order."""
    groups: dict[tuple[str | None, str], dict[str, list[SessionRecord]]] = {}
    for s in sessions:
        if s.profile != "worker" or len(s.messages) < 2 or s.label not in ("pass", "fail"):
            continue
        groups.setdefault((s.mission_id, s.task), {"pass": [], "fail": []})[s.label].append(s)

    out = []
    for (mission_id, task), bylabel in groups.items():
        if not bylabel["pass"] or not bylabel["fail"]:
            continue
        chosen, rejected = bylabel["pass"][0], bylabel["fail"][0]
        out.append({
            "profile": "worker", "task": task, "mission_id": mission_id,
            "prompt": chosen.messages[:2],
            "chosen": chosen.messages[2:],
            "rejected": rejected.messages[2:],
            "source": "koretex-trajectory",
        })
    return out


def harvest(store: Path | str = DEFAULT_STORE,
            mission_workdirs: list[Path | str] | None = None) -> dict:
    sessions = load_sessions(store)
    if mission_workdirs:
        apply_gate_labels(sessions, load_mission_labels(mission_workdirs))
    workers = [s for s in sessions if s.profile == "worker"]
    sft = build_sft(workers)
    dpo = build_dpo(workers)
    stats = {
        "sessions": len(sessions),
        "by_profile": {p: sum(1 for s in sessions if s.profile == p)
                       for p in sorted({s.profile for s in sessions})},
        "worker_pass": sum(1 for s in workers if s.label == "pass"),
        "worker_fail": sum(1 for s in workers if s.label == "fail"),
        "sft_examples": len(sft),
        "dpo_pairs": len(dpo),
        "gate_linked": bool(mission_workdirs),
    }
    return {"sft": sft, "dpo": dpo, "stats": stats}


def write_datasets(out_dir: Path | str, harvested: dict) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in ("sft", "dpo"):
        p = out_dir / f"worker_{name}.jsonl"
        p.write_text("".join(json.dumps(ex) + "\n" for ex in harvested[name]))
        paths[name] = str(p)
    (out_dir / "stats.json").write_text(json.dumps(harvested["stats"], indent=2))
    return paths
