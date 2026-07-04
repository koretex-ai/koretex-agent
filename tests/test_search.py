"""Web search: backend seam selection, the web_search/web_fetch tools, and the
dependency-free HTML→text fetch. All offline — the ddgs default and the httpx
backends are exercised live in scripts, never in the unit suite."""
import sys

import pytest

from koretex_agent import search as S
from koretex_agent.search import (
    BraveBackend,
    DdgsBackend,
    SearchResult,
    SearxngBackend,
    TavilyBackend,
    backend_from_env,
    fetch_url,
    html_to_text,
)
from koretex_agent.tools import Toolbox


class FakeBackend:
    name = "fake"

    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, max_results=5):
        self.calls.append((query, max_results))
        return self.results[:max_results]


# ── backend selection ───────────────────────────────────────────────────────
def test_default_is_keyless_ddgs():
    assert backend_from_env({}).name == "ddgs"


def test_env_selects_keyed_backends():
    assert backend_from_env({"KORETEX_BRAVE_API_KEY": "k"}).name == "brave"
    assert backend_from_env({"KORETEX_TAVILY_API_KEY": "k"}).name == "tavily"
    assert backend_from_env({"KORETEX_SEARXNG_URL": "http://h:8888"}).name == "searxng"


def test_explicit_override_wins_over_keys():
    assert backend_from_env({"KORETEX_SEARCH_BACKEND": "ddgs",
                             "KORETEX_BRAVE_API_KEY": "k"}).name == "ddgs"


def test_auto_priority_searxng_over_brave_over_tavily():
    env = {"KORETEX_SEARXNG_URL": "u", "KORETEX_BRAVE_API_KEY": "k",
           "KORETEX_TAVILY_API_KEY": "k"}
    assert backend_from_env(env).name == "searxng"
    env.pop("KORETEX_SEARXNG_URL")
    assert backend_from_env(env).name == "brave"


def test_searxng_url_is_normalized():
    assert SearxngBackend("http://h:8888/").base_url == "http://h:8888"


def test_ddgs_missing_lib_gives_friendly_error(monkeypatch):
    # Simulate the optional [search] extra not being installed.
    monkeypatch.setitem(sys.modules, "ddgs", None)
    with pytest.raises(RuntimeError, match="ddgs"):
        DdgsBackend().search("x")


# ── html → text ─────────────────────────────────────────────────────────────
def test_html_to_text_drops_script_style_and_collapses():
    html = ("<html><head><style>b{color:red}</style></head><body>"
            "<h1>Title</h1><script>evil()</script><p>Body text</p></body></html>")
    out = html_to_text(html)
    assert "Title" in out and "Body text" in out
    assert "evil" not in out and "color:red" not in out


# ── fetch_url ───────────────────────────────────────────────────────────────
class _FakeStream:
    def __init__(self, text, ctype="text/html"):
        self._text = text
        self.headers = {"content-type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_text(self):
        yield self._text


def test_fetch_rejects_non_http():
    with pytest.raises(ValueError, match="http"):
        fetch_url("file:///etc/passwd")
    with pytest.raises(ValueError, match="http"):
        fetch_url("ftp://host/x")


def test_fetch_strips_html(monkeypatch):
    monkeypatch.setattr(S.httpx, "stream",
                        lambda *a, **k: _FakeStream("<body><p>Hello world</p></body>"))
    assert fetch_url("https://example.com") == "Hello world"


def test_fetch_truncates_to_max_chars(monkeypatch):
    monkeypatch.setattr(S.httpx, "stream",
                        lambda *a, **k: _FakeStream("x" * 5000, ctype="text/plain"))
    assert len(fetch_url("https://example.com", max_chars=100)) == 100


# ── tool dispatch ───────────────────────────────────────────────────────────
def test_web_search_tool_formats_results():
    fake = FakeBackend([SearchResult(title="A", url="http://a", snippet="sa"),
                        SearchResult(title="B", url="http://b", snippet="sb")])
    tb = Toolbox("/tmp", allowed=["web_search"], search_backend=fake)
    out = tb.call("web_search", {"query": "q", "max_results": 2})
    assert "1. A" in out and "http://a" in out and "sa" in out and "2. B" in out


def test_web_search_clamps_max_results():
    fake = FakeBackend([SearchResult(title=str(i), url="u") for i in range(50)])
    tb = Toolbox("/tmp", allowed=["web_search"], search_backend=fake)
    tb.call("web_search", {"query": "q", "max_results": 99})
    assert fake.calls[-1][1] == 10  # clamped to the 10 ceiling


def test_web_search_no_results():
    tb = Toolbox("/tmp", allowed=["web_search"], search_backend=FakeBackend([]))
    assert tb.call("web_search", {"query": "q"}) == "no results"


def test_web_fetch_tool_scheme_guard():
    tb = Toolbox("/tmp", allowed=["web_fetch"])
    assert "http" in tb.call("web_fetch", {"url": "file:///etc/passwd"})


def test_web_tools_gated_by_allowed():
    tb = Toolbox("/tmp", allowed=["read_file"])
    assert "not available" in tb.call("web_search", {"query": "q"})
