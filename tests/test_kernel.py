import json

from koretex_agent.schemas import (
    Assertion,
    ValidateHandoff,
    WorkHandoff,
    WorkOrder,
    response_schema,
)
from koretex_agent.profiles import ORCHESTRATOR, SCRUTINY, VALIDATOR, WORKER
from koretex_agent.session import _effort, _render_order, strip_thinking
from koretex_agent.tools import Toolbox


def test_toolbox_sandbox_and_syntax_check(tmp_path):
    tb = Toolbox(str(tmp_path))
    assert "escapes workdir" in tb.call("read_file", {"path": "../etc/passwd"})
    out = tb.call("write_file", {"path": "bad.py", "content": "def f(:\n  pass"})
    assert "SYNTAX ERROR" in out
    out = tb.call("write_file", {"path": "ok.py", "content": "x = 1\n"})
    assert "SYNTAX ERROR" not in out
    assert tb.call("read_file", {"path": "ok.py"}) == "x = 1\n"
    assert "exit=0" in tb.call("run_shell", {"command": "echo hi"})


def test_toolbox_respects_allowed_subset(tmp_path):
    tb = Toolbox(str(tmp_path), allowed=["read_file"])
    assert "not available" in tb.call("run_shell", {"command": "echo hi"})
    assert len(tb.schemas()) == 1


def test_schemas_roundtrip_and_response_format():
    order = WorkOrder(
        order_id="o1",
        task="t",
        workdir="/tmp",
        assertions=[Assertion(item_id="VAL-001", statement="s", command="true")],
    )
    assert "VAL-001" in _render_order(order)
    rf = response_schema(WorkHandoff)
    assert rf["json_schema"]["name"] == "WorkHandoff"
    v = ValidateHandoff.model_validate_json(
        json.dumps(
            {
                "order_id": "o1",
                "items": [
                    {"item_id": "VAL-001", "passed": False, "command": "true", "raw_output": "x"}
                ],
                "overall_passed": False,
            }
        )
    )
    assert v.overall_passed is False


def test_strip_thinking_drops_reasoning():
    msg = {"role": "assistant", "content": "hi", "reasoning": "...", "thinking": "..."}
    assert set(strip_thinking(msg)) == {"role", "content"}


def test_thinking_policy_per_tier():
    # Mechanical roles run without reasoning; only the planner thinks.
    assert (WORKER.thinking, VALIDATOR.thinking, SCRUTINY.thinking) == (False, False, False)
    assert ORCHESTRATOR.thinking is True


def test_effort_maps_thinking_to_reasoning_effort():
    # Thinking on → unset (model default). Off → "none", the switch empirically
    # honored by llama.cpp/Ollama (the /no_think text switch is not).
    assert _effort(True) is None
    assert _effort(False) == "none"


def test_run_session_propagates_effort_to_every_call(monkeypatch, tmp_path):
    # Worker (thinking off) → every chat, tool loop and terminal handoff, carries
    # reasoning_effort="none". Guards against reasoning leaking back in mid-session.
    from koretex_agent import session as sess
    from koretex_agent.client import ChatResult
    from koretex_agent.schemas import WorkHandoff, WorkOrder

    seen = []

    def fake_chat(self, messages, tools=None, response_format=None,
                  temperature=0.2, reasoning_effort=None):
        seen.append(reasoning_effort)
        if response_format is not None:  # terminal handoff
            return ChatResult(
                message={"role": "assistant", "content": WorkHandoff(
                    order_id="o1", done=True, report="ok",
                ).model_dump_json()},
                usage={},
            )
        return ChatResult(message={"role": "assistant", "content": "finished"}, usage={})

    monkeypatch.setattr(sess.Client, "chat", fake_chat)
    order = WorkOrder(order_id="o1", task="noop", workdir=str(tmp_path), assertions=[])
    sess.run_session("worker", "sys", order, Toolbox(str(tmp_path)),
                     WorkHandoff, max_turns=3, thinking=False)
    assert seen and all(e == "none" for e in seen)
