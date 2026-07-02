import json

from koretex_agent.schemas import (
    Assertion,
    ValidateHandoff,
    WorkHandoff,
    WorkOrder,
    response_schema,
)
from koretex_agent.session import _render_order, strip_thinking
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
