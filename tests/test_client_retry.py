"""Network-reliability: classified, time-budgeted retries + friendly errors."""
import httpx
import pytest

from koretex_agent.client import Client, ModelConfig, NetworkError

CHAT_URL = "http://x/v1/chat/completions"
OK = {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {}}


def _resp(status, payload=None):
    return httpx.Response(status, json=payload if payload is not None else {},
                          request=httpx.Request("POST", CHAT_URL))


def _client(monkeypatch, sequence, **cfg):
    """sequence: items returned by successive posts; an Exception item is raised."""
    monkeypatch.setattr("koretex_agent.client.time.sleep", lambda *_: None)  # no real waits
    c = Client(ModelConfig(max_retries=cfg.pop("max_retries", 4),
                           total_retry_seconds=cfg.pop("total_retry_seconds", 1e6)))
    it = iter(sequence)
    calls = {"n": 0}
    def fake_post(url, headers=None, json=None):
        calls["n"] += 1
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt
    monkeypatch.setattr(c._http, "post", fake_post)
    c._calls = calls
    return c


def test_retries_5xx_then_succeeds(monkeypatch):
    c = _client(monkeypatch, [_resp(500), _resp(503), _resp(200, OK)])
    assert c.chat([{"role": "user", "content": "x"}]).message["content"] == "hi"
    assert c._calls["n"] == 3


def test_auth_error_is_not_retried(monkeypatch):
    c = _client(monkeypatch, [_resp(401)] * 5)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "auth"
    assert c._calls["n"] == 1  # 401 is fatal, not retried


def test_4xx_request_error_is_not_retried(monkeypatch):
    c = _client(monkeypatch, [_resp(400)] * 5)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "request"
    assert c._calls["n"] == 1


def test_connect_error_classifies_as_down(monkeypatch):
    c = _client(monkeypatch, [httpx.ConnectError("refused")] * 4)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "down"
    assert "reach the Koretex network" in ei.value.friendly


def test_read_timeout_classifies_as_slow(monkeypatch):
    c = _client(monkeypatch, [httpx.ReadTimeout("slow")] * 4)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "slow"
    assert "KORETEX_READ_TIMEOUT" in ei.value.friendly  # actionable hint


def test_dropped_connection_classifies_as_dropped(monkeypatch):
    # RemoteProtocolError = the node accepted then dropped the connection
    # mid-response — the exact failure a heavy generation on a flaky node hits.
    c = _client(monkeypatch, [httpx.RemoteProtocolError("peer closed")] * 4)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "dropped"
    assert "dropped the connection" in ei.value.friendly


def test_read_error_also_classifies_as_dropped(monkeypatch):
    c = _client(monkeypatch, [httpx.ReadError("reset")] * 4)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "dropped"


def test_dropped_connection_is_retried_then_recovers(monkeypatch):
    # A transient drop should be ridden out, not fatal — this is the whole point.
    c = _client(monkeypatch, [httpx.RemoteProtocolError("drop"),
                              httpx.RemoteProtocolError("drop"), _resp(200, OK)])
    assert c.chat([{"role": "user", "content": "x"}]).message["content"] == "hi"
    assert c._calls["n"] == 3


def test_drops_retried_up_to_max_retries(monkeypatch):
    c = _client(monkeypatch, [httpx.RemoteProtocolError("drop")] * 8, max_retries=6)
    with pytest.raises(NetworkError) as ei:
        c.chat([{"role": "user", "content": "x"}])
    assert ei.value.kind == "dropped"
    assert c._calls["n"] == 6  # honored the (raised) attempt budget


def test_max_retries_defaults_from_env(monkeypatch):
    monkeypatch.setenv("KORETEX_MAX_RETRIES", "9")
    assert ModelConfig().max_retries == 9


def test_malformed_body_is_retried_then_recovers(monkeypatch):
    # first response is valid JSON but wrong shape (no "choices") → retryable
    c = _client(monkeypatch, [_resp(200, {"oops": 1}), _resp(200, OK)])
    assert c.chat([{"role": "user", "content": "x"}]).message["content"] == "hi"
    assert c._calls["n"] == 2


def test_retry_budget_caps_attempts(monkeypatch):
    # a tiny budget means we give up after the first failure rather than looping
    c = _client(monkeypatch, [httpx.ReadTimeout("slow")] * 5, total_retry_seconds=0.0001)
    with pytest.raises(NetworkError):
        c.chat([{"role": "user", "content": "x"}])
    assert c._calls["n"] == 1
