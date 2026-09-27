"""Tests for config loading: 5-node layout + OLLAMA_* env fallback.

Covers:
  * ``OLLAMA_*`` fills empty transport fields on review/audit (shared local model).
  * The final node (Step 5) has NO env var fallback — its fields must come from
    config.json or built-in defaults.
  * Confirms the old generic ``LLM_*`` / ``FINAL_*`` aliases are gone (no silent
    effect).
"""

import json

from app.config import Settings, load_config


def _write_config(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)


def test_load_config_fills_local_and_overrides_final(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    _write_config(cfg_file, {
        "corrector": {"model": "m"},
        "sensitive": {},
        "review": {"protocol": "ollama"},
        "audit": {"enabled": False},
        # final is explicitly configured in config.json (no env var fallback).
        "final": {"enabled": True, "protocol": "openai",
                  "base_url": "https://api.deepseek.com",
                  "model": "deepseek-chat", "api_key": "sk-final"},
    })
    settings = Settings(data_dir=str(tmp_path), config_path=str(cfg_file))

    # Shared local Ollama settings.
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-local")
    # Old aliases must be ignored.
    monkeypatch.setenv("FINAL_BASE_URL", "http://should-be-ignored-final")
    monkeypatch.setenv("LLM_BASE_URL", "http://should-be-ignored-llm")

    cfg = load_config(settings)

    # review & audit inherit the shared local Ollama settings.
    assert cfg["review"]["base_url"] == "http://ollama:11434"
    assert cfg["review"]["model"] == "qwen3.5:9b"
    assert cfg["review"]["api_key"] == "sk-local"
    assert cfg["audit"]["base_url"] == "http://ollama:11434"
    assert cfg["audit"]["model"] == "qwen3.5:9b"

    # final comes from config.json; FINAL_* / LLM_* must NOT leak.
    assert cfg["final"]["base_url"] == "https://api.deepseek.com"
    assert cfg["final"]["model"] == "deepseek-chat"
    assert cfg["final"]["protocol"] == "openai"
    assert cfg["final"]["api_key"] == "sk-final"
    assert "should-be-ignored" not in cfg["final"]["base_url"]
    assert "should-be-ignored" not in cfg["review"]["base_url"]


def test_load_config_local_settings_dont_overwrite_explicit_final(monkeypatch, tmp_path):
    """An explicit final.base_url in config.json survives OLLAMA_*."""
    cfg_file = tmp_path / "config.json"
    _write_config(cfg_file, {
        "review": {"protocol": "ollama"},
        "audit": {"enabled": False},
        "final": {"enabled": True, "protocol": "openai",
                   "base_url": "http://explicit-final:8000/v1", "model": "qwen-32b"},
    })
    settings = Settings(data_dir=str(tmp_path), config_path=str(cfg_file))

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3.5:9b")

    cfg = load_config(settings)
    # OLLAMA_* must NOT clobber the explicit external final endpoint.
    assert cfg["final"]["base_url"] == "http://explicit-final:8000/v1"
    assert cfg["final"]["model"] == "qwen-32b"
    # But review/audit still get the local Ollama defaults.
    assert cfg["review"]["base_url"] == "http://ollama:11434"


def test_load_config_defaults_protocol_to_ollama(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.json"
    _write_config(cfg_file, {"review": {}, "audit": {}, "final": {}})
    settings = Settings(data_dir=str(tmp_path), config_path=str(cfg_file))

    cfg = load_config(settings)
    for step in ("review", "audit", "final"):
        assert cfg[step]["protocol"] == "ollama"
