"""Web-friendly artifacts: detecting the browser-runnable deliverable, the
open-line rendering, and the auto-open gate."""
from pathlib import Path

from koretex_agent import concierge as cg
from koretex_agent.artifacts import (
    detect_primary_artifact,
    file_url,
    is_web_artifact,
    should_auto_open,
)
from koretex_agent.concierge import ConciergeResult, render_reply
from koretex_agent.schemas import Route, WorkHandoff
from koretex_agent.session import SessionResult


# ── detection ───────────────────────────────────────────────────────────────
def test_root_index_wins(tmp_path):
    (tmp_path / "other.html").write_text("<html>")
    (tmp_path / "index.html").write_text("<html><script>x</script></html>")
    assert detect_primary_artifact(tmp_path).name == "index.html"


def test_single_html_is_the_artifact(tmp_path):
    (tmp_path / "game.html").write_text("<html>")
    assert detect_primary_artifact(tmp_path).name == "game.html"
    assert is_web_artifact(tmp_path)


def test_no_html_returns_none(tmp_path):
    (tmp_path / "main.py").write_text("print(1)")
    assert detect_primary_artifact(tmp_path) is None
    assert not is_web_artifact(tmp_path)


def test_touched_restricts_to_the_run_output(tmp_path):
    # A pre-existing page in the user's cwd must not be mistaken for the output.
    (tmp_path / "preexisting.html").write_text("<html>")
    (tmp_path / "built.html").write_text("<html>")
    got = detect_primary_artifact(tmp_path, touched=["built.html"])
    assert got.name == "built.html"


def test_touched_accepts_absolute_and_relative(tmp_path):
    sub = tmp_path / "app"
    sub.mkdir()
    page = sub / "index.html"
    page.write_text("<html>")
    # subdir artifact is only reachable via touched (scan is top-level only)
    assert detect_primary_artifact(tmp_path, touched=["app/index.html"]).name == "index.html"
    assert detect_primary_artifact(tmp_path, touched=[str(page)]) == page.resolve()


def test_touched_non_html_falls_back_to_scan(tmp_path):
    (tmp_path / "index.html").write_text("<html>")
    # worker reported only a non-html file — detection should still find the page
    assert detect_primary_artifact(tmp_path, touched=["notes.txt"]).name == "index.html"


def test_scan_is_not_recursive(tmp_path):
    # a page buried in a subdir with no touched hint is intentionally not found
    sub = tmp_path / "vendor"
    sub.mkdir()
    (sub / "index.html").write_text("<html>")
    assert detect_primary_artifact(tmp_path) is None


def test_file_url_is_browser_openable(tmp_path):
    f = tmp_path / "my game.html"
    f.write_text("<html>")
    url = file_url(f)
    assert url.startswith("file://") and "my%20game.html" in url


# ── auto-open gate ──────────────────────────────────────────────────────────
def test_auto_open_only_on_tty():
    assert should_auto_open(True, {}) is True
    assert should_auto_open(False, {}) is False


def test_auto_open_opt_out_env():
    assert should_auto_open(True, {"KORETEX_NO_OPEN": "1"}) is False
    assert should_auto_open(True, {"KORETEX_NO_OPEN": "true"}) is False
    assert should_auto_open(True, {"KORETEX_NO_OPEN": "0"}) is True  # falsey → still opens


# ── rendering ───────────────────────────────────────────────────────────────
def test_render_shows_open_line_for_task():
    r = ConciergeResult(route="task", artifact="/tmp/x/index.html", workdir="/tmp/x",
                        handoff={"done": True, "report": "built it"}, ledger={"total_tokens": 100})
    out = render_reply(r, color=False)
    assert "open in your browser" in out and "file:///" in out


def test_render_shows_open_line_for_mission():
    r = ConciergeResult(route="mission", artifact="/tmp/y/index.html", workdir="/tmp/y",
                        mission={"status": "done", "tasks": [{"task_id": "T01", "status": "cleared"}]},
                        ledger={"total_tokens": 100})
    out = render_reply(r, color=False)
    assert "open in your browser" in out


def test_render_falls_back_to_dir_without_artifact():
    r = ConciergeResult(route="task", workdir="/tmp/z",
                        handoff={"done": True, "report": "wrote parser.py"}, ledger={"total_tokens": 100})
    out = render_reply(r, color=False)
    assert "open in your browser" not in out and "📁 /tmp/z" in out


# ── handle() populates the artifact ─────────────────────────────────────────
def test_handle_sets_artifact_from_task_output(tmp_path, monkeypatch):
    monkeypatch.setattr(cg, "decide",
                        lambda m, c, u=None: Route(decision="task", work="make a page", reason="one file"))

    def fake_worker(work, workdir, client, skills_dir=None):
        Path(workdir, "index.html").write_text("<html><script>1</script></html>")
        return SessionResult(handoff=WorkHandoff(order_id="o", done=True, report="built",
                                                 files_touched=["index.html"]).model_dump(),
                             turns=1, prompt_tokens=10, completion_tokens=5, session_id="s")

    monkeypatch.setattr(cg, "_run_worker", fake_worker)
    res = cg.handle("make a page", workdir=str(tmp_path), client=object(), log_routing=False)
    assert res.artifact == str(tmp_path / "index.html")


def test_handle_chat_has_no_artifact(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html>")  # stray page in cwd
    monkeypatch.setattr(cg, "decide",
                        lambda m, c, u=None: Route(decision="chat", reply="hi", reason="greeting"))
    res = cg.handle("hello", workdir=str(tmp_path), client=object(), log_routing=False)
    assert res.artifact == ""  # chat never opens a file, even if cwd has one
