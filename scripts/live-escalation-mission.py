#!/usr/bin/env python3
"""A LIVE, non-injected full mission that escalates a genuinely irreducible step.

Unlike scripts/probe-escalation.py (which fault-injects a stuck tier-2 worker),
this runs a real Mission.run() end to end: the orchestrator plans, the standard
tier attempts the work for real, its own validators judge it, and only if the
standard tier genuinely can't clear a step does tier-3 (a stronger model) take
that one step. The point is to watch the ladder fire on a real capability gap.

Model ladder via env:
  standard tier (orchestrator + workers + validators): KORETEX_AGENT_{BASE_URL,MODEL}
  premium tier  (tier-3 escalation):                   KORETEX_AGENT_ESCALATION_{BASE_URL,MODEL,API_KEY}

Example (local weak → local strong):
  KORETEX_AGENT_BASE_URL=http://localhost:11434/v1 KORETEX_AGENT_MODEL=qwen3:4b \
  KORETEX_AGENT_ESCALATION_BASE_URL=http://localhost:11434/v1 KORETEX_AGENT_ESCALATION_MODEL=qwen3:14b \
  phase0/.venv/bin/python scripts/live-escalation-mission.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent import mission as M  # noqa: E402
from koretex_agent.client import Client, escalation_client_from_env  # noqa: E402

# Bounded but real: two honest standard-tier attempts is enough to establish the
# step is irreducible for that tier before we spend the premium model.
M.MAX_ATTEMPTS_PER_TASK = int(os.environ.get("KORETEX_MAX_ATTEMPTS", "2"))

BRIEF = (
    "Build a small command-line arithmetic calculator in a single file calc.py.\n"
    "It takes ONE argument: an expression string, and prints ONLY the numeric result.\n"
    "Requirements (match Python's own arithmetic semantics exactly):\n"
    "  - Operators: + - * / and ** (exponentiation), and parentheses.\n"
    "  - + - * / are LEFT-associative; ** is RIGHT-associative.\n"
    "  - Unary minus is supported and binds LOOSER than ** (so -2**2 is -4, not 4).\n"
    "  - Correct precedence: ** highest, then unary minus, then * /, then + -.\n"
    "  - Integer results print as integers (14, not 14.0).\n"
    "  - You MUST NOT use Python's eval() or exec() — write a real recursive-descent parser.\n"
    "These exact cases MUST pass (verify each by actually running calc.py):\n"
    "  python3 calc.py \"2+3*4\"     -> 14\n"
    "  python3 calc.py \"(2+3)*4\"   -> 20\n"
    "  python3 calc.py \"2**3**2\"   -> 512   (right-associative; left gives 64)\n"
    "  python3 calc.py \"-2**2\"     -> -4    (unary minus binds looser than **)\n"
    "  python3 calc.py \"2*-3\"      -> -6\n"
    "  python3 calc.py \"10-2-3\"    -> 5\n"
    "The contract must include an assertion that calc.py does not contain the word eval."
)

esc = escalation_client_from_env()  # tier-3 "Larger"; None until a BYO-key endpoint is wired

wd = tempfile.mkdtemp(prefix="live-esc-")
print(f"workdir: {wd}")
print(f"standard tier: {Client().cfg.base_url}  {Client().cfg.model}")
print(f"premium  tier: {esc.cfg.base_url + '  ' + esc.cfg.model if esc else '(none — tier-3 off, healthy-case run)'}")
print(f"max attempts/task (standard tier): {M.MAX_ATTEMPTS_PER_TASK}\n")

m = M.Mission(BRIEF, wd, client=Client(), escalation_client=esc,
              use_skills=False, synthesize_on_pass=False)
state = m.run()

print("\n=== LIVE MISSION RESULT ===")
print("status:", state.status)
print("tasks:", [(t.task_id, t.status, f"{t.attempts}att") for t in state.tasks])
print("escalations:", json.dumps(state.escalations, indent=2))
print("deliverable calc.py exists:", (Path(wd) / "calc.py").exists())
print("\ntier ledger:", json.dumps(state.ledger.report(), indent=2))
print("\nnotes:")
for n in state.notes:
    print("  -", n)

escalated = len(state.escalations) > 0
cleared_by_t3 = any(e.get("cleared") for e in state.escalations)
print("\n-- verdict --")
print("a step escalated to tier 3:", escalated)
print("tier 3 cleared an escalated step:", cleared_by_t3)
print("mission completed:", state.status == "done")
