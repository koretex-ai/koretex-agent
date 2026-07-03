"""Plan-lint rules, anchored to the real defects that motivated them."""
from koretex_agent.plan_lint import lint_assertion, lint_plan
from koretex_agent.profiles import ORCHESTRATOR, WORKER
from koretex_agent.budget import profile_prefix_tokens
from koretex_agent.schemas import Assertion, Plan, PlanTask


def _a(command=None, statement="checks a thing", item_id="VAL-001"):
    return Assertion(item_id=item_id, statement=statement, command=command)


def test_flags_self_passing_command():
    objs = lint_assertion(_a(command="python3 -m pytest tests/ || true"))
    assert any("always pass" in o for o in objs)


def test_flags_existence_only_command():
    objs = lint_assertion(_a(command="test -f cli.py && [ -d tests ]"))
    assert any("only tests that files exist" in o for o in objs)


def test_existence_plus_behavior_is_clean():
    # existence gate followed by a real check must NOT trip the existence rule
    objs = lint_assertion(_a(command="test -f cli.py && python3 cli.py in.csv | grep -q '\"a\"'"))
    assert objs == []


def test_flags_case_sensitive_doc_grep_the_val006_case():
    # the exact assertion from mission run m1 that cost ~26 worker turns
    cmd = "[ -f README.md ] && grep -q 'usage' README.md && grep -q '\\-\\-pretty' README.md"
    objs = lint_assertion(_a(command=cmd))
    assert any("brittle" in o for o in objs)          # case-sensitive doc grep
    assert any("escaping" in o for o in objs)         # the \-\- escaping


def test_case_insensitive_doc_grep_is_clean():
    objs = lint_assertion(_a(command="grep -qi 'usage' README.md"))
    assert objs == []


def test_flags_missing_command():
    objs = lint_assertion(_a(command=None, statement="The CLI prints valid JSON."))
    assert any("no `command`" in o for o in objs)


def test_flags_check_misfiled_in_statement():
    objs = lint_assertion(_a(command=None, statement="python3 cli.py x.csv | grep -q '\"a\"'"))
    assert any("move the runnable command" in o for o in objs)


def test_lint_plan_aggregates_and_clean_plan_is_empty():
    dirty = Plan(tasks=[PlanTask(task_id="T01", description="d", assertions=[
        _a(command="pytest || true", item_id="VAL-001"),
        _a(command="grep -qi 'usage' README.md", item_id="VAL-002"),  # clean
    ])])
    assert len(lint_plan(dirty)) == 1

    clean = Plan(tasks=[PlanTask(task_id="T01", description="d", assertions=[
        _a(command="python3 cli.py in.csv | grep -q '\"name\": \"Alice\"'", item_id="VAL-001"),
    ])])
    assert lint_plan(clean) == []


def test_prompts_stay_within_budget_after_hardening():
    # the tightened orchestrator + worker escape-hatch text must not blow budgets
    assert profile_prefix_tokens(ORCHESTRATOR) <= ORCHESTRATOR.prefix_budget_tokens
    assert profile_prefix_tokens(WORKER) <= WORKER.prefix_budget_tokens
