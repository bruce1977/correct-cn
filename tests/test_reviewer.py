"""Tests for the Ollama reviewer (HTTP calls mocked, so they run offline)."""

import json
from unittest import mock

from app.reviewer import TextReviewer


def _fake_response(payload: dict):
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return _Resp()


def test_prompt_building_and_parsing(monkeypatch):
    cfg = {
        "system_prompt": "SYS",
        "user_template": "审校：{text}",
        "output_format": "FMT",
        "temperature": 0.2,
        "max_tokens": 512,
    }
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 30, cfg)

    # Capture the outgoing request body.
    sent = {}

    def _fake_urlopen(req, timeout=None):
        sent["data"] = json.loads(req.data.decode("utf-8"))
        return _fake_response({"response": "建议修改为：你好"})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)

    result = reviewer.review("你号")
    assert result["reachable"] is True
    assert result["suggestions"] == "建议修改为：你好"
    # Verify the prompt was assembled from template + output format.
    assert "你号" in sent["data"]["prompt"]
    assert "FMT" in sent["data"]["prompt"]
    assert sent["data"]["system"] == "SYS"
    assert sent["data"]["model"] == "qwen3.5:9b"
    assert sent["data"]["options"]["temperature"] == 0.2


def test_unreachable_returns_error(monkeypatch):
    import urllib.error

    def _boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _boom)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {})
    result = reviewer.review("text")
    assert result["reachable"] is False
    assert result["suggestions"] == ""
    assert result["error"] is not None


def test_is_reachable_false_when_down(monkeypatch):
    import urllib.error

    def _boom(req, timeout=None):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _boom)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {})
    assert reviewer.is_reachable() is False


def test_is_reachable_true_when_up(monkeypatch):
    def _ok(req, timeout=None):
        return _fake_response({"response": "pong"})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _ok)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {})
    assert reviewer.is_reachable() is True
