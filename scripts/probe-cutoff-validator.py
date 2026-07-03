#!/usr/bin/env python3
"""Fault-injection probe for step 2b's safety net. Forces validator lanes to a
turn cap of 2 so they are guaranteed to get cut off, then runs a tiny mission and
asserts the cure fires: each cut-off lane is re-run, the event is recorded in
state.notes, and the mission does NOT fail spuriously (terminal review inconclusive
→ accept on per-task gates). Model-agnostic — run it against local Ollama.

  KORETEX_AGENT_BASE_URL=http://localhost:11434/v1 KORETEX_AGENT_MODEL=qwen3:14b \
  phase0/.venv/bin/python scripts/probe-cutoff-validator.py
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent import mission as M  # noqa: E402
from koretex_agent.client import Client  # noqa: E402
from koretex_agent.mission import TaskRecord  # noqa: E402
from koretex_agent.schemas import Assertion  # noqa: E402

# Force the validator lanes (and the terminal review) to cut off almost immediately.
M.VALIDATOR = dataclasses.replace(M.VALIDATOR, max_turns=2)
M.SCRUTINY = dataclasses.replace(M.SCRUTINY, max_turns=2)
M.TERMINAL_REVIEW_MAX_TURNS = 2

wd = tempfile.mkdtemp()
brief = "Create hello.py that prints exactly 'hello' when run with python3."
m = M.Mission(brief, wd, client=Client())
# Inject a plan directly — the orchestrator (thinking-on) is slow/fragile on local
# 14b and irrelevant here; we only want to exercise the validator safety net.
m.state.tasks = [TaskRecord(
    task_id="T01",
    description="Create hello.py that prints 'hello' when run with python3.",
    assertions=[Assertion(item_id="VAL-001",
                          statement="python3 hello.py prints hello",
                          command="python3 hello.py | grep -q hello")],
)]
m.state.status = "running"
m._save()
state = m.run()

print("\n=== PROBE RESULT ===")
print("status:", state.status)
print("review_passed:", (state.terminal_review or {}).get("overall_passed"))
print("tasks:", [(t.task_id, t.status) for t in state.tasks])
print("notes:")
for n in state.notes:
    print("  -", n)

inconclusive_events = [n for n in state.notes if "inconclusive" in n]
ok = state.status != "failed" and len(inconclusive_events) > 0
print("\nsafety net fired (inconclusive events recorded):", len(inconclusive_events) > 0)
print("mission did NOT fail spuriously:", state.status != "failed")
print("PROBE", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
