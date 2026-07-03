"""Loop 2 — skill synthesis, catalog, and the win/loss ledger."""
import json

from koretex_agent import skills as sk
from koretex_agent.budget import profile_prefix_tokens
from koretex_agent.client import ChatResult, Client
from koretex_agent.profiles import SKILL_SYNTHESIZER
from koretex_agent.schemas import Skill
from koretex_agent.tools import Toolbox


def _skill(name="csv-to-json-cli"):
    return Skill(name=name, description="when building a CSV->JSON CLI tool",
                 body="1. Use argparse + csv + json.\n2. Handle quoted fields.\n")


def test_render_skill_md_has_frontmatter():
    md = sk.render_skill_md(_skill())
    assert md.startswith("---\nname: csv-to-json-cli\ndescription: when building")
    assert "1. Use argparse" in md and md.endswith("\n")


def test_save_skill_writes_and_registers(tmp_path):
    path = sk.save_skill(_skill(), catalog=tmp_path)
    assert path == tmp_path / "csv-to-json-cli" / "SKILL.md"
    assert path.exists()
    ledger = sk.load_ledger(tmp_path)
    assert ledger["csv-to-json-cli"] == {"uses": 0, "wins": 0, "losses": 0,
                                         "description": "when building a CSV->JSON CLI tool"}


def test_saved_skill_is_loadable_via_use_skill_tool(tmp_path):
    sk.save_skill(_skill(), catalog=tmp_path)
    tb = Toolbox(str(tmp_path), skills_dir=str(tmp_path), allowed=["use_skill"])
    out = tb.call("use_skill", {"name": "csv-to-json-cli"})
    assert "1. Use argparse" in out  # the body is served to the worker


def test_record_outcome_and_win_rate(tmp_path):
    sk.save_skill(_skill(), catalog=tmp_path)
    sk.record_outcome(["csv-to-json-cli"], won=True, catalog=tmp_path)
    sk.record_outcome(["csv-to-json-cli"], won=True, catalog=tmp_path)
    sk.record_outcome(["csv-to-json-cli"], won=False, catalog=tmp_path)
    idx = sk.catalog_index(tmp_path)
    assert idx[0]["name"] == "csv-to-json-cli"
    assert idx[0]["wins"] == 2 and idx[0]["losses"] == 1
    assert abs(idx[0]["win_rate"] - 2 / 3) < 1e-9


def test_catalog_index_ranks_winners_first(tmp_path):
    sk.save_skill(_skill("good-skill"), catalog=tmp_path)
    sk.save_skill(_skill("bad-skill"), catalog=tmp_path)
    sk.record_outcome(["good-skill"], won=True, catalog=tmp_path)
    sk.record_outcome(["bad-skill"], won=False, catalog=tmp_path)
    names = [s["name"] for s in sk.catalog_index(tmp_path)]
    assert names == ["good-skill", "bad-skill"]  # higher win-rate first


def test_synthesize_skill_uses_constrained_call(monkeypatch):
    def fake_chat(self, messages, tools=None, response_format=None,
                  temperature=0.2, reasoning_effort=None):
        assert response_format["json_schema"]["name"] == "Skill"
        return ChatResult(message={"role": "assistant", "content": _skill().model_dump_json()})
    monkeypatch.setattr(sk.Client, "chat", fake_chat)
    out = sk.synthesize_skill("build a csv tool", "wrote cli.py", client=Client())
    assert out.name == "csv-to-json-cli"


def test_synthesize_from_mission(monkeypatch, tmp_path):
    # a done mission + a passing worker trajectory keyed to its id
    mid = "m-abc12345"
    (tmp_path / ".mission").mkdir()
    (tmp_path / ".mission" / "state.json").write_text(json.dumps({
        "mission_id": mid, "status": "done", "brief": "build a csv2json tool",
        "tasks": [{"description": "make cli.py", "status": "cleared"}]}))
    store = tmp_path / "traj"
    store.mkdir()
    (store / "w.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"event": "start", "profile": "worker", "contract": {"order_id": f"{mid}-aaaaaa", "task": "make cli.py"}},
        {"event": "message", "msg": {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "write_file", "arguments": json.dumps({"path": "cli.py", "content": "x"})}}]}},
        {"event": "verdict", "verdict": {"order_id": f"{mid}-aaaaaa", "done": True, "report": "r"}},
    ]))
    monkeypatch.setattr(sk, "synthesize_skill", lambda task, actions, client=None: _skill())
    skill, path = sk.synthesize_from_mission(tmp_path, catalog=tmp_path / "cat", store=store)
    assert skill.name == "csv-to-json-cli" and path.exists()


def test_skill_synthesizer_within_budget():
    assert profile_prefix_tokens(SKILL_SYNTHESIZER) <= SKILL_SYNTHESIZER.prefix_budget_tokens
