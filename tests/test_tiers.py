"""The tier ledger + escalation-rate KPI."""
from koretex_agent.tiers import DEFAULT_KPI, Tier, TierLedger


def test_empty_ledger_is_vacuously_compliant():
    L = TierLedger()
    assert L.total() == 0
    assert L.within_tier_fraction() == 1.0
    assert L.escalation_rate() == 0.0
    assert L.within_kpi() is True


def test_add_and_totals():
    L = TierLedger()
    L.add(Tier.MISSION, prompt=100, completion=50)
    L.add(Tier.MISSION, prompt=10, completion=5)
    L.add(Tier.ESCALATION, prompt=20, completion=5)
    assert L.tokens_at(Tier.MISSION) == 165
    assert L.tokens_at(Tier.ESCALATION) == 25
    assert L.total() == 190
    assert L.at_or_below(Tier.MISSION) == 165


def test_escalation_rate_and_kpi():
    L = TierLedger()
    L.add(Tier.MISSION, 900, 0)      # tier ≤2
    L.add(Tier.ESCALATION, 100, 0)   # tier 3
    assert L.escalation_rate() == 0.1
    assert L.within_tier_fraction() == 0.9
    assert L.within_kpi(DEFAULT_KPI) is True      # exactly at the 0.90 floor
    # push escalation over 10% → KPI breached
    L.add(Tier.ESCALATION, 50, 0)
    assert L.within_kpi() is False


def test_concierge_and_task_count_as_within_tier():
    L = TierLedger()
    L.add(Tier.CONCIERGE, 30, 10)
    L.add(Tier.TASK, 200, 50)
    assert L.at_or_below(Tier.MISSION) == L.total()
    assert L.within_kpi() is True


def test_merge():
    a = TierLedger(); a.add(Tier.MISSION, 100, 0, sessions=2)
    b = TierLedger(); b.add(Tier.MISSION, 50, 0); b.add(Tier.ESCALATION, 10, 0)
    a.merge(b)
    assert a.tokens_at(Tier.MISSION) == 150
    assert a.tokens_at(Tier.ESCALATION) == 10
    assert a.by_tier[str(int(Tier.MISSION))]["sessions"] == 3


def test_report_shape():
    L = TierLedger()
    L.add(Tier.MISSION, 90, 0)
    L.add(Tier.ESCALATION, 10, 0)
    r = L.report()
    assert r["total_tokens"] == 100
    assert r["by_tier"] == {"mission": 90, "escalation": 10}
    assert r["escalation_rate"] == 0.1
    assert r["within_kpi"] is True


def test_ledger_round_trips_through_json():
    L = TierLedger()
    L.add(Tier.ESCALATION, 5, 5)
    restored = TierLedger.model_validate_json(L.model_dump_json())
    assert restored.tokens_at(Tier.ESCALATION) == 10
