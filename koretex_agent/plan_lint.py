"""Deterministic plan-lint: reject fragile/vacuous contract assertions in code,
before any worker burns turns on them.

Motivation is empirical, not theoretical. Phase 1 orchestrators emitted
`pytest || true` (always passes) and bare `test -f` (checks nothing). Mission
run `m1` (2026-07-03) emitted
`[ -f README.md ] && grep -q 'usage' README.md && grep -q '\\-\\-pretty' README.md`
— a case-sensitive grep that fails on the "Usage" heading a correct README
uses, with confusing `\\-\\-` escaping. Two workers burned ~26 turns fighting
that unsatisfiable check while the deliverable was already correct.

`lint_plan` returns a list of human-readable objections (one per problem, tagged
with the assertion id). The mission coordinator bounces them back to the
orchestrator for one repair pass; the validators remain the real gate, so a plan
that still lints dirty after the retry proceeds rather than hard-failing."""
from __future__ import annotations

import re

from .schemas import Assertion, Plan

# A command segment that is purely a file-existence test: `test -f X`, `[ -f X ]`,
# `[ -d X ]`, etc. A contract made only of these checks that files exist, not that
# the program behaves.
_EXISTENCE_SEG = re.compile(r"^\s*(?:test\s+-[fdes]\s+\S+|\[\s+-[fdes]\s+\S+\s+\])\s*$")
# Names that denote documentation, where exact prose/case greps are brittle.
_DOC_TARGET = re.compile(r"(?:README|CHANGELOG|\.md\b|(?:^|[\s/])docs/)", re.IGNORECASE)
# A shell-command shape, for spotting a check that was put in `statement` by mistake.
_SHELL_SHAPE = re.compile(r"(?:&&|\|\||[|;]|\$\(|`|^\s*(?:python3?|pytest|grep|test|\[|\./|bash|sh)\b)")


def _segments(cmd: str) -> list[str]:
    """Split a command on shell operators into individual invocations."""
    return [s for s in re.split(r"&&|\|\||[|;]", cmd) if s.strip()]


def _lint_command(item_id: str, cmd: str) -> list[str]:
    out: list[str] = []
    segs = _segments(cmd)

    # 1. Vacuous: swallows its own failure, or checks nothing but existence.
    if re.search(r"\|\|\s*(?:true|:)\b", cmd):
        out.append(f"{item_id}: `|| true`/`|| :` makes the check always pass — remove it and let the command's real exit status stand.")
    if segs and all(_EXISTENCE_SEG.match(s) for s in segs):
        out.append(f"{item_id}: command only tests that files exist ({cmd.strip()!r}), not that the program behaves — add a real behavioral check (run it, grep its output).")

    # 2. Fragile documentation grep: case-sensitive prose match on a doc file.
    for s in segs:
        if "grep" in s and _DOC_TARGET.search(s) and not re.search(r"grep\s+(?:-\w*\s+)*-\w*i", s):
            out.append(f"{item_id}: case-sensitive grep on documentation ({s.strip()!r}) is brittle — headings capitalize words (e.g. 'Usage'). Use `grep -qi`, or assert the file/section exists rather than exact prose.")
            break

    # 3. Confusing escaping: `\-\-flag` instead of `-- '--flag'` / `-F`.
    if re.search(r"\\-\\-", cmd):
        out.append(f"{item_id}: `\\-\\-` escaping is confusing and error-prone — match a literal flag with `grep -- '--flag'` or `grep -F '--flag'`.")

    return out


def lint_assertion(a: Assertion) -> list[str]:
    out: list[str] = []
    if a.command is None:
        # No command at all. If the statement is itself a shell check, it was
        # misfiled; otherwise the orchestrator just failed to give a check.
        if _SHELL_SHAPE.search(a.statement or ""):
            out.append(f"{a.item_id}: the check appears to be in `statement` — move the runnable command into `command` and keep `statement` as prose.")
        else:
            out.append(f"{a.item_id}: no `command` — every assertion needs the exact command a validator runs. Populate `command`.")
        return out
    out.extend(_lint_command(a.item_id, a.command))
    return out


def lint_plan(plan: Plan) -> list[str]:
    """All objections across the plan, empty if the plan is clean."""
    objs: list[str] = []
    for task in plan.tasks:
        for a in task.assertions:
            objs.extend(lint_assertion(a))
    return objs
