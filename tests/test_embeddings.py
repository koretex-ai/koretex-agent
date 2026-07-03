"""Embedding-based skill relevance (tier-0). Deterministic — a fake embedder
maps text to fixed vectors, so no model server is needed and CI stays offline."""
from pathlib import Path

import pytest

from koretex_agent import skills as sk
from koretex_agent.embeddings import Embedder, cosine
from koretex_agent.schemas import Skill


# ── cosine ───────────────────────────────────────────────────────────────
def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([1, 0], [-1, 0]) == pytest.approx(-1.0)
    assert cosine([0, 0], [1, 1]) == 0.0  # degenerate → 0, no ZeroDivision


# ── Embedder seam: prefixing + mark-dead ───────────────────────────────────
def test_embedder_applies_prefixes():
    seen = []
    def embed_fn(xs):
        seen.append(list(xs))
        return [[float(len(x))] for x in xs]
    emb = Embedder(embed_fn, query_prefix="Q: ", document_prefix="D: ")
    emb.embed_query("hi")
    emb.embed_documents(["a", "b"])
    assert seen[0] == ["Q: hi"]
    assert seen[1] == ["D: a", "D: b"]


def test_embedder_marks_itself_dead_on_failure():
    def boom(_xs):
        raise RuntimeError("no embed model")
    emb = Embedder(boom)
    assert emb.alive is True
    with pytest.raises(RuntimeError):
        emb.embed_query("x")
    assert emb.alive is False
    # subsequent calls short-circuit without hitting the backend again
    with pytest.raises(RuntimeError):
        emb.embed_documents(["y"])


# ── a controllable fake embedder for select_skills ─────────────────────────
class FakeEmbedder:
    """Maps a text to a 3-axis topic vector [csv, http, git] by marker tokens,
    simulating an embedder that groups paraphrases. alive/model mimic the real
    seam so select_skills treats it identically."""
    alive = True
    model = "fake-v1"

    def _vec(self, text: str) -> list[float]:
        t = text.lower()
        csv = any(w in t for w in ("csv", "json", "spreadsheet", "tabular", "delimited"))
        http = any(w in t for w in ("http", "network", "request", "retry", "backoff"))
        git = any(w in t for w in ("git", "rebase", "merge", "conflict"))
        v = [float(csv), float(http), float(git)]
        return v if any(v) else [0.3, 0.3, 0.3]

    def embed_query(self, text): return self._vec(text)
    def embed_documents(self, texts): return [self._vec(t) for t in texts]


@pytest.fixture
def catalog(tmp_path):
    cat = tmp_path / "skills"
    cat.mkdir()
    for name, desc in [
        ("csv-to-json-cli", "convert csv spreadsheet files into json"),
        ("http-retry-backoff", "add retry backoff to a flaky network http client"),
        ("git-rebase-helper", "resolve git rebase merge conflicts"),
    ]:
        sk.save_skill(Skill(name=name, description=desc, body="steps"), cat)
    return cat


# ── select_skills: semantic path ───────────────────────────────────────────
def test_semantic_match_beats_zero_keyword(catalog):
    # "reshape delimited records" shares NO keyword with csv-to-json-cli, but the
    # embedder groups it on the csv axis.
    picks = sk.select_skills("reshape delimited records", catalog, embedder=FakeEmbedder())
    assert picks == ["csv-to-json-cli"]
    # keyword-only finds nothing here — proves the semantic path adds recall.
    assert sk._select_keyword("reshape delimited records", sk.catalog_index(catalog), 3) == []


def test_threshold_rejects_unrelated(catalog):
    # a topic none of the skills cover → below floor, no keyword → nothing.
    picks = sk.select_skills("deploy kubernetes to the cloud", catalog, embedder=FakeEmbedder())
    assert picks == []


def test_keyword_rescues_below_floor(catalog):
    # orthogonal-axis query (git) vs the csv skill would be cos 0, but a literal
    # shared word still admits it via the keyword floor.
    picks = sk.select_skills("json", catalog, embedder=FakeEmbedder())
    assert "csv-to-json-cli" in picks


def test_ranks_by_cosine(catalog):
    picks = sk.select_skills("network http retry", catalog, embedder=FakeEmbedder(), k=3)
    assert picks[0] == "http-retry-backoff"


# ── fallback ───────────────────────────────────────────────────────────────
def test_no_embedder_uses_keyword(catalog):
    # identical to the legacy path
    picks = sk.select_skills("convert csv to json", catalog, embedder=None)
    assert picks == ["csv-to-json-cli"]


def test_dead_embedder_falls_back_to_keyword(catalog):
    class Dead:
        alive = True
        model = "x"
        def embed_query(self, _): raise RuntimeError("down")
        def embed_documents(self, _): raise RuntimeError("down")
    picks = sk.select_skills("convert csv to json", catalog, embedder=Dead())
    assert picks == ["csv-to-json-cli"]  # degraded, not crashed


# ── vector cache ───────────────────────────────────────────────────────────
def test_vectors_are_cached_and_reused(catalog):
    calls = {"n": 0}
    fake = FakeEmbedder()
    orig = fake.embed_documents
    def counting(texts):
        calls["n"] += len(texts)
        return orig(texts)
    fake.embed_documents = counting

    sk.select_skills("csv json", catalog, embedder=fake)
    first = calls["n"]
    assert first == 3  # all three skill docs embedded once
    sk.select_skills("network retry", catalog, embedder=fake)
    assert calls["n"] == first  # second call served entirely from disk cache


def test_cache_invalidated_by_model_change(catalog):
    fake = FakeEmbedder()
    sk.select_skills("csv json", catalog, embedder=fake)
    p = sk._embed_cache_path(catalog, "csv-to-json-cli")
    assert p.exists()
    fake.model = "different-model"
    # a cache written under fake-v1 must not be reused for a new vector space
    assert sk._load_cached_vector(catalog, "csv-to-json-cli",
                                  sk._skill_doc(sk.catalog_index(catalog)[0]),
                                  "different-model") is None
