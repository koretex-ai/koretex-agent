"""Web search behind a pluggable backend seam. The keyless `ddgs` (DuckDuckGo)
backend is the default so a fresh install has working search with no signup;
BYO-key Brave/Tavily and a self-hosted SearXNG are drop-in swaps selected by env.
Every backend returns the same `SearchResult` shape, so the web_search tool and
the eventual deep-research flow are backend-agnostic.

Design notes:
- All backends but ddgs are plain httpx JSON calls — no extra dependencies. ddgs
  is a lazy import (optional `[search]` extra) because DuckDuckGo's anti-bot
  token handling isn't worth re-implementing; absence yields a friendly error.
- Search egress runs wherever the agent's Toolbox runs — the consumer's machine,
  not the network node — which keeps it decentralization-friendly and lets the
  SearXNG endpoint be local/self-hosted (the endgame for this seam)."""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel

SEARCH_TIMEOUT = float(os.environ.get("KORETEX_SEARCH_TIMEOUT", "15"))
FETCH_MAX_CHARS = int(os.environ.get("KORETEX_FETCH_MAX_CHARS", "20000"))


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


@runtime_checkable
class SearchBackend(Protocol):
    name: str

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]: ...


class DdgsBackend:
    """Keyless default (DuckDuckGo via the `ddgs` library, lazy-imported)."""
    name = "ddgs"

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        try:
            from ddgs import DDGS
        except ImportError as e:
            raise RuntimeError(
                "web search needs the 'ddgs' package (keyless DuckDuckGo backend): "
                "pip install 'koretex-agent[search]' — or set a BYO-key backend "
                "(KORETEX_BRAVE_API_KEY / KORETEX_TAVILY_API_KEY) or KORETEX_SEARXNG_URL."
            ) from e
        rows = DDGS(timeout=int(SEARCH_TIMEOUT)).text(query, max_results=max_results)
        return [SearchResult(title=r.get("title", ""), url=r.get("href", ""),
                             snippet=r.get("body", "")) for r in rows][:max_results]


class BraveBackend:
    """BYO-key Brave Search API (KORETEX_BRAVE_API_KEY)."""
    name = "brave"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        r = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        hits = (r.json().get("web") or {}).get("results") or []
        return [SearchResult(title=h.get("title", ""), url=h.get("url", ""),
                             snippet=h.get("description", "")) for h in hits][:max_results]


class TavilyBackend:
    """BYO-key Tavily Search API (KORETEX_TAVILY_API_KEY)."""
    name = "tavily"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        r = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "max_results": max_results},
            timeout=SEARCH_TIMEOUT,
        )
        r.raise_for_status()
        hits = r.json().get("results") or []
        return [SearchResult(title=h.get("title", ""), url=h.get("url", ""),
                             snippet=h.get("content", "")) for h in hits][:max_results]


class SearxngBackend:
    """Self-hosted SearXNG (KORETEX_SEARXNG_URL) — the decentralized endgame:
    no third-party key, ideally run as a Koretex-network service."""
    name = "searxng"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        r = httpx.get(f"{self.base_url}/search",
                      params={"q": query, "format": "json"}, timeout=SEARCH_TIMEOUT)
        r.raise_for_status()
        hits = r.json().get("results") or []
        return [SearchResult(title=h.get("title", ""), url=h.get("url", ""),
                             snippet=h.get("content", "")) for h in hits][:max_results]


# ── web_fetch: pull a page down to readable text, no dependencies ───────────
class _TextExtractor(HTMLParser):
    """Strip HTML to visible text: drop script/style/etc, keep data as lines."""
    _SKIP = {"script", "style", "noscript", "template", "svg", "head"}

    def __init__(self):
        super().__init__()
        self._depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._depth:
            self._depth -= 1

    def handle_data(self, data):
        if not self._depth:
            t = data.strip()
            if t:
                self.parts.append(t)


def html_to_text(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(p.parts)).strip()


def fetch_url(url: str, max_chars: int = FETCH_MAX_CHARS) -> str:
    """GET a web page and return its readable text. http/https only; the body is
    size-capped as it streams so a huge asset can't blow up memory."""
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"only http/https URLs are allowed (got '{scheme or 'none'}')")
    cap_bytes = max(max_chars, 0) * 4  # decoded chars ≈ ¼ the bytes we bother reading
    with httpx.stream("GET", url, timeout=SEARCH_TIMEOUT, follow_redirects=True,
                      headers={"User-Agent": "koretex-agent/1.0 (+research)"}) as r:
        r.raise_for_status()
        ctype = r.headers.get("content-type", "").lower()
        chunks: list[str] = []
        total = 0
        for chunk in r.iter_text():
            chunks.append(chunk)
            total += len(chunk)
            if total >= cap_bytes:
                break
    body = "".join(chunks)
    looks_html = "html" in ctype or body.lstrip()[:1] == "<"
    text = html_to_text(body) if looks_html else body
    return text[:max_chars]


def backend_from_env(env: dict | None = None) -> SearchBackend:
    """Pick a backend: explicit KORETEX_SEARCH_BACKEND wins, else the first
    configured of SearXNG → Brave → Tavily, else the keyless ddgs default."""
    env = os.environ if env is None else env
    choice = (env.get("KORETEX_SEARCH_BACKEND") or "").strip().lower()
    searxng = env.get("KORETEX_SEARXNG_URL")
    brave = env.get("KORETEX_BRAVE_API_KEY")
    tavily = env.get("KORETEX_TAVILY_API_KEY")

    if choice == "brave":
        return BraveBackend(brave or "")
    if choice == "tavily":
        return TavilyBackend(tavily or "")
    if choice == "searxng":
        return SearxngBackend(searxng or "")
    if choice == "ddgs":
        return DdgsBackend()

    # Auto: prefer a configured self-hosted/keyed backend, fall back to keyless.
    if searxng:
        return SearxngBackend(searxng)
    if brave:
        return BraveBackend(brave)
    if tavily:
        return TavilyBackend(tavily)
    return DdgsBackend()
