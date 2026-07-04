"""The kernel's single model client: OpenAI-compatible chat completions.

One provider, by design. Endpoint and tier come from config; the same client
serves local Ollama during development and the Koretex dispatcher in product.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx


@dataclass
class ModelConfig:
    base_url: str = os.environ.get("KORETEX_AGENT_BASE_URL", "http://localhost:11434/v1")
    model: str = os.environ.get("KORETEX_AGENT_MODEL", "qwen3:14b-16k")
    api_key: str = os.environ.get("KORETEX_API_KEY", "local")
    # Split timeouts: fail *fast* if a node won't accept the connection (dead),
    # but allow a bounded wait for a slow generation. A single 300s timeout meant
    # a hung node blocked for minutes per attempt.
    connect_timeout: float = float(os.environ.get("KORETEX_CONNECT_TIMEOUT", "10"))
    read_timeout: float = float(os.environ.get("KORETEX_READ_TIMEOUT", "120"))
    # Cap total wall-clock spent *retrying* a single call, so a flaky node can't
    # stack read-timeouts into a multi-minute hang before we give up gracefully.
    total_retry_seconds: float = float(os.environ.get("KORETEX_RETRY_BUDGET", "150"))
    # How many attempts before giving up (within the wall-clock budget above).
    # Raise it to ride out a persistently flaky node — drops retry cheaply.
    # default_factory so the env var is read at instantiation, not import time.
    max_retries: int = field(default_factory=lambda: int(os.environ.get("KORETEX_MAX_RETRIES", "6")))
    # Embeddings power tier-0 skill relevance. They run *locally* by design —
    # matching a task to a skill is a routing decision that shouldn't cost a
    # network round-trip to the work tier — so the embed endpoint defaults to
    # local Ollama even when base_url points at the dispatcher.
    embed_base_url: str = os.environ.get("KORETEX_AGENT_EMBED_BASE_URL", "http://localhost:11434/v1")
    embed_model: str = os.environ.get("KORETEX_AGENT_EMBED_MODEL", "nomic-embed-text")


class NetworkError(RuntimeError):
    """A model call failed after exhausting retries. `kind` classifies it and
    `friendly` is a message safe to show a user (no stack trace / URL)."""

    def __init__(self, kind: str, friendly: str, cause: Exception | None = None):
        super().__init__(friendly)
        self.kind = kind          # down | slow | dropped | busy | auth | request | unknown
        self.friendly = friendly
        self.cause = cause


def _backoff(attempt: int) -> float:
    # Cap at 8s (not 20s): most transient failures are fast connection drops that
    # are cheap to retry, so a lower cap lets more attempts fit the wall-clock
    # retry budget and ride out a brief flaky window instead of giving up.
    base = float(min(2 ** attempt, 8))
    return base + random.uniform(0, base * 0.25)  # jitter avoids thundering herd


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return min(float(resp.headers.get("retry-after", "")), 30.0)
    except ValueError:
        return None


def _classify(exc: Exception | None) -> NetworkError:
    # Order matters: the connect group before the generic timeouts, and the
    # protocol/read-drop group before the fallthrough. These map to *honest*,
    # actionable messages — a dropped connection is not the same as a slow node,
    # and the old code conflated both as "busy or slow", which misled diagnosis.
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)):
        return NetworkError("down", "Can't reach the Koretex network right now — check your "
                            "connection, or try again shortly.", exc)
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout)):
        return NetworkError("slow", "The node is taking too long to respond — it may be under "
                            "heavy load. Try again, or raise KORETEX_READ_TIMEOUT for a large job.", exc)
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.ProtocolError)):
        return NetworkError("dropped", "The node dropped the connection mid-response — this is "
                            "usually transient. Give it a moment and try again.", exc)
    if isinstance(exc, httpx.HTTPStatusError):
        return NetworkError("busy", f"The network had a server error (HTTP "
                            f"{exc.response.status_code}). Try again shortly.", exc)
    return NetworkError("unknown", f"The network request failed ({type(exc).__name__}). "
                        "Try again shortly.", exc)


@dataclass
class ChatResult:
    message: dict[str, Any]
    usage: dict[str, int] = field(default_factory=dict)


class Client:
    def __init__(self, cfg: ModelConfig | None = None):
        self.cfg = cfg or ModelConfig()
        self._http = httpx.Client(timeout=httpx.Timeout(
            connect=self.cfg.connect_timeout, read=self.cfg.read_timeout,
            write=self.cfg.read_timeout, pool=self.cfg.connect_timeout))

    def _request(self, url: str, body: dict, parse: Callable[[dict], Any]) -> Any:
        """POST with classified, time-budgeted retries. Retries transient failures
        (5xx, 429, timeouts, disconnects, malformed bodies) with jittered backoff;
        does NOT retry auth/4xx. Raises NetworkError with a user-safe message when
        the retry budget or count is exhausted."""
        start = time.monotonic()
        last: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            try:
                r = self._http.post(url, headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                                    json=body)
                r.raise_for_status()
                return parse(r.json())
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                if code in (401, 403):
                    raise NetworkError("auth", "The Koretex network rejected your API key — "
                                       "check KORETEX_API_KEY.", e) from e
                if code < 500 and code != 429:
                    raise NetworkError("request", f"The network rejected the request "
                                       f"(HTTP {code}).", e) from e
                last = e
                wait = _retry_after(e.response) or _backoff(attempt)
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, IndexError) as e:
                last = e
                wait = _backoff(attempt)
            if attempt == self.cfg.max_retries - 1 or \
               (time.monotonic() - start) + wait > self.cfg.total_retry_seconds:
                break
            time.sleep(wait)
        raise _classify(last)

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        # OpenAI-standard reasoning control. "none" is honored by both the
        # dispatcher's llama.cpp server and local Ollama (empirically: 35B
        # 307→14 and qwen3:14b 380→14 completion tokens, <think> block gone).
        # The Qwen `/no_think` text switch does NOT work through either serving
        # path — llama.cpp treats it as literal prompt text and reasons *more*.
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort

        return self._request(
            f"{self.cfg.base_url}/chat/completions", body,
            lambda d: ChatResult(message=d["choices"][0]["message"], usage=d.get("usage", {})))

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Batch-embed strings via the OpenAI-compatible /embeddings endpoint
        (local Ollama by default — see ModelConfig.embed_base_url). Returns one
        vector per input, in order. Raises on failure so callers can fall back."""
        if not inputs:
            return []
        return self._request(
            f"{self.cfg.embed_base_url}/embeddings",
            {"model": self.cfg.embed_model, "input": inputs},
            # OpenAI returns data sorted by "index"; sort defensively.
            lambda d: [row["embedding"] for row in sorted(d["data"], key=lambda x: x.get("index", 0))])


def concierge_client_from_env() -> "Client | None":
    """The tier-0 concierge runs a small model LOCALLY (a bundled llama.cpp
    server in the consumer install), separate from the network work tier — so a
    weak device answers chat/routing on-device for free and only pays credits for
    real work sent to the network. Returns None when unset → callers reuse the
    work client (dev convenience, single-model behavior)."""
    model = os.environ.get("KORETEX_CONCIERGE_MODEL")
    if not model:
        return None
    return Client(ModelConfig(
        base_url=os.environ.get("KORETEX_CONCIERGE_BASE_URL", "http://localhost:8080/v1"),
        model=model,
        api_key=os.environ.get("KORETEX_CONCIERGE_API_KEY", "local"),
    ))


def escalation_client_from_env() -> "Client | None":
    """The tier-3 client: a stronger model for irreducible steps. Configured
    separately from the work tier (its own model, and optionally its own endpoint
    / key for a BYO-key premium provider). Returns None when unset — tier-3 then
    stays off and missions behave as if escalation didn't exist."""
    model = os.environ.get("KORETEX_AGENT_ESCALATION_MODEL")
    if not model:
        return None
    return Client(ModelConfig(
        base_url=os.environ.get("KORETEX_AGENT_ESCALATION_BASE_URL",
                                os.environ.get("KORETEX_AGENT_BASE_URL", "http://localhost:11434/v1")),
        model=model,
        api_key=os.environ.get("KORETEX_AGENT_ESCALATION_API_KEY",
                               os.environ.get("KORETEX_API_KEY", "local")),
    ))
