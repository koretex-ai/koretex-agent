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


_GRAMMAR_UNSUPPORTED = ("pattern", "minItems", "maxItems", "minLength", "maxLength")


def _sanitize(obj):
    """llama.cpp's JSON-schema→grammar conversion fails on regex patterns
    (e.g. `\\d` → 'unknown escape') and length constraints. Strip them from the
    wire schema; pydantic still enforces the full schema client-side."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items() if k not in _GRAMMAR_UNSUPPORTED}
    if isinstance(obj, list):
        return [_sanitize(x) for x in obj]
    return obj


def response_schema(model: type[BaseModel]) -> dict:
    """OpenAI-compatible response_format for grammar-constrained decoding."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": model.__name__,
            "schema": _sanitize(model.model_json_schema()),
            "strict": True,
        },
    }


class PlanTask(BaseModel):
    """One work task in a mission plan. Tasks run in list order (v0: sequential)."""
    task_id: str = Field(pattern=r"^T\d{2}$")
    description: str
    assertions: list[Assertion]


class Plan(BaseModel):
    """Orchestrator output: the whole mission decomposed. Emitted in one
    schema-constrained call — planning is not an agentic session."""
    tasks: list[PlanTask] = Field(min_length=1, max_length=4)
