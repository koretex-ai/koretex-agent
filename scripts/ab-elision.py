#!/usr/bin/env python3
"""Controlled A/B for the efficiency fixes. Both arms run the SAME injected plan
(the orchestrator is skipped so plan stochasticity can't confound the numbers) on
the SAME model; the only difference is whether wire-elision is active.

  arm OFF: _elide_stale_context patched to identity  (pre-change behavior)
  arm ON : current code

Reports per arm: mission status, per-session turns, total tokens, and whether the
deliverable is correct. Also does a deterministic replay of the ON arm's real
trajectory with elision on vs off — the exact, stochastic-free token saving.

  KORETEX_AGENT_BASE_URL=https://dispatcher.koretex.ai/v1 \
  KORETEX_AGENT_MODEL=hf.co/unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M \
  KORETEX_API_KEY=$KEY phase0/.venv/bin/python scripts/ab-elision.py
"""
from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent import mission as M          # noqa: E402
from koretex_agent import session as S          # noqa: E402
from koretex_agent.client import Client          # noqa: E402
from koretex_agent.mission import TaskRecord     # noqa: E402
from koretex_agent.schemas import Assertion      # noqa: E402

import tiktoken                                   # noqa: E402
ENC = tiktoken.get_encoding("cl100k_base")

FIXED_TASK = TaskRecord(
    task_id="T01",
    description=("Implement calc.py: a recursive-descent arithmetic parser (no eval/exec) "
                 "supporting + - * / ** and parentheses, correct precedence, ** right-associative, "
                 "unary minus looser than **, integer output formatting. CLI: python3 calc.py <expr>."),
    assertions=[
        Assertion(item_id="VAL-001", statement="no eval in source", command="! grep -qw 'eval' calc.py"),
        Assertion(item_id="VAL-002", statement="2+3*4=14", command='python3 calc.py "2+3*4" | grep -qx 14'),
        Assertion(item_id="VAL-003", statement="2**3**2=512 (right-assoc)", command='python3 calc.py "2**3**2" | grep -qx 512'),
        Assertion(item_id="VAL-004", statement="-2**2=-4 (unary precedence)", command="python3 calc.py \"-2**2\" | grep -qx -- '-4'"),
        Assertion(item_id="VAL-005", statement="10-2-3=5 (left-assoc)", command='python3 calc.py "10-2-3" | grep -qx 5'),
    ],
)
TRAPS = [("2+3*4", "14"), ("(2+3)*4", "20"), ("2**3**2", "512"), ("-2**2", "-4"), ("10-2-3", "5")]


def deliverable_ok(wd):
    calc = Path(wd) / "calc.py"
    if not calc.exists():
        return False
    import subprocess
    for expr, want in TRAPS:
        r = subprocess.run(["python3", "calc.py", expr], cwd=wd, capture_output=True, text=True)
        if r.stdout.strip() != want:
            return False
    return "eval" not in calc.read_text()


def run_arm(label, elide_on):
    wd = tempfile.mkdtemp(prefix=f"ab-{label}-")
    orig = S._elide_stale_context
    if not elide_on:
        S._elide_stale_context = lambda msgs, keep_last=3: msgs  # identity = pre-change wire
    try:
        m = M.Mission("A/B fixed plan", wd, client=Client(), use_skills=False, synthesize_on_pass=False)
        m.state.tasks = [FIXED_TASK.model_copy(deep=True)]
        m.state.status = "running"
        m._save()
        state = m.run()
    finally:
        S._elide_stale_context = orig
    return wd, state


def sessions_for(mid):
    rows = []
    for f in glob.glob(os.path.expanduser("~/.koretex-agent/trajectories/*.jsonl")):
        prof = oid = None; p = c = turns = 0; msgs = []
        for line in open(f):
            try: e = json.loads(line)
            except: continue
            if e.get("event") == "start": prof = e.get("profile"); oid = e.get("contract", {}).get("order_id", "")
            elif e.get("event") == "usage": u = e.get("usage", e); p += u.get("prompt_tokens", 0); c += u.get("completion_tokens", 0); turns += 1
            elif e.get("event") == "message": msgs.append(e["msg"])
        if oid and oid.startswith(mid):
            rows.append((prof, turns, p, c, f, msgs))
    return rows


def summarize(label, wd, state):
    rows = sessions_for(state.mission_id)
    tot = sum(p + c for _, _, p, c, _, _ in rows)
    print(f"\n── ARM {label} ──  mission {state.mission_id}")
    print(f"  status: {state.status}   deliverable_ok: {deliverable_ok(wd)}")
    print(f"  ledger total: {state.ledger.total():,} tokens   (KPI within: {state.ledger.within_kpi()})")
    for prof, turns, p, c, _, _ in rows:
        print(f"    {prof:10} turns={turns:3d}  prompt={p:8,}  compl={c:7,}")
    return rows


print("A/B: same fixed 1-task plan, elision OFF vs ON, on", Client().cfg.model)
wd_off, st_off = run_arm("OFF", elide_on=False)
r_off = summarize("OFF (no elision)", wd_off, st_off)
wd_on, st_on = run_arm("ON", elide_on=True)
r_on = summarize("ON (elision)", wd_on, st_on)

# deterministic replay: exact elision saving on the ON arm's real trajectory
print("\n── deterministic replay (ON arm trajectory, elision on vs off) ──")
full = elided = 0
for prof, _, _, _, _, msgs in r_on:
    # reconstruct per-turn wire growth: sum of the prefix sent at each assistant turn
    prefix = []
    for msg in msgs:
        prefix.append(msg)
        if msg.get("role") == "assistant":
            full += len(ENC.encode(json.dumps(prefix)))
            elided += len(ENC.encode(json.dumps(S._elide_stale_context(prefix))))
if full:
    print(f"  wire tokens sent across all turns: full {full:,} -> elided {elided:,}  ({100*(full-elided)/full:.0f}% saved)")

o = st_off.ledger.total(); n = st_on.ledger.total()
print(f"\n── headline ──  OFF {o:,} tok  vs  ON {n:,} tok"
      + (f"  ({100*(o-n)/o:+.0f}%)" if o else ""))
print("  (live arms are N=1 and stochastic; the deterministic replay above is the clean elision number.)")
