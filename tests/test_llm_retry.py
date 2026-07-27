import io
import json
import urllib.error

import pytest

from forge import llm


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _ok_response() -> _FakeResponse:
    return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})


def test_chat_retries_on_http_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=120):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "rate limited", {}, io.BytesIO(b"slow down"))
        return _ok_response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    out = llm._chat("http://x", "key", "model", "prompt", 0.5)
    assert out == "ok"
    assert calls["n"] == 3


def test_chat_retries_on_url_error_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=120):
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.URLError("connection refused")
        return _ok_response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    out = llm._chat("http://x", "key", "model", "prompt", 0.5)
    assert out == "ok"
    assert calls["n"] == 2


def test_chat_gives_up_after_max_retries(monkeypatch):
    def fake_urlopen(req, timeout=120):
        raise urllib.error.HTTPError(req.full_url, 500, "server error", {}, io.BytesIO(b"boom"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        llm._chat("http://x", "key", "model", "prompt", 0.5)


def test_temperature_400_special_case_does_not_consume_retry_budget_or_sleep(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=120):
        calls["n"] += 1
        body = json.loads(req.data.decode())
        if "temperature" in body:
            raise urllib.error.HTTPError(
                req.full_url, 400, "bad request", {}, io.BytesIO(b"temperature not supported")
            )
        return _ok_response()

    def _no_sleep_allowed(seconds):
        raise AssertionError("temperature-400 fix should retry immediately without sleeping")

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(llm.time, "sleep", _no_sleep_allowed)

    out = llm._chat("http://x", "key", "model", "prompt", 0.5)
    assert out == "ok"
    assert calls["n"] == 2
