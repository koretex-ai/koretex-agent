"""Step 2b: a cut-off validator must not be trusted. Anchored to mission m2,
where a terminal validator that hit its turn cap returned a false FAIL on a
suite that actually passed."""
from koretex_agent.client import ChatResult
from koretex_agent.mission import Mission
from koretex_agent.profiles import VALIDATOR
from koretex_agent.schemas import Assertion, ValidateHandoff, ValidationItem, WorkHandoff, WorkOrder
from koretex_agent.session import SessionResult, run_session
from koretex_agent.tools import Toolbox


def _vh(passed: bool) -> dict:
    item = ValidationItem(item_id="VAL-001", passed=passed, command="c", raw_output="o")
    return ValidateHandoff(order_id="o", items=[item], overall_passed=passed).model_dump()


def _mk_result(hit_cap: bool, passed: bool = False) -> SessionResult:
    return SessionResult(handoff=_vh(passed), turns=1, prompt_tokens=0,
                         completion_tokens=0, session_id="s", hit_turn_cap=hit_cap)


# ── run_session sets the cut-off flag ────────────────────────────────────
def _fake_chat_factory(ever_stops: bool):
    def fake_chat(self, messages, tools=None, response_format=None,
                  temperature=0.2, reasoning_effort=None):
        if response_format is not None:  # terminal handoff
            return ChatResult(message={"role": "assistant",
                                       "content": WorkHandoff(order_id="o", done=True, report="r").model_dump_json()})
        if ever_stops:
            return ChatResult(message={"role": "assistant", "content": "done"})  # no tool_calls → clean stop
        return ChatResult(message={"role": "assistant", "content": "",
                                   "tool_calls": [{"id": "1", "function": {"name": "run_shell", "arguments": "{\"command\":\"echo hi\"}"}}]})
    return fake_chat


def test_hit_turn_cap_true_when_never_stops(monkeypatch, tmp_path):
    from koretex_agent import session as sess
    monkeypatch.setattr(sess.Client, "chat", _fake_chat_factory(ever_stops=False))
    order = WorkOrder(order_id="o", task="t", workdir=str(tmp_path))
    res = run_session("worker", "sys", order, Toolbox(str(tmp_path)), WorkHandoff, max_turns=3)
    assert res.hit_turn_cap is True


def test_hit_turn_cap_false_when_stops_cleanly(monkeypatch, tmp_path):
    from koretex_agent import session as sess
    monkeypatch.setattr(sess.Client, "chat", _fake_chat_factory(ever_stops=True))
    order = WorkOrder(order_id="o", task="t", workdir=str(tmp_path))
    res = run_session("worker", "sys", order, Toolbox(str(tmp_path)), WorkHandoff, max_turns=3)
    assert res.hit_turn_cap is False


# ── _judge re-runs a cut-off lane, and reports still-inconclusive ────────
def _mission(tmp_path):
    return Mission("brief", str(tmp_path), client=object())  # client unused; _run is patched


def test_judge_reruns_once_on_cap_then_trusts_clean(monkeypatch, tmp_path):
    m = _mission(tmp_path)
    seq = [_mk_result(hit_cap=True, passed=False), _mk_result(hit_cap=False, passed=True)]
    calls = []
    monkeypatch.setattr(m, "_run", lambda *a, **k: (calls.append(1), seq[len(calls) - 1])[1])
    v, inconclusive = m._judge(VALIDATOR, "task", [])
    assert len(calls) == 2          # cut-off verdict triggered exactly one re-run
    assert inconclusive is False    # the clean re-run is authoritative
    assert v.overall_passed is True


def test_judge_inconclusive_when_cap_twice(monkeypatch, tmp_path):
    m = _mission(tmp_path)
    calls = []
    monkeypatch.setattr(m, "_run", lambda *a, **k: (calls.append(1), _mk_result(hit_cap=True))[1])
    _, inconclusive = m._judge(VALIDATOR, "task", [])
    assert len(calls) == 2 and inconclusive is True  # only one re-run, still cut off


# ── an inconclusive lane cannot bounce a task on a spurious FAIL ─────────
def test_validate_ignores_inconclusive_lane(monkeypatch, tmp_path):
    m = _mission(tmp_path)
    # both lanes report a FAIL but are inconclusive (the m2 situation)
    monkeypatch.setattr(m, "_judge",
                        lambda lane, task_str, assertions, max_turns=None: (ValidateHandoff.model_validate(_vh(False)), True))
    task = type("T", (), {"description": "d", "task_id": "T01", "assertions": []})()
    ok, regressions = m._validate(task)
    assert ok is True and regressions == []      # spurious FAILs ignored
    assert any("inconclusive" in n for n in m.state.notes)


def test_validate_still_bounces_on_conclusive_fail(monkeypatch, tmp_path):
    m = _mission(tmp_path)
    monkeypatch.setattr(m, "_judge",
                        lambda lane, task_str, assertions, max_turns=None: (ValidateHandoff.model_validate(_vh(False)), False))
    task = type("T", (), {"description": "d", "task_id": "T01", "assertions": []})()
    ok, regressions = m._validate(task)
    assert ok is False and regressions          # a trustworthy FAIL still blocks
