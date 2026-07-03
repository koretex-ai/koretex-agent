"""Tier-3 surgical escalation inside a mission. run_session is faked (not _run),
so the coordinator's tier tagging + token ledger run for real."""
from unittest.mock import patch

from koretex_agent import mission as mission_mod
from koretex_agent.mission import Mission
from koretex_agent.tiers import Tier
from tests.test_mission import DONE_W, FAIL_V, PASS_V, fake_session, plan_response


def _mission(tmp_path, **kw):
    with patch.object(mission_mod, "Client"):
        m = Mission("brief", str(tmp_path), use_skills=False, synthesize_on_pass=False, **kw)
        plan_response(m.client)
    return m


def _seq_run_session(sequence):
    """A fake run_session that yields handoffs in order and records the tier each
    call was charged to (read off the mission via the closure)."""
    it = iter(sequence)

    def fake(profile_name, system_prompt, order, toolbox, handoff_model,
             client=None, max_turns=20, thinking=True):
        return fake_session(next(it))
    return fake


# tier-2 exhausts 3 attempts (worker + two failing lanes each), then tier-3
# clears the gate, then the terminal review passes.
STUCK_THEN_ESCALATE = (
    [DONE_W, FAIL_V, FAIL_V] * 3   # 3 tier-2 attempts, all fail the gate
    + [DONE_W, PASS_V, PASS_V]     # tier-3 attempt clears both lanes
    + [PASS_V]                     # terminal review
)


def test_escalation_clears_a_stuck_step(tmp_path):
    m = _mission(tmp_path, escalation_client=object())  # sentinel enables tier-3
    with patch.object(mission_mod, "run_session", side_effect=_seq_run_session(STUCK_THEN_ESCALATE)):
        state = m.run()
    assert state.status == "done"
    assert state.tasks[0].status == "cleared"
    assert len(state.escalations) == 1
    assert state.escalations[0]["cleared"] is True
    # the escalated work was charged to tier 3; validation stayed at tier 2
    assert state.ledger.tokens_at(Tier.ESCALATION) > 0
    assert state.ledger.tokens_at(Tier.MISSION) > 0


def test_no_escalation_client_fails_as_before(tmp_path):
    m = _mission(tmp_path)  # escalation_client None → tier-3 off
    with patch.object(mission_mod, "run_session",
                      side_effect=_seq_run_session([DONE_W, FAIL_V, FAIL_V] * 3)):
        state = m.run()
    assert state.status == "failed"
    assert state.escalations == []
    assert state.ledger.tokens_at(Tier.ESCALATION) == 0


def test_failed_escalation_still_fails_the_task(tmp_path):
    m = _mission(tmp_path, escalation_client=object())
    seq = ([DONE_W, FAIL_V, FAIL_V] * 3      # tier-2 exhausted
           + [DONE_W, FAIL_V, FAIL_V])       # tier-3 attempt also fails the gate
    with patch.object(mission_mod, "run_session", side_effect=_seq_run_session(seq)):
        state = m.run()
    assert state.status == "failed"
    assert len(state.escalations) == 1
    assert state.escalations[0]["cleared"] is False
    assert state.ledger.tokens_at(Tier.ESCALATION) > 0  # the attempt was still charged


def test_escalation_budget_blocks_when_zero(tmp_path):
    m = _mission(tmp_path, escalation_client=object(), escalation_budget=0)
    with patch.object(mission_mod, "run_session",
                      side_effect=_seq_run_session([DONE_W, FAIL_V, FAIL_V] * 3)):
        state = m.run()
    assert state.status == "failed"
    assert state.escalations == []  # budget 0 → never even attempted
    assert any("budget" in n for n in state.notes)


def test_escalation_recorded_in_kpi_report(tmp_path):
    m = _mission(tmp_path, escalation_client=object())
    with patch.object(mission_mod, "run_session", side_effect=_seq_run_session(STUCK_THEN_ESCALATE)):
        state = m.run()
    report = state.ledger.report()
    assert report["total_tokens"] > 0
    assert "escalation" in report["by_tier"]
    assert 0.0 < report["escalation_rate"] <= 1.0
