#!/usr/bin/env python3
"""T2 — single-worker probe on the T01 csv2json contract. Measures whether the
worker stops when done vs running to max_turns, and its token cost. Driven via
the API (not the CLI) to avoid --assert pipe-splitting on the assertion strings.

Reads model config from env (KORETEX_AGENT_BASE_URL / KORETEX_AGENT_MODEL /
KORETEX_API_KEY). See docs/benchmarks.md."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from koretex_agent.client import Client, ModelConfig  # noqa: E402
from koretex_agent.profiles import WORKER  # noqa: E402
from koretex_agent.schemas import Assertion, WorkHandoff, WorkOrder  # noqa: E402
from koretex_agent.session import run_session  # noqa: E402
from koretex_agent.tools import Toolbox  # noqa: E402

wd = tempfile.mkdtemp()
order = WorkOrder(
    order_id="probe-t01",
    workdir=wd,
    task=(
        "Implement the core CLI tool in cli.py using only standard library modules "
        "(argparse, csv, json). It must accept a file path argument and an optional "
        "--pretty flag. The tool should read CSV data, convert rows to JSON objects "
        "(using headers as keys), and print the result to stdout."
    ),
    assertions=[
        Assertion(item_id="VAL-001", statement="test -f cli.py && python3 -c 'import py_compile; py_compile.compile(\"cli.py\", doraise=True)'"),
        Assertion(item_id="VAL-002", statement="printf '%s\\n%s\\n' 'name,age' 'Alice,30' > /tmp/t1.csv && python3 cli.py /tmp/t1.csv | grep -q '\"name\": \"Alice\"'"),
        Assertion(item_id="VAL-003", statement="printf '%s\\n%s\\n' 'a,b' '1,2' > /tmp/t2.csv && python3 cli.py --pretty /tmp/t2.csv | grep -q '^    '"),
    ],
)
tb = Toolbox(wd, allowed=list(WORKER.tools))
res = run_session("worker", WORKER.system_prompt(), order, tb, WorkHandoff,
                  client=Client(ModelConfig()), max_turns=WORKER.max_turns,
                  thinking=WORKER.thinking)
print(f"turns: {res.turns} / {WORKER.max_turns}  (baseline maxed at 20)")
print(f"prompt_tokens: {res.prompt_tokens}  completion_tokens: {res.completion_tokens}  "
      f"total: {res.prompt_tokens + res.completion_tokens}")
print(f"done: {res.handoff.get('done')}  request_attention: {res.handoff.get('request_attention')}")
