"""Embeddings for tier-0 skill relevance.

Skill *triggering* — deciding which learned skills a task should load — was
keyword overlap, which misses paraphrase ("convert a spreadsheet to JSON" vs a
skill named `csv-to-json-cli`: zero shared words, obvious semantic match). This
module supplies the semantic signal.

By design it runs locally: matching a task to a skill is a routing decision and
must not cost a network round-trip to the work tier. The default backend is a
small Ollama embed model over the same OpenAI-compatible client the kernel
already uses (one provider, by design) — see ModelConfig.embed_base_url.

The Embedder is a *seam*: callers pass one in to get semantic ranking, or pass
None to fall back to keyword overlap. That keeps the offline test suite and any
box without the embed model green with no code path changes.
"""
from __future__ import annotations

import math
from typing import Callable

# nomic-embed-text is asymmetric: queries and documents get distinct task
# prefixes, and skipping them measurably degrades retrieval. These defaults suit
# nomic; override for a model with a different prompting convention.
QUERY_PREFIX = "search_query: "
DOCUMENT_PREFIX = "search_document: "


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors. 0.0 if either is degenerate."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class Embedder:
    """Wraps a batch embed function with query/document prefixing. If a call
    raises (embed model absent, server down) the embedder marks itself dead so
    callers stop retrying it for the rest of the run — the first failure costs
    one fallback, not one per task."""

    def __init__(self, embed_fn: Callable[[list[str]], list[list[float]]], *,
                 model: str = "unknown",
                 query_prefix: str = QUERY_PREFIX, document_prefix: str = DOCUMENT_PREFIX):
        self._embed = embed_fn
        self.model = model  # identifies the vector space; used to invalidate caches
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self.alive = True

    def _run(self, texts: list[str]) -> list[list[float]]:
        if not self.alive:
            raise RuntimeError("embedder marked dead after a prior failure")
        try:
            return self._embed(texts)
        except Exception:
            self.alive = False
            raise

    def embed_query(self, text: str) -> list[float]:
        return self._run([self.query_prefix + text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._run([self.document_prefix + t for t in texts])


def default_embedder(client=None) -> Embedder:
    """A local-Ollama-backed embedder over the kernel's OpenAI-compatible client.
    Construction is lazy/cheap — it does not probe the server; the first embed
    call fails fast to keyword fallback if the model isn't available."""
    if client is None:
        from .client import Client
        client = Client()
    return Embedder(client.embed, model=client.cfg.embed_model)
