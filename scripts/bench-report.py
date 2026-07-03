#!/usr/bin/env python3
"""Summarize a finished mission benchmark run. Usage:
    python3 scripts/bench-report.py phase0/bench/<label>

Reads the mission state.json for token totals + terminal review, and the
trajectory files written during the run (matched by mtime window) for per-session
turn counts — flagging any session that hit its profile's max_turns without
stopping. See docs/benchmarks.md."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.profiles import ALL  # noqa: E402

TRAJ_DIR = Path.home() / ".koretex-agent" / "trajectories"


def main(wd: str) -> None:
    wd_p = Path(wd)
    state = json.loads((wd_p / ".mission" / "state.json").read_text())
    target_workdir = str(wd_p.resolve())

    tok = state["tokens"]
    total = tok["prompt"] + tok["completion"]
    print(f"=== {wd} ===")
    print(f"status: {state['status']}  review_passed: {(state.get('terminal_review') or {}).get('overall_passed')}")
    print(f"tokens: prompt={tok['prompt']} completion={tok['completion']} TOTAL={total}")
    print("tasks:", [(t["task_id"], t["status"], f"att{t['attempts']}") for t in state["tasks"]])

    # per-session turn counts — match trajectories by the workdir in their
    # contract (robust; mtime windows misfire when runs overlap or replan).
    print("\nsessions (profile: turns / max, maxed?):")
    sess = []
    for f in sorted(TRAJ_DIR.glob("2026*.jsonl"), key=lambda p: p.stat().st_mtime):
        ev = [json.loads(l) for l in f.open()]
        if not ev or ev[0].get("event") != "start":
            continue
        if (ev[0].get("contract") or {}).get("workdir") != target_workdir:
            continue
        prof = ev[0]["profile"]
        asst = [e for e in ev if e["event"] == "message" and e["msg"].get("role") == "assistant"]
        turns = len(asst)
        stopped = any(not (e["msg"].get("tool_calls")) for e in asst)
        cap = ALL[prof].max_turns if prof in ALL else None
        maxed = cap is not None and turns >= cap and not stopped
        sess.append((prof, turns, cap, maxed))
        flag = "  <-- MAXED OUT" if maxed else ""
        print(f"  {prof:11s} {turns:2d}/{cap}{flag}")
    n_maxed = sum(1 for _, _, _, m in sess if m)
    worker_turns = [t for p, t, _, _ in sess if p == "worker"]
    print(f"\nsessions={len(sess)} maxed_out={n_maxed} worker_turns={worker_turns} total_tokens={total}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "phase0/bench/m1")
