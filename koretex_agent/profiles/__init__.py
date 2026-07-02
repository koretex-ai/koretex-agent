"""Profiles: a role = prompt + tool subset + model tier + hard prefix budget.
Budgets are enforced by tests/test_budgets.py — exceeding one fails the build."""
from __future__ import annotations

from dataclasses import dataclass
from importlib import resources


@dataclass(frozen=True)
class Profile:
    name: str
    tools: tuple[str, ...]
    prefix_budget_tokens: int
    max_turns: int

    def system_prompt(self) -> str:
        return (resources.files(__package__) / f"{self.name}.md").read_text()


WORKER = Profile(
    name="worker",
    tools=("run_shell", "read_file", "write_file", "search_files", "use_skill"),
    prefix_budget_tokens=3_000,
    max_turns=20,
)

VALIDATOR = Profile(
    name="validator",
    tools=("run_shell", "read_file", "search_files"),
    prefix_budget_tokens=2_500,
    max_turns=12,
)

SCRUTINY = Profile(
    name="scrutiny",
    tools=("run_shell", "read_file", "search_files"),
    prefix_budget_tokens=2_500,
    max_turns=12,
)

# Planning is one constrained-decoding call, not an agentic session — no tools.
# This is the structural fix for the Phase 0 orchestrator collapse.
ORCHESTRATOR = Profile(
    name="orchestrator",
    tools=(),
    prefix_budget_tokens=5_000,
    max_turns=1,
)

ALL = {p.name: p for p in (WORKER, VALIDATOR, SCRUTINY, ORCHESTRATOR)}
