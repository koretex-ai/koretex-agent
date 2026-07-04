"""Tier-0 concierge: routing decision + dispatch across the ladder."""
from koretex_agent import concierge as cg
from koretex_agent.budget import profile_prefix_tokens
from koretex_agent.client import ChatResult, Client
from koretex_agent.profiles import CONCIERGE
from koretex_agent.schemas import Route, WorkHandoff
from koretex_agent.session import SessionResult


def _worker_result(**wh):
    """A SessionResult wrapping a WorkHandoff, as _run_worker now returns."""
    return SessionResult(handoff=WorkHandoff(order_id="o", **wh).model_dump(),
                         turns=1, prompt_tokens=10, completion_tokens=5, session_id="s")


class _FakeMission:
    last_args = None

    def __init__(self, brief, workdir, client=None, skills_dir=None, **kwargs):
        _FakeMission.last_args = (brief, workdir)

    def run(self):
        return type("S", (), {"model_dump": lambda self: {"status": "done", "brief_seen": True}})()


def test_decide_parses_route(monkeypatch):
    from koretex_agent import session as sess

    def fake_chat(self, messages, tools=None, response_format=None,
                  temperature=0.2, reasoning_effort=None):
        assert response_format is not None and reasoning_effort == "none"
        return ChatResult(message={"role": "assistant",
                                   "content": Route(decision="mission", work="build a tool", reason="multi-step").model_dump_json()})

    monkeypatch.setattr(sess.Client, "chat", fake_chat)
    r = cg.decide("build me a csv tool", Client())
    assert r.decision == "mission" and r.work == "build a tool"


def test_chat_route_answers_locally(monkeypatch):
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="chat", reply="4", reason="trivial"))
    res = cg.handle("what is 2+2?", workdir="/tmp", client=object(), log_routing=False)
    assert res.route == "chat" and res.reply == "4"
    assert res.handoff is None and res.mission is None


def test_task_route_runs_one_worker(monkeypatch):
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="task", work="make hello.py", reason="one file"))
    monkeypatch.setattr(cg, "_run_worker",
                        lambda work, workdir, client, skills_dir=None: _worker_result(done=True, report="done"))
    res = cg.handle("make hello.py", workdir="/tmp", client=object(), log_routing=False)
    assert res.route == "task" and res.handoff["done"] is True and res.mission is None
    assert res.ledger["by_tier"].get("task", 0) == 15  # tier-1 worker tokens counted


def test_task_escalates_to_mission_when_worker_falls_short(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="task", work="hard thing", reason="looked simple"))
    monkeypatch.setattr(cg, "_run_worker",
                        lambda work, workdir, client, skills_dir=None: _worker_result(done=False, report="stuck", request_attention=True))
    monkeypatch.setattr("koretex_agent.mission.Mission", _FakeMission)
    res = cg.handle("hard thing", workdir=str(tmp_path), client=object(), log_routing=False)
    assert res.route == "task->mission"
    assert res.handoff["done"] is False           # the tier-1 attempt is preserved
    assert res.mission == {"status": "done", "brief_seen": True}
    assert _FakeMission.last_args[0] == "hard thing"  # brief handed down verbatim


def test_mission_route_runs_full_mission(monkeypatch, tmp_path):
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="mission", work="build a tool", reason="multi-step"))
    monkeypatch.setattr("koretex_agent.mission.Mission", _FakeMission)
    res = cg.handle("build a tool", workdir=str(tmp_path), client=object(), log_routing=False)
    assert res.route == "mission" and res.mission["status"] == "done"
    assert res.handoff is None


def test_blank_work_falls_back_to_message(monkeypatch, tmp_path):
    # small models pick the route but sometimes leave `work` empty
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="mission", work="", reason="multi-step"))
    monkeypatch.setattr("koretex_agent.mission.Mission", _FakeMission)
    res = cg.handle("build a csv2json tool with tests", workdir=str(tmp_path), client=object(), log_routing=False)
    assert res.work == "build a csv2json tool with tests"       # fell back to the message
    assert _FakeMission.last_args[0] == "build a csv2json tool with tests"


def test_concierge_within_prefix_budget():
    assert profile_prefix_tokens(CONCIERGE) <= CONCIERGE.prefix_budget_tokens


# ── human-facing rendering ──────────────────────────────────────────────────
def _cr(**kw):
    return cg.ConciergeResult(**kw)


def test_render_chat_is_just_the_reply():
    out = cg.render_reply(_cr(route="chat", reply="hi there", ledger={"total_tokens": 40}), color=False)
    assert out == "hi there"  # no JSON, no footer — just the words


def test_render_task_shows_report_and_status():
    r = _cr(route="task", handoff={"done": True, "report": "wrote calc.py", "files_touched": ["calc.py"]},
            ledger={"total_tokens": 1200})
    out = cg.render_reply(r, color=False)
    assert "wrote calc.py" in out
    assert "files: calc.py" in out
    assert "✓ task · 1.2k tokens" in out


def test_render_mission_summarizes():
    r = _cr(route="mission", mission={"status": "done",
            "tasks": [{"status": "cleared"}, {"status": "cleared"}]}, ledger={"total_tokens": 50000})
    out = cg.render_reply(r, color=False)
    assert "mission done — 2/2 tasks cleared" in out
    assert "50.0k tokens" in out


def test_render_no_color_has_no_ansi():
    out = cg.render_reply(_cr(route="chat", reply="x"), color=False)
    assert "\033[" not in out


def test_verbose_chat_shows_routing_and_ladder():
    r = _cr(route="chat", reply="hi", reason="trivial greeting",
            ledger={"total_tokens": 40, "by_tier": {"concierge": 40}, "within_kpi": True},
            tier_models={"concierge": "qwen3-4b"})
    out = cg.render_reply(r, color=False, verbose=True)
    assert out.startswith("hi")                        # reply first
    assert "how it was handled" in out
    assert "routed: chat" in out and "trivial greeting" in out
    assert "concierge (answered on-device)" in out
    assert "concierge · qwen3-4b" in out and "40" in out  # per-model token line


def test_handle_uses_given_workdir_and_emits_progress(monkeypatch, tmp_path):
    from pathlib import Path
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="task", work="make a thing", reason="one file"))
    seen = {}
    def fake_worker(work, workdir, client, skills_dir=None):
        seen["workdir"] = workdir
        return _worker_result(done=True, report="did it")
    monkeypatch.setattr(cg, "_run_worker", fake_worker)
    events = []
    res = cg.handle("make a thing", workdir=str(tmp_path), client=object(),
                    log_routing=False, progress=events.append)
    assert res.route == "task"
    assert res.workdir == str(Path(str(tmp_path)).resolve())  # the given dir, no auto-subfolder
    assert seen["workdir"] == res.workdir                     # worker ran there
    assert "routed → task" in events and "working…" in events


def test_chat_creates_no_workdir(monkeypatch):
    monkeypatch.setattr(cg, "decide", lambda m, c, u=None: Route(decision="chat", reply="4", reason="q"))
    res = cg.handle("2+2?", workdir="/tmp/should-not-be-used", client=object(), log_routing=False)
    assert res.route == "chat" and res.workdir == ""  # a question makes nothing


def test_render_task_shows_workdir():
    out = cg.render_reply(_cr(route="task", handoff={"done": True, "report": "x"},
                              workdir="/tmp/foo", ledger={"total_tokens": 10}), color=False)
    assert "📁 /tmp/foo" in out


def test_verbose_mission_shows_thinking_escalation_and_models():
    r = _cr(route="mission",
            mission={"status": "done",
                     "tasks": [{"task_id": "T01", "status": "cleared", "attempts": 2}],
                     "escalations": [{"task_id": "T01", "trigger": "tier-2 exhausted", "cleared": True}],
                     "planning": {"reasoning": "break the brief into build + verify"}},
            ledger={"total_tokens": 21000, "by_tier": {"mission": 16000, "escalation": 5000},
                    "within_kpi": False},
            tier_models={"mission": "35B-A3B", "escalation": "70B"})
    out = cg.render_reply(r, color=False, verbose=True)
    assert "escalation (tier 3)" in out                    # ladder shows the climb
    assert "escalated T01: tier-2 exhausted → cleared" in out
    assert "break the brief into build + verify" in out    # the thinking
    assert "mission · 35B-A3B" in out and "escalation · 70B" in out
    assert "escalation-heavy" in out                        # KPI breach flagged


def test_concierge_client_from_env(monkeypatch):
    from koretex_agent.client import concierge_client_from_env
    monkeypatch.delenv("KORETEX_CONCIERGE_MODEL", raising=False)
    assert concierge_client_from_env() is None  # unset → caller reuses the work client
    monkeypatch.setenv("KORETEX_CONCIERGE_MODEL", "qwen3:4b")
    monkeypatch.setenv("KORETEX_CONCIERGE_BASE_URL", "http://localhost:8080/v1")
    c = concierge_client_from_env()
    assert c is not None and c.cfg.model == "qwen3:4b" and "8080" in c.cfg.base_url
