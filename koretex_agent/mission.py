"""The mission tier: deterministic coordination around three narrow LLM roles.

Design follows Zenith (Intelligent-Internet/zenith, CC-BY-4.0): sequential task
list with per-task contracts, two independent validator lanes, gates that clear
only on agreement, regression feedback on retry, and a fresh-eyes terminal
review. The coordinator itself never calls a model for control flow — plans,
gates, retries, and stopping are code. State checkpoints to disk after every
step, so a killed mission resumes where it stopped.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from pydantic import BaseModel

from .client import Client
from .profiles import ORCHESTRATOR, SCRUTINY, VALIDATOR, WORKER, Profile
from .schemas import (
    Assertion,
    Plan,
    ValidateHandoff,
    WorkHandoff,
    WorkOrder,
)
from .session import constrained_call, run_session
from .tools import Toolbox

MAX_ATTEMPTS_PER_TASK = 3


class TaskRecord(BaseModel):
    task_id: str
    description: str
    assertions: list[Assertion]
    status: str = "pending"  # pending | cleared | failed
    attempts: int = 0
    revised: bool = False  # one bounded replan per task when a worker raises attention
    regressions: list[str] = []


class MissionState(BaseModel):
    mission_id: str
    brief: str
    workdir: str
    status: str = "planning"  # planning | running | review | done | failed
    tasks: list[TaskRecord] = []
    terminal_review: dict | None = None
    tokens: dict[str, int] = {"prompt": 0, "completion": 0}


class Mission:
    def __init__(self, brief: str, workdir: str, client: Client | None = None,
                 skills_dir: str | None = None):
        self.client = client or Client()
        self.skills_dir = skills_dir
        self.dir = Path(workdir).resolve() / ".mission"
        self.dir.mkdir(parents=True, exist_ok=True)
        existing = self.dir / "state.json"
        if existing.exists():
            self.state = MissionState.model_validate_json(existing.read_text())
        else:
            self.state = MissionState(
                mission_id=f"m-{uuid.uuid4().hex[:8]}", brief=brief, workdir=str(Path(workdir).resolve())
            )
            self._save()

    def _save(self) -> None:
        (self.dir / "state.json").write_text(self.state.model_dump_json(indent=1))

    def _count(self, res) -> None:
        self.state.tokens["prompt"] += res.prompt_tokens
        self.state.tokens["completion"] += res.completion_tokens

    # ── planning: one constrained call, no tools ─────────────────────────
    def plan(self) -> None:
        msgs = [
            {"role": "system", "content": ORCHESTRATOR.system_prompt()},
            {"role": "user", "content": f"Brief:\n{self.state.brief}\n\nEmit the Plan as JSON."},
        ]
        usage_sink: list[dict] = []
        plan, _ = constrained_call(self.client, msgs, Plan, usage_sink)
        for u in usage_sink:
            self.state.tokens["prompt"] += u.get("prompt_tokens", 0)
            self.state.tokens["completion"] += u.get("completion_tokens", 0)
        self.state.tasks = [
            TaskRecord(task_id=t.task_id, description=t.description, assertions=t.assertions)
            for t in plan.tasks
        ]
        self.state.status = "running"
        self._save()

    # ── attention → bounded replan: the planner routes around a blockage ──
    def revise(self, task: TaskRecord, report: str) -> None:
        from .schemas import PlanTask

        msgs = [
            {"role": "system", "content": ORCHESTRATOR.system_prompt()},
            {
                "role": "user",
                "content": (
                    f"Mission brief:\n{self.state.brief}\n\n"
                    f"Task {task.task_id} ('{task.description}') is blocked. "
                    f"Worker report: {report}\n\n"
                    "Emit a revised PlanTask (same task_id) that achieves the same "
                    "goal while routing around the blockage, staying within the brief."
                ),
            },
        ]
        usage_sink: list[dict] = []
        revised, _ = constrained_call(self.client, msgs, PlanTask, usage_sink)
        for u in usage_sink:
            self.state.tokens["prompt"] += u.get("prompt_tokens", 0)
            self.state.tokens["completion"] += u.get("completion_tokens", 0)
        task.description = revised.description
        task.assertions = revised.assertions
        task.revised = True
        self._save()

    # ── one profile session inside this mission's workdir ────────────────
    def _run(self, profile: Profile, task: str, assertions: list[Assertion],
             context: str, handoff_model):
        order = WorkOrder(
            order_id=f"{self.state.mission_id}-{uuid.uuid4().hex[:6]}",
            task=task,
            workdir=self.state.workdir,
            assertions=assertions,
            context=context,
        )
        toolbox = Toolbox(self.state.workdir, skills_dir=self.skills_dir,
                          allowed=list(profile.tools))
        res = run_session(profile.name, profile.system_prompt(), order, toolbox,
                          handoff_model, client=self.client, max_turns=profile.max_turns,
                          thinking=profile.thinking)
        self._count(res)
        self._save()
        return res

    # ── the gate: two independent lanes must both pass ───────────────────
    def _validate(self, task: TaskRecord) -> tuple[bool, list[str]]:
        regressions: list[str] = []
        for lane in (VALIDATOR, SCRUTINY):
            res = self._run(
                lane,
                f"Validate the work for: {task.description}",
                task.assertions,
                context="",
                handoff_model=ValidateHandoff,
            )
            v = ValidateHandoff.model_validate(res.handoff)
            for item in v.items:
                if not item.passed:
                    regressions.append(
                        f"[{lane.name}] {item.item_id} FAILED\ncommand: {item.command}\noutput: {item.raw_output[:1500]}"
                    )
            if not v.overall_passed:
                # first failing lane is enough to bounce the task; the second
                # lane still ran independently on prior attempts' evidence
                pass
        return (len(regressions) == 0, regressions)

    # ── main loop ─────────────────────────────────────────────────────────
    def run(self) -> MissionState:
        if self.state.status == "planning":
            self.plan()

        for task in self.state.tasks:
            if task.status == "cleared":
                continue
            while task.attempts < MAX_ATTEMPTS_PER_TASK:
                task.attempts += 1
                context = f"Mission brief (global constraints apply):\n{self.state.brief}"
                if task.regressions:
                    context += (
                        "\n\nPrevious attempt failed validation. Fix these specific "
                        "failures (evidence below) — do not start over:\n\n"
                        + "\n\n".join(task.regressions[-4:])
                    )
                work = self._run(WORKER, task.description, task.assertions,
                                 context, WorkHandoff)
                wh = WorkHandoff.model_validate(work.handoff)
                if wh.request_attention:
                    task.regressions.append(f"worker requested attention: {wh.report[:500]}")
                    if task.revised:
                        task.status = "failed"
                        break
                    self.revise(task, wh.report[:800])
                    continue
                ok, regressions = self._validate(task)
                if ok:
                    task.status = "cleared"
                    task.regressions = []
                    break
                task.regressions.extend(regressions)
                self._save()
            else:
                task.status = "failed"
            self._save()
            if task.status == "failed":
                self.state.status = "failed"
                self._save()
                return self.state

        # ── terminal review: fresh eyes, the whole workdir vs the brief ───
        self.state.status = "review"
        self._save()
        all_asserts = [a for t in self.state.tasks for a in t.assertions]
        res = self._run(
            VALIDATOR,
            "Terminal review: judge the finished work in this directory against "
            f"the original brief, end to end. Brief: {self.state.brief}",
            all_asserts,
            context="",
            handoff_model=ValidateHandoff,
        )
        review = ValidateHandoff.model_validate(res.handoff)
        self.state.terminal_review = review.model_dump()
        self.state.status = "done" if review.overall_passed else "failed"
        self._save()
        return self.state
