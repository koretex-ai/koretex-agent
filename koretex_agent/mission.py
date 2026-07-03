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
from .plan_lint import lint_plan
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
# The terminal review judges the whole workdir against the entire brief — many
# more assertions than a single task's gate — so it gets more room than a
# per-task validator lane (which uses its profile's 12).
TERMINAL_REVIEW_MAX_TURNS = 20


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
    notes: list[str] = []  # reliability events (e.g. inconclusive validator lanes)
    skills_used: list[str] = []  # catalog skills injected into workers this mission
    skills_scored: bool = False  # ledger updated on resolution (guards resume)


class Mission:
    def __init__(self, brief: str, workdir: str, client: Client | None = None,
                 skills_dir: str | None = None, *, use_skills: bool = True,
                 synthesize_on_pass: bool = True, embedder=None):
        self.client = client or Client()
        self.use_skills = use_skills
        self.synthesize_on_pass = synthesize_on_pass
        # Optional semantic skill relevance. None → keyword overlap (offline
        # default). Real runs inject a local embedder via the CLI.
        self.embedder = embedder
        # The skills catalog doubles as the use_skill source. When skills are on
        # and no explicit dir is given, default to the shared catalog so workers
        # can load previously-learned skills.
        if use_skills:
            from .skills import SKILLS_DIR
            self.catalog = Path(skills_dir) if skills_dir else SKILLS_DIR
            self.skills_dir = str(self.catalog)
        else:
            self.catalog = None
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
        plan, res = constrained_call(self.client, msgs, Plan, usage_sink)

        # Deterministic plan-lint: reject fragile/vacuous assertions in code
        # before a worker burns turns on them. One repair pass — the validators
        # are the real gate, so a still-dirty plan proceeds rather than blocking.
        objections = lint_plan(plan)
        if objections:
            msgs += [
                res.message,
                {
                    "role": "user",
                    "content": (
                        "The plan has assertion defects that would make workers fail or "
                        "thrash. Fix every one and re-emit the whole Plan as JSON:\n- "
                        + "\n- ".join(objections)
                    ),
                },
            ]
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
             context: str, handoff_model, max_turns: int | None = None,
             skills: list[str] | None = None):
        order = WorkOrder(
            order_id=f"{self.state.mission_id}-{uuid.uuid4().hex[:6]}",
            task=task,
            workdir=self.state.workdir,
            assertions=assertions,
            context=context,
            skills=skills or [],
        )
        toolbox = Toolbox(self.state.workdir, skills_dir=self.skills_dir,
                          allowed=list(profile.tools))
        res = run_session(profile.name, profile.system_prompt(), order, toolbox,
                          handoff_model, client=self.client,
                          max_turns=max_turns or profile.max_turns,
                          thinking=profile.thinking)
        self._count(res)
        self._save()
        return res

    # ── a validator lane whose verdict we can actually trust ─────────────
    def _judge(self, lane: Profile, task_str: str, assertions: list[Assertion],
               max_turns: int | None = None) -> tuple[ValidateHandoff, bool]:
        """Run a validator lane. A lane that hits its turn cap gets cut off mid-
        check and emits unreliable verdicts (false FAILs — mission m2), so give it
        one clean re-run. Returns (handoff, inconclusive) where inconclusive means
        it hit the cap even on the retry — the caller must not treat its FAILs as
        authoritative."""
        res = self._run(lane, task_str, assertions, "", ValidateHandoff, max_turns=max_turns)
        if res.hit_turn_cap:
            res = self._run(lane, task_str, assertions, "", ValidateHandoff, max_turns=max_turns)
        return ValidateHandoff.model_validate(res.handoff), res.hit_turn_cap

    # ── the gate: two independent lanes must both pass ───────────────────
    def _validate(self, task: TaskRecord) -> tuple[bool, list[str]]:
        regressions: list[str] = []
        for lane in (VALIDATOR, SCRUTINY):
            v, inconclusive = self._judge(
                lane, f"Validate the work for: {task.description}", task.assertions)
            if inconclusive:
                # a cut-off lane can't distinguish a real failure from its own
                # confusion — don't let it bounce the task; the other lane still
                # provides an honest independent check
                self.state.notes.append(
                    f"{task.task_id}: [{lane.name}] inconclusive (hit turn cap twice) — verdict ignored")
                continue
            for item in v.items:
                if not item.passed:
                    regressions.append(
                        f"[{lane.name}] {item.item_id} FAILED\ncommand: {item.command}\noutput: {item.raw_output[:1500]}"
                    )
        return (len(regressions) == 0, regressions)

    # ── skills: select for a worker, score + synthesize on resolution ────
    def _select_skills(self, task_desc: str) -> list[str]:
        if not self.use_skills:
            return []
        from .skills import select_skills
        return select_skills(task_desc, self.catalog, embedder=self.embedder)

    def _finalize_skills(self) -> None:
        """Once the mission resolves: score the ledger for every skill it loaded
        (a cleared mission is a win, a failed one a loss) and — on a pass —
        distil a fresh skill. Guarded so a resumed run doesn't double-count."""
        if not self.use_skills or self.state.skills_scored:
            return
        won = self.state.status == "done"
        if self.state.skills_used:
            from .skills import record_outcome
            record_outcome(list(dict.fromkeys(self.state.skills_used)), won=won,
                           catalog=self.catalog)
        if won and self.synthesize_on_pass:
            try:  # best-effort — never let synthesis fail the mission
                from .skills import synthesize_from_mission
                _, path = synthesize_from_mission(self.state.workdir, client=self.client,
                                                  catalog=self.catalog)
                self.state.notes.append(f"skill synthesized: {path.parent.name}")
            except Exception as e:
                self.state.notes.append(f"skill synthesis skipped: {e}")
        self.state.skills_scored = True
        self._save()

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
                sel = self._select_skills(task.description)
                work = self._run(WORKER, task.description, task.assertions,
                                 context, WorkHandoff, skills=sel)
                for s in sel:
                    if s not in self.state.skills_used:
                        self.state.skills_used.append(s)
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
                self._finalize_skills()
                return self.state

        # ── terminal review: fresh eyes, the whole workdir vs the brief ───
        self.state.status = "review"
        self._save()
        all_asserts = [a for t in self.state.tasks for a in t.assertions]
        review, inconclusive = self._judge(
            VALIDATOR,
            "Terminal review: judge the finished work in this directory against "
            f"the original brief, end to end. Brief: {self.state.brief}",
            all_asserts,
            max_turns=TERMINAL_REVIEW_MAX_TURNS,
        )
        self.state.terminal_review = review.model_dump()
        if inconclusive:
            # The reviewer got cut off even with extra room — its verdict is
            # untrustworthy. Every task already cleared its own two-lane gate, so
            # don't fail the whole mission on a cut-off review; accept with a note.
            self.state.notes.append(
                "terminal review inconclusive (validator hit turn cap on retry); "
                "accepting on per-task gates")
            self.state.status = "done"
        else:
            self.state.status = "done" if review.overall_passed else "failed"
        self._save()
        self._finalize_skills()
        return self.state
