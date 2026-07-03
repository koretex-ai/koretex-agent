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
    # Reasoning-mode policy per tier: thinking is planning judgment the
    # orchestrator needs, but dead weight for the mechanical worker/validator
    # roles — the 35B mission burned ~288K tokens largely on Qwen3.6 thinking
    # preambles across ~12 sessions. Off → session.py sends
    # `reasoning_effort:"none"` on every call (see _effort); the `/no_think`
    # text switch does NOT work through llama.cpp/Ollama.
    thinking: bool = True

    def system_prompt(self) -> str:
        return (resources.files(__package__) / f"{self.name}.md").read_text()


WORKER = Profile(
    name="worker",
    tools=("run_shell", "read_file", "write_file", "search_files", "use_skill"),
    prefix_budget_tokens=3_000,
    max_turns=20,
    thinking=False,
)

VALIDATOR = Profile(
    name="validator",
    tools=("run_shell", "read_file", "search_files"),
    prefix_budget_tokens=2_500,
    max_turns=12,
    thinking=False,
)

SCRUTINY = Profile(
    name="scrutiny",
    tools=("run_shell", "read_file", "search_files"),
    prefix_budget_tokens=2_500,
    max_turns=12,
    thinking=False,
)

# Planning is one constrained-decoding call, not an agentic session — no tools.
# This is the structural fix for the Phase 0 orchestrator collapse. Thinking
# stays ON: the plan/replan judgment is exactly what the reasoning block buys.
ORCHESTRATOR = Profile(
    name="orchestrator",
    tools=(),
    prefix_budget_tokens=5_000,
    max_turns=1,
    thinking=True,
)

ALL = {p.name: p for p in (WORKER, VALIDATOR, SCRUTINY, ORCHESTRATOR)}
