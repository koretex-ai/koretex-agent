"""Coordinator logic tests with the LLM faked out — control flow must be
fully deterministic, so it must be fully testable without a model."""
import json
from unittest.mock import patch

from koretex_agent import mission as mission_mod
from koretex_agent.cli import _resume_hint
from koretex_agent.mission import Mission, MissionState, TaskRecord
from koretex_agent.schemas import Assertion
from koretex_agent.session import SessionResult


def fake_session(handoff: dict) -> SessionResult:
    return SessionResult(
        handoff=handoff, turns=1, prompt_tokens=10, completion_tokens=5, session_id="s"
    )


def plan_response(client_mock):
    plan = {
        "tasks": [
            {
                "task_id": "T01",
                "description": "build it",
                "assertions": [
                    {"item_id": "VAL-001", "statement": "works", "command": "true"}
                ],
            }
        ]
    }
    client_mock.chat.return_value.message = {"content": json.dumps(plan)}
    client_mock.chat.return_value.usage = {"prompt_tokens": 100, "completion_tokens": 50}


def _handoffs(*sequence):
    """Yield successive handoffs for _run calls in order."""
    it = iter(sequence)

    def side_effect(profile, task, assertions, context, handoff_model, max_turns=None, skills=None):
        return fake_session(next(it))

    return side_effect


PASS_V = {
    "order_id": "o", "overall_passed": True,
    "items": [{"item_id": "VAL-001", "passed": True, "command": "true", "raw_output": "ok"}],
}
FAIL_V = {
    "order_id": "o", "overall_passed": False,
    "items": [{"item_id": "VAL-001", "passed": False, "command": "true", "raw_output": "boom"}],
}
DONE_W = {"order_id": "o", "done": True, "report": "did it"}


def make_mission(tmp_path):
    with patch.object(mission_mod, "Client"):
        # skills off: these tests exercise the coordinator, not the skill loop
        # (which has its own tests and would touch the real catalog / a model)
        m = Mission("brief", str(tmp_path), use_skills=False, synthesize_on_pass=False)
        plan_response(m.client)
        return m


def test_happy_path_clears_and_reviews(tmp_path):
    m = make_mission(tmp_path)
    # worker, validator, scrutiny, terminal review
    with patch.object(m, "_run", side_effect=_handoffs(DONE_W, PASS_V, PASS_V, PASS_V)):
        state = m.run()
    assert state.status == "done"
    assert state.tasks[0].status == "cleared"
    assert state.terminal_review["overall_passed"] is True


def test_failed_validation_retries_with_regression_context(tmp_path):
    m = make_mission(tmp_path)
    seen_contexts = []

    handoffs = iter([DONE_W, FAIL_V, PASS_V,   # attempt 1: live lane fails
                     DONE_W, PASS_V, PASS_V,   # attempt 2: both lanes pass
                     PASS_V])                  # terminal review

    def run_spy(profile, task, assertions, context, handoff_model, max_turns=None, skills=None):
        seen_contexts.append((profile.name, context))
        return fake_session(next(handoffs))

    with patch.object(m, "_run", side_effect=run_spy):
        state = m.run()
    assert state.status == "done"
    assert state.tasks[0].attempts == 2
    retry_worker_ctx = [c for (n, c) in seen_contexts if n == "worker"][1]
    assert "boom" in retry_worker_ctx  # regression evidence fed back verbatim


def test_exhausted_attempts_fail_the_mission(tmp_path):
    m = make_mission(tmp_path)
    with patch.object(m, "_run", side_effect=_handoffs(*([DONE_W, FAIL_V, PASS_V] * 3))):
        state = m.run()
    assert state.status == "failed"
    assert state.tasks[0].attempts == 3


def test_progress_events_emitted(tmp_path):
    m = make_mission(tmp_path)
    events = []
    m.progress = events.append
    with patch.object(m, "_run", side_effect=_handoffs(DONE_W, PASS_V, PASS_V, PASS_V)):
        m.run()
    joined = " · ".join(events)
    assert "planning" in joined
    assert "task 1/1" in joined and "✓" in joined
    assert "final review" in joined and "done" in joined


def test_plan_records_step0_instrumentation(tmp_path):
    m = make_mission(tmp_path)
    m.plan()
    p = m.state.planning
    assert p["initial_model_calls"] == 1        # valid plan first try, no retry
    assert p["initial_tokens"] == 150           # 100 prompt + 50 completion (mock)
    assert isinstance(p["repair_fired"], bool)  # lint decision recorded
    assert "repair_tokens" in p


def test_state_checkpoints_and_resumes(tmp_path):
    m = make_mission(tmp_path)
    with patch.object(m, "_run", side_effect=_handoffs(DONE_W, PASS_V, PASS_V, PASS_V)):
        m.run()
    resumed = MissionState.model_validate_json((tmp_path / ".mission" / "state.json").read_text())
    assert resumed.status == "done"
    assert resumed.tokens["prompt"] > 0


ATTN_W = {"order_id": "o", "done": False, "report": "blocked: pytest missing", "request_attention": True}


def test_attention_triggers_bounded_replan(tmp_path):
    m = make_mission(tmp_path)
    revised_task = {
        "task_id": "T01", "description": "revised approach",
        "assertions": [{"item_id": "VAL-001", "statement": "works", "command": "true"}],
    }

    def chat_side_effect(msgs, **kw):
        class R:
            usage = {"prompt_tokens": 10, "completion_tokens": 5}
            message = {"content": json.dumps(revised_task)}
        return R()

    with patch.object(m, "_run", side_effect=_handoffs(ATTN_W, DONE_W, PASS_V, PASS_V, PASS_V)):
        m.client.chat.side_effect = None
        plan_response(m.client)
        m.plan()
        m.client.chat.side_effect = chat_side_effect
        state = m.run()
    assert state.status == "done"
    assert state.tasks[0].revised is True
    assert state.tasks[0].description == "revised approach"


def test_second_attention_fails_task(tmp_path):
    m = make_mission(tmp_path)
    revised_task = {
        "task_id": "T01", "description": "revised approach",
        "assertions": [{"item_id": "VAL-001", "statement": "works", "command": "true"}],
    }

    def chat_side_effect(msgs, **kw):
        class R:
            usage = {}
            message = {"content": json.dumps(revised_task)}
        return R()

    with patch.object(m, "_run", side_effect=_handoffs(ATTN_W, ATTN_W)):
        m.client.chat.side_effect = None
        plan_response(m.client)
        m.plan()
        m.client.chat.side_effect = chat_side_effect
        state = m.run()
    assert state.status == "failed"
    assert state.tasks[0].status == "failed"


# ── resume: only an interrupted (non-terminal) run, and skip cleared work ────
def _a(i):
    return Assertion(item_id=f"VAL-{i:03d}", statement="x", command="true")


def _write_state(tmp_path, status, tasks, mission_id="m-old"):
    d = tmp_path / ".mission"
    d.mkdir(parents=True, exist_ok=True)
    st = MissionState(mission_id=mission_id, brief="brief",
                      workdir=str(tmp_path.resolve()), status=status, tasks=tasks)
    (d / "state.json").write_text(st.model_dump_json(indent=1))
    return st


def test_resume_skips_already_cleared_tasks(tmp_path):
    t0 = TaskRecord(task_id="T01", description="first thing", assertions=[_a(1)], status="cleared")
    t1 = TaskRecord(task_id="T02", description="second thing", assertions=[_a(2)], status="pending")
    _write_state(tmp_path, "running", [t0, t1])
    with patch.object(mission_mod, "Client"):
        m = Mission("brief", str(tmp_path), use_skills=False, synthesize_on_pass=False)
    assert m.state.mission_id == "m-old"   # adopted the checkpoint, not a fresh mission
    assert m.resumed_from == 1
    # only T02's worker + its two validators + the terminal review should run
    with patch.object(m, "_run", side_effect=_handoffs(DONE_W, PASS_V, PASS_V, PASS_V)) as run_mock:
        state = m.run()
    assert state.status == "done"
    assert run_mock.call_args_list[0].args[1] == "second thing"  # T01 was NOT re-run


def test_terminal_checkpoint_starts_fresh_and_is_archived(tmp_path):
    _write_state(tmp_path, "done", [TaskRecord(task_id="T01", description="a", assertions=[_a(1)],
                                               status="cleared")])
    with patch.object(mission_mod, "Client"):
        m = Mission("brief", str(tmp_path), use_skills=False, synthesize_on_pass=False)
    assert m.state.mission_id != "m-old"        # did NOT replay the finished mission
    assert m.state.status == "planning"
    assert m.resumed_from is None
    assert (tmp_path / ".mission" / "state.prev-m-old.json").exists()  # old one kept aside


def test_corrupt_checkpoint_starts_fresh(tmp_path):
    d = tmp_path / ".mission"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text("{ not valid json")
    with patch.object(mission_mod, "Client"):
        m = Mission("brief", str(tmp_path), use_skills=False, synthesize_on_pass=False)
    assert m.state.status == "planning" and m.resumed_from is None


def test_resume_hint_only_for_interrupted_run(tmp_path):
    # no checkpoint → no hint
    assert _resume_hint(str(tmp_path)) is None
    # interrupted (running) → hint with progress
    _write_state(tmp_path, "running", [TaskRecord(task_id="T01", description="a", assertions=[_a(1)],
                                                  status="cleared"),
                                       TaskRecord(task_id="T02", description="b", assertions=[_a(2)])])
    hint = _resume_hint(str(tmp_path))
    assert hint is not None and "1/2" in hint and "resume" in hint
    # terminal (done) → no hint
    _write_state(tmp_path, "done", [TaskRecord(task_id="T01", description="a", assertions=[_a(1)],
                                               status="cleared")])
    assert _resume_hint(str(tmp_path)) is None
