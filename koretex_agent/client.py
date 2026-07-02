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
    max_retries: int = 3


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
