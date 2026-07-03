"""Scrubbing + consent gating: the safeguards that stand between a trajectory and
any export."""
import json

import pytest

from koretex_agent.consent import ConsentError, load_consent, require_consent, set_consent
from koretex_agent.export import prepare, write_bundle
from koretex_agent.scrub import Scrubber, scrub_text


# ── scrubbing ────────────────────────────────────────────────────────────
def test_scrub_redacts_the_usual_secrets():
    # all values below are deliberately fake / documentation-only
    s = ("key sk-test-000000000000fakekey and email a.dev@example.com "
         "at /Users/alice/code/x running on 203.0.113.9 with Bearer abc123XYZ.token")
    out = scrub_text(s)
    assert "sk-test-" not in out and "<redacted:api_key>" in out
    assert "a.dev@example.com" not in out and "<redacted:email>" in out
    assert "alice" not in out and "/Users/<user>" in out
    assert "203.0.113.9" not in out and "<redacted:ip>" in out
    assert "abc123XYZ.token" not in out and "Bearer <redacted:token>" in out


def test_scrub_preserves_ordinary_text_and_semver():
    s = "print('hi') built with pytest 8.4.1 and 26 tests passed"
    assert scrub_text(s) == s  # no false-positive redaction (semver is not an IP)


def test_scrub_env_secret_literal():
    fake = "FAKEsecret0123456789abcdefFAKE0123456789"  # not a real credential
    sc = Scrubber(extra_secrets=[fake])
    out = sc.text(f"secret={fake} done")
    assert "FAKEsecret" not in out and "<redacted:secret>" in out
    assert sc.counts["env_secret"] == 1


def test_scrub_obj_is_recursive():
    sc = Scrubber()
    o = {"messages": [{"content": "at /Users/alice/x"}], "n": 3}
    out = sc.obj(o)
    assert out["messages"][0]["content"] == "at /Users/<user>/x"
    assert out["n"] == 3  # non-strings untouched


# ── consent ──────────────────────────────────────────────────────────────
def test_require_consent_raises_without_file(tmp_path):
    with pytest.raises(ConsentError):
        require_consent(tmp_path / "consent.json")


def test_require_consent_raises_when_declined(tmp_path):
    p = tmp_path / "consent.json"
    set_consent(contribute=False, scope="user", path=p)
    with pytest.raises(ConsentError):
        require_consent(p)


def test_set_then_require_consent(tmp_path):
    p = tmp_path / "consent.json"
    set_consent(contribute=True, scope="own", note="team box", path=p)
    c = require_consent(p)
    assert c.contribute and c.scope == "own"
    assert load_consent(p).note == "team box"


# ── export applies scrubbing to real harvested examples ──────────────────
def _traj(path, order_id, task, content):
    lines = [
        {"event": "start", "profile": "worker", "contract": {"order_id": order_id, "task": task}},
        {"event": "message", "msg": {"role": "system", "content": "sys"}},
        {"event": "message", "msg": {"role": "user", "content": content}},
        {"event": "message", "msg": {"role": "assistant", "content": "ok", "tool_calls": [{"id": "1", "function": {"name": "run_shell", "arguments": "{}"}}]}},
        {"event": "verdict", "verdict": {"order_id": order_id, "done": True, "report": "r"}},
    ]
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))


def test_prepare_scrubs_examples(tmp_path):
    _traj(tmp_path / "a.jsonl", "m-1-aaaaaa", "task", "work in /Users/alice/proj")
    bundle = prepare(store=tmp_path)
    assert bundle["datasets"]["worker_sft"], "expected one passing worker example"
    blob = json.dumps(bundle["datasets"]["worker_sft"])
    assert "alice" not in blob and "/Users/<user>" in blob
    assert bundle["scrub_counts"].get("home_macos", 0) >= 1


def test_write_bundle_manifest_records_consent_and_scrub(tmp_path):
    _traj(tmp_path / "a.jsonl", "m-1-aaaaaa", "task", "at /Users/bob/x")
    bundle = prepare(store=tmp_path)
    consent = set_consent(contribute=True, scope="own", path=tmp_path / "consent.json")
    manifest = write_bundle(tmp_path / "out", bundle, consent)
    assert manifest["consent"]["scope"] == "own"
    assert manifest["stats"]["counts"]["worker_sft"] == 1
    assert (tmp_path / "out" / "worker_sft.jsonl").exists()
