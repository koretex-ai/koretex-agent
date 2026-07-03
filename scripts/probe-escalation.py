#!/usr/bin/env python3
"""Real-model demonstration of tier-3 surgical escalation + the escalation-rate
metric. The trigger (a stuck tier-2 worker) is fault-injected so the run is
deterministic; everything else is a REAL model: the validators that fail on the
missing deliverable, the tier-3 worker that actually produces it, the validators
that then pass, and the terminal review. Proves the escalation path clears a step
end-to-end with a live model and yields a real per-tier token ledger.

  KORETEX_AGENT_BASE_URL=http://localhost:11434/v1 KORETEX_AGENT_MODEL=qwen3:14b \
  phase0/.venv/bin/python scripts/probe-escalation.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent import mission as M  # noqa: E402
from koretex_agent import session as S  # noqa: E402
from koretex_agent.client import Client  # noqa: E402
from koretex_agent.mission import TaskRecord  # noqa: E402
from koretex_agent.schemas import Assertion, WorkHandoff  # noqa: E402

# One tier-2 attempt, snappy validators — the deliverable is trivial to check.
M.MAX_ATTEMPTS_PER_TASK = 1
M.VALIDATOR = dataclasses.replace(M.VALIDATOR, max_turns=6)
M.SCRUTINY = dataclasses.replace(M.SCRUTINY, max_turns=6)
M.TERMINAL_REVIEW_MAX_TURNS = 6

tier2_client = Client()        # validation + (stubbed) tier-2 worker
esc_client = Client()          # tier-3: the real model that clears the step
real_run_session = M.run_session


def wrapped(profile_name, system_prompt, order, toolbox, handoff_model,
            client=None, max_turns=20, thinking=True):
    # Fault-inject ONLY the tier-2 worker: it produces nothing, as if genuinely
    # stuck. Validators and the tier-3 worker run for real.
    if profile_name == "worker" and client is tier2_client:
        stub = WorkHandoff(order_id=order.order_id, done=False,
                           report="tier-2 worker could not complete this step")
        return S.SessionResult(handoff=stub.model_dump(), turns=max_turns,
                               prompt_tokens=40, completion_tokens=10,
                               session_id="stub-tier2", hit_turn_cap=True)
    return real_run_session(profile_name, system_prompt, order, toolbox, handoff_model,
                            client=client, max_turns=max_turns, thinking=thinking)


M.run_session = wrapped

wd = tempfile.mkdtemp()
brief = "Create hello.py that prints exactly 'hello' when run with python3."
m = M.Mission(brief, wd, client=tier2_client, escalation_client=esc_client,
              use_skills=False, synthesize_on_pass=False)
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
print("tasks:", [(t.task_id, t.status) for t in state.tasks])
print("escalations:", json.dumps(state.escalations, indent=2))
print("deliverable exists:", (Path(wd) / "hello.py").exists())
print("\ntier ledger:", json.dumps(state.ledger.report(), indent=2))
print("\nnotes:")
for n in state.notes:
    print("  -", n)

ok = (state.status == "done"
      and len(state.escalations) == 1 and state.escalations[0]["cleared"] is True
      and state.ledger.tokens_at(M.Tier.ESCALATION) > 0)
print("\nPROBE", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
