"""The kernel's single model client: OpenAI-compatible chat completions.

One provider, by design. Endpoint and tier come from config; the same client
serves local Ollama during development and the Koretex dispatcher in product.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ModelConfig:
    base_url: str = os.environ.get("KORETEX_AGENT_BASE_URL", "http://localhost:11434/v1")
    model: str = os.environ.get("KORETEX_AGENT_MODEL", "qwen3:14b-16k")
    api_key: str = os.environ.get("KORETEX_API_KEY", "local")
    timeout_s: float = 300.0
    max_retries: int = 5  # transient 5xx from a busy/reloading network node are
    # common; exponential backoff (1,2,4,8s) rides over them so one blip doesn't
    # kill a whole mission.
    # Embeddings power tier-0 skill relevance. They run *locally* by design —
    # matching a task to a skill is a routing decision that shouldn't cost a
    # network round-trip to the work tier — so the embed endpoint defaults to
    # local Ollama even when base_url points at the dispatcher.
    embed_base_url: str = os.environ.get("KORETEX_AGENT_EMBED_BASE_URL", "http://localhost:11434/v1")
    embed_model: str = os.environ.get("KORETEX_AGENT_EMBED_MODEL", "nomic-embed-text")


@dataclass
class ChatResult:
    message: dict[str, Any]
    usage: dict[str, int] = field(default_factory=dict)


class Client:
    def __init__(self, cfg: ModelConfig | None = None):
        self.cfg = cfg or ModelConfig()
        self._http = httpx.Client(timeout=self.cfg.timeout_s)

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

        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            try:
                r = self._http.post(
                    f"{self.cfg.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    json=body,
                )
                r.raise_for_status()
                data = r.json()
                choice = data["choices"][0]
                return ChatResult(message=choice["message"], usage=data.get("usage", {}))
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2**attempt)
        raise RuntimeError(f"chat failed after {self.cfg.max_retries} attempts: {last_err}")

    def embed(self, inputs: list[str]) -> list[list[float]]:
        """Batch-embed strings via the OpenAI-compatible /embeddings endpoint
        (local Ollama by default — see ModelConfig.embed_base_url). Returns one
        vector per input, in order. Raises on failure so callers can fall back."""
        if not inputs:
            return []
        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            try:
                r = self._http.post(
                    f"{self.cfg.embed_base_url}/embeddings",
                    headers={"Authorization": f"Bearer {self.cfg.api_key}"},
                    json={"model": self.cfg.embed_model, "input": inputs},
                )
                r.raise_for_status()
                data = r.json()
                # OpenAI returns data sorted by "index"; sort defensively.
                rows = sorted(data["data"], key=lambda d: d.get("index", 0))
                return [row["embedding"] for row in rows]
            except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
                last_err = e
                time.sleep(2**attempt)
        raise RuntimeError(f"embed failed after {self.cfg.max_retries} attempts: {last_err}")


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
