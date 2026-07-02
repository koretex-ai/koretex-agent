"""Contracts crossing tier boundaries. Every LLM handoff is one of these,
enforced at the serving layer via response_format json_schema."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Assertion(BaseModel):
    """One atomic, executable check in a contract."""
    item_id: str = Field(pattern=r"^VAL-\d{3}$")
    statement: str
    command: str | None = None  # exact command a validator must run, when applicable


class WorkOrder(BaseModel):
    """What a tier hands down: the unit of work."""
    order_id: str
    task: str
    workdir: str
    assertions: list[Assertion] = []
    context: str = ""  # task-relevant memory/skill snippets, injected by the planner
    skills: list[str] = []  # skill names the worker may load via use_skill


class WorkHandoff(BaseModel):
    """Worker's terminal output. `done` means "my executed evidence shows it works",
    not "I tried"."""
    order_id: str
    done: bool
    report: str
    commands_run: list[str] = []
    files_touched: list[str] = []
    request_attention: bool = False


class ValidationItem(BaseModel):
    item_id: str
    passed: bool
    command: str
    raw_output: str  # pasted verbatim — prose summaries get embellished (Phase 0)


class ValidateHandoff(BaseModel):
    """Validator's terminal output: per-assertion verdicts with raw evidence."""
    order_id: str
    items: list[ValidationItem]
    overall_passed: bool
    notes: str = ""


def response_schema(model: type[BaseModel]) -> dict:
    """OpenAI-compatible response_format for grammar-constrained decoding."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": model.model_json_schema(),
            "strict": True,
        },
    }
