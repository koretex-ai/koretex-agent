"""Skill auto-wiring: relevant skills are selected into worker orders, the ledger
is scored on mission resolution, and a pass triggers synthesis."""
from koretex_agent import mission as mission_mod
from koretex_agent import skills as sk
from koretex_agent.mission import Mission
from koretex_agent.schemas import Skill, WorkHandoff
from koretex_agent.session import SessionResult


def _seed(catalog, name, description):
    sk.save_skill(Skill(name=name, description=description, body="do the thing"), catalog=catalog)


# ── selection ────────────────────────────────────────────────────────────
def test_select_skills_by_relevance_and_winrate(tmp_path):
    _seed(tmp_path, "csv-json", "when converting CSV files to JSON with a CLI")
    _seed(tmp_path, "web-scrape", "when scraping HTML pages")
    picked = sk.select_skills("build a CSV to JSON converter tool", catalog=tmp_path)
    assert picked == ["csv-json"]  # only the relevant one


def test_select_skills_returns_empty_when_nothing_matches(tmp_path):
    _seed(tmp_path, "web-scrape", "when scraping HTML pages")
    assert sk.select_skills("compute prime numbers", catalog=tmp_path) == []


def test_select_ranks_higher_winrate_first_on_equal_overlap(tmp_path):
    _seed(tmp_path, "json-a", "when producing JSON output")
    _seed(tmp_path, "json-b", "when producing JSON output")
    sk.record_outcome(["json-b"], won=True, catalog=tmp_path)
    picked = sk.select_skills("produce JSON output", catalog=tmp_path, k=2)
    assert picked[0] == "json-b"  # same overlap → win-rate breaks the tie


# ── mission injects selected skills into the worker order ────────────────
def test_mission_injects_skills_into_worker_order(tmp_path, monkeypatch):
    _seed(tmp_path, "csv-json", "when building a CSV to JSON tool")
    m = Mission("brief", str(tmp_path / "wd"), client=object(),
                skills_dir=str(tmp_path), synthesize_on_pass=False)

    captured = {}

    def fake_run_session(name, prompt, order, toolbox, handoff_model, **kw):
        captured["skills"] = order.skills
        return SessionResult(handoff=WorkHandoff(order_id="o", done=True, report="r").model_dump(),
                             turns=1, prompt_tokens=0, completion_tokens=0, session_id="s")

    monkeypatch.setattr(mission_mod, "run_session", fake_run_session)
    sel = m._select_skills("build a CSV to JSON tool")
    m._run(mission_mod.WORKER, "build a CSV to JSON tool", [], "", WorkHandoff, skills=sel)
    assert captured["skills"] == ["csv-json"]  # the worker sees the relevant skill


# ── ledger scoring on resolution ─────────────────────────────────────────
def test_finalize_scores_win_and_synthesizes(tmp_path, monkeypatch):
    _seed(tmp_path, "csv-json", "when building a CSV to JSON tool")
    m = Mission("brief", str(tmp_path / "wd"), client=object(), skills_dir=str(tmp_path))
    m.state.skills_used = ["csv-json"]
    m.state.status = "done"

    synth_called = {}
    monkeypatch.setattr(sk, "synthesize_from_mission",
                        lambda workdir, client=None, catalog=None: synth_called.setdefault("hit", True)
                        or (Skill(name="new-skill", description="d", body="b"), tmp_path / "new-skill" / "SKILL.md"))
    m._finalize_skills()

    ledger = sk.load_ledger(tmp_path)
    assert ledger["csv-json"]["wins"] == 1 and ledger["csv-json"]["losses"] == 0
    assert m.state.skills_scored is True
    assert synth_called.get("hit")  # synthesis fired on the pass


def test_finalize_scores_loss_on_failure(tmp_path, monkeypatch):
    _seed(tmp_path, "csv-json", "when building a CSV to JSON tool")
    m = Mission("brief", str(tmp_path / "wd"), client=object(), skills_dir=str(tmp_path),
                synthesize_on_pass=False)
    m.state.skills_used = ["csv-json"]
    m.state.status = "failed"
    m._finalize_skills()
    ledger = sk.load_ledger(tmp_path)
    assert ledger["csv-json"]["losses"] == 1 and ledger["csv-json"]["wins"] == 0


def test_finalize_is_idempotent_on_resume(tmp_path):
    _seed(tmp_path, "csv-json", "when building a CSV to JSON tool")
    m = Mission("brief", str(tmp_path / "wd"), client=object(), skills_dir=str(tmp_path),
                synthesize_on_pass=False)
    m.state.skills_used = ["csv-json"]
    m.state.status = "done"
    m._finalize_skills()
    m._finalize_skills()  # a resumed run must not double-count
    assert sk.load_ledger(tmp_path)["csv-json"]["wins"] == 1
