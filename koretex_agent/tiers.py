"""The escalation ladder: explicit tiers + the token accounting that measures it.

The whole architecture is a lazy ladder — do the cheapest thing that works,
escalate only what's irreducible. That only *means* something if we can measure
it, so every model call is attributed to the tier that made it and the KPI is a
number: **≥90% of tokens must be served at tier ≤2**. Tier 3 (a stronger, more
expensive model) is reserved for steps the standard tiers genuinely can't clear;
if it's carrying more than ~10% of the tokens, either the work is too hard for
the standard tier or the learning loops aren't pulling their weight yet.

This is deliberately the same yardstick for both cost and learning: as skills
(loop 2) and weights (loop 3) improve, the escalation rate should fall — so the
metric doubles as the scoreboard for whether the flywheel is turning.
"""
from __future__ import annotations

from enum import IntEnum

from pydantic import BaseModel, Field


class Tier(IntEnum):
    CONCIERGE = 0   # on-device routing / chat — the always-resident front door
    TASK = 1        # a single bounded worker
    MISSION = 2     # the full coordinator: orchestrator + workers + validators
    ESCALATION = 3  # surgical 70B+/BYO-key escalation of one irreducible step


# Tokens at or below this tier are "within budget"; above it is escalation.
KPI_BOUNDARY = Tier.MISSION
# The standing KPI: at least this fraction of tokens must land at tier ≤ boundary.
DEFAULT_KPI = 0.90


def _blank() -> dict[str, int]:
    return {"prompt": 0, "completion": 0, "sessions": 0}


class TierLedger(BaseModel):
    """Per-tier token accounting. Keys are the tier's int value as a string (so it
    round-trips through JSON state); values track prompt/completion tokens and the
    number of sessions charged to that tier."""

    by_tier: dict[str, dict[str, int]] = Field(default_factory=dict)

    def add(self, tier: Tier, prompt: int, completion: int, sessions: int = 1) -> None:
        e = self.by_tier.setdefault(str(int(tier)), _blank())
        e["prompt"] += prompt
        e["completion"] += completion
        e["sessions"] += sessions

    def merge(self, other: "TierLedger") -> None:
        for k, e in other.by_tier.items():
            d = self.by_tier.setdefault(k, _blank())
            for f in ("prompt", "completion", "sessions"):
                d[f] = d.get(f, 0) + e.get(f, 0)

    def tokens_at(self, tier: Tier) -> int:
        e = self.by_tier.get(str(int(tier)), {})
        return e.get("prompt", 0) + e.get("completion", 0)

    def total(self) -> int:
        return sum(e.get("prompt", 0) + e.get("completion", 0) for e in self.by_tier.values())

    def at_or_below(self, tier: Tier) -> int:
        return sum(e.get("prompt", 0) + e.get("completion", 0)
                   for k, e in self.by_tier.items() if int(k) <= int(tier))

    def escalated_tokens(self) -> int:
        """Tokens spent above the KPI boundary (tier 3+)."""
        return self.total() - self.at_or_below(KPI_BOUNDARY)

    def escalation_rate(self) -> float:
        """Fraction of tokens spent above tier 2. The number the KPI caps at 0.10."""
        t = self.total()
        return (self.escalated_tokens() / t) if t else 0.0

    def within_tier_fraction(self) -> float:
        """Fraction of tokens at tier ≤2. Empty ledger is vacuously compliant (1.0)."""
        t = self.total()
        return (self.at_or_below(KPI_BOUNDARY) / t) if t else 1.0

    def within_kpi(self, threshold: float = DEFAULT_KPI) -> bool:
        return self.within_tier_fraction() >= threshold

    def report(self, threshold: float = DEFAULT_KPI) -> dict:
        """Human/JSON-facing summary — what the CLI prints and the metric asserts."""
        return {
            "total_tokens": self.total(),
            "by_tier": {Tier(int(k)).name.lower(): (e.get("prompt", 0) + e.get("completion", 0))
                        for k, e in sorted(self.by_tier.items(), key=lambda kv: int(kv[0]))},
            "within_tier_fraction": round(self.within_tier_fraction(), 4),
            "escalation_rate": round(self.escalation_rate(), 4),
            "kpi_threshold": threshold,
            "within_kpi": self.within_kpi(threshold),
        }
