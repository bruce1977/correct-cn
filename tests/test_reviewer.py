"""Tests for the Ollama reviewer (HTTP calls mocked, so they run offline)."""

import json
from unittest import mock

from app.reviewer import TextReviewer, build_step_reviewers
from app.schemas import ErrorLocation, SensitiveHit
import io
import urllib.error


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


def test_review_parses_verdicts_and_missed(monkeypatch):
    """The structured review response must yield typed issues + verdicts,
    including 漏报 (problems the tools missed) parsed from 【漏报】."""
    # A realistic LLM output: per-item verdicts (typos first, then sensitive)
    # followed by a 漏报 section.
    sample = (
        "【复核结论】\n"
        "1. 【确认修改】原文=「你号」建议=「你好」理由=人称问候常见错别字\n"
        "2. 【非错误】原文=「己经」理由=人名用字\n"
        "3. 【确认敏感】原文=「炸弹」建议=「删除」理由=描述购买行为\n\n"
        "【漏报】\n"
        "1. 类型=语法 原文=「由于下雨了所以比赛取消」建议=「因为下雨，比赛取消了」理由=关联词冗余\n"
        "2. 类型=错别字 原文=「纳闷」建议=「纳闷」理由=形近字\n"
    )

    def _fake_urlopen(req, timeout=None):
        return _fake_response({"response": sample})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)

    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 30, {})
    typos = [
        ErrorLocation(line=1, start=0, end=2, original="你号", corrected="你好"),
        ErrorLocation(line=1, start=3, end=5, original="己经", corrected="已经"),
    ]
    hits = [SensitiveHit(word="炸弹", category="涉枪涉爆", line=1, start=6, end=8)]
    result = reviewer.review("你号己经炸弹", typos=typos, sensitive_hits=hits)

    assert result["reachable"] is True
    issues = result["issues"]
    # 3 tool findings (2 typos + 1 sensitive) + 2 漏报 = 5 issues.
    assert len(issues) == 5

    # Index 0: confirmed typo.
    assert issues[0].type == "typo"
    assert issues[0].verdict == "typo_confirmed"
    assert "你好" in issues[0].suggestion

    # Index 1: 误报 (false positive) -> rejected.
    assert issues[1].type == "typo"
    assert issues[1].verdict == "typo_rejected"
    assert "非错误" in issues[1].suggestion or "用法正确" in issues[1].suggestion

    # Index 2: confirmed sensitive.
    assert issues[2].type == "sensitive"
    assert issues[2].verdict == "sensitive_confirmed"
    assert "确认敏感词" in issues[2].suggestion

    # 漏报 items: one grammar, one typo.
    grammar = [i for i in issues if i.type == "grammar"]
    assert grammar, "expected a 漏报 grammar issue"
    assert grammar[0].verdict == "grammar_confirmed"
    assert "因为下雨" in grammar[0].corrected

    missed_typos = [i for i in issues if i.type == "typo" and i.verdict == "typo_confirmed" and i.original == "纳闷"]
    assert missed_typos, "expected a 漏报 typo issue (纳闷)"


def test_review_standalone_returns_raw_suggestions(monkeypatch):
    """Without tool findings, review returns the raw LLM text + empty issues."""
    sample = "全文无明显错误，仅发现一处漏报：\n【漏报】\n1. 类型=语法 原文=「他说的对」建议=「他说得对」理由=的地得"

    def _fake_urlopen(req, timeout=None):
        return _fake_response({"response": sample})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 30, {})
    result = reviewer.review("他说的对")
    assert result["reachable"] is True
    assert result["suggestions"] == sample
    # The 漏报 grammar issue is still extracted.
    assert any(i.type == "grammar" for i in result["issues"])


# --------------------------------------------------------------------------- #
# Retry / error-handling behaviour of _generate
# --------------------------------------------------------------------------- #
def test_generate_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.reviewer.time.sleep", lambda *a, **k: None)
    calls = {"n": 0}

    def _flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("transient down")
        return _fake_response({"response": "ok"})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _flaky)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {"max_retries": 2})
    result = reviewer._generate("sys", "prompt")
    assert result["reachable"] is True
    assert result["suggestions"] == "ok"
    assert calls["n"] == 3  # 2 failed attempts + 1 success


def test_generate_exhausts_retries(monkeypatch):
    monkeypatch.setattr("app.reviewer.time.sleep", lambda *a, **k: None)
    calls = {"n": 0}

    def _boom(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _boom)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {"max_retries": 2})
    result = reviewer._generate("sys", "prompt")
    assert result["reachable"] is False
    assert result["error"] is not None
    assert calls["n"] == 3  # max_retries(2) + 1


def test_generate_no_retry_on_4xx(monkeypatch):
    calls = {"n": 0}

    def _client_err(req, timeout=None):
        calls["n"] += 1
        fp = io.BytesIO(b'{"error":"bad request"}')
        raise urllib.error.HTTPError(req.full_url, 400, "msg", {}, fp)

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _client_err)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {"max_retries": 2})
    result = reviewer._generate("sys", "prompt")
    assert result["reachable"] is False
    assert calls["n"] == 1  # 4xx -> no retry


def test_generate_retries_on_429(monkeypatch):
    monkeypatch.setattr("app.reviewer.time.sleep", lambda *a, **k: None)
    calls = {"n": 0}

    def _rate(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            fp = io.BytesIO(b'{"error":"rate limited"}')
            raise urllib.error.HTTPError("http://x", 429, "msg", {}, fp)
        return _fake_response({"response": "ok"})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _rate)
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {"max_retries": 2})
    result = reviewer._generate("sys", "prompt")
    assert result["reachable"] is True
    assert calls["n"] == 2  # 1 retry after 429


# --------------------------------------------------------------------------- #
# Protocol compatibility: the same flow against an OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #
def _openai_body(content: str):
    return {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}]}


def test_normalize_protocol_aliases():
    from app.reviewer import normalize_protocol

    assert normalize_protocol("openai") == "openai"
    assert normalize_protocol("OpenAI-Compatible") == "openai"
    assert normalize_protocol("openai_compatible") == "openai"
    assert normalize_protocol("v1") == "openai"
    # Unknown / missing values fall back to Ollama so old configs keep working.
    assert normalize_protocol("") == "ollama"
    assert normalize_protocol(None) == "ollama"
    assert normalize_protocol("ollama") == "ollama"


def test_openai_chat_url_normalisation():
    """A bare host, a /v1 base and a full path must all resolve to one URL."""
    cfg = {"protocol": "openai"}
    cases = {
        "https://api.deepseek.com": "https://api.deepseek.com/v1/chat/completions",
        "https://api.deepseek.com/v1": "https://api.deepseek.com/v1/chat/completions",
        "https://dashscope.aliyuncs.com/compatible-mode/v1":
            "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "https://host/v1/chat/completions": "https://host/v1/chat/completions",
    }
    for base, expected in cases.items():
        assert TextReviewer(base, "m", 5, cfg)._chat_url() == expected


def test_ollama_chat_url_unchanged():
    reviewer = TextReviewer("http://ollama:11434", "qwen3.5:9b", 5, {})
    assert reviewer._chat_url() == "http://ollama:11434/api/generate"
    assert reviewer.protocol == "ollama"


def test_openai_payload_uses_messages_not_ollama_fields(monkeypatch):
    """OpenAI payloads must not leak Ollama-only keys (servers 400 on them)."""
    cfg = {
        "protocol": "openai",
        "system_prompt": "SYS",
        "user_template": "审校：{text}",
        "output_format": "FMT",
        "temperature": 0.2,
        "max_tokens": 512,
        "num_ctx": 16384,  # Ollama-only: must NOT appear in an OpenAI payload
    }
    reviewer = TextReviewer("https://api.deepseek.com", "deepseek-chat", 30, cfg,
                            api_key="sk-test")

    sent = {}

    def _fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        sent["headers"] = {k.lower(): v for k, v in req.header_items()}
        sent["data"] = json.loads(req.data.decode("utf-8"))
        return _fake_response(_openai_body("1. 【确认修改】原文=「你号」建议=「你好」理由=常见错别字"))

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)

    result = reviewer.review("你号")
    assert result["reachable"] is True
    assert result["suggestions"].startswith("1. 【确认修改】")
    assert sent["url"] == "https://api.deepseek.com/v1/chat/completions"

    body = sent["data"]
    assert body["model"] == "deepseek-chat"
    assert body["stream"] is False
    assert body["temperature"] == 0.2
    assert body["max_tokens"] == 512
    # messages array (system + user), no Ollama-native fields.
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1]["role"] == "user"
    assert "你号" in body["messages"][1]["content"]
    assert "FMT" in body["messages"][1]["content"]
    for ollama_only in ("prompt", "system", "options", "think", "num_ctx"):
        assert ollama_only not in body
    # Bearer auth from the config-provided api_key.
    assert sent["headers"].get("authorization") == "Bearer sk-test"


def test_openai_max_tokens_param_override(monkeypatch):
    """Newer OpenAI models want max_completion_tokens instead of max_tokens."""
    cfg = {"protocol": "openai", "max_tokens": 256,
           "max_tokens_param": "max_completion_tokens"}
    reviewer = TextReviewer("https://api.openai.com", "gpt-x", 30, cfg)
    sent = {}

    def _fake_urlopen(req, timeout=None):
        sent["data"] = json.loads(req.data.decode("utf-8"))
        return _fake_response(_openai_body("ok"))

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer._generate("s", "p")
    assert sent["data"]["max_completion_tokens"] == 256
    assert "max_tokens" not in sent["data"]


def test_openai_extra_body_merged(monkeypatch):
    cfg = {"protocol": "openai", "extra_body": {"top_p": 0.9, "seed": 7}}
    reviewer = TextReviewer("https://host/v1", "m", 30, cfg)
    sent = {}

    def _fake_urlopen(req, timeout=None):
        sent["data"] = json.loads(req.data.decode("utf-8"))
        return _fake_response(_openai_body("ok"))

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer._generate("s", "p")
    assert sent["data"]["top_p"] == 0.9
    assert sent["data"]["seed"] == 7


def test_openai_reasoning_fallback(monkeypatch):
    """Reasoning models may leave content empty; use reasoning_content then."""
    cfg = {"protocol": "openai"}

    def _fake_urlopen(req, timeout=None):
        return _fake_response({
            "choices": [{"message": {"content": "", "reasoning_content": "思考过程"},
                         "finish_reason": "stop"}]
        })

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer = TextReviewer("https://host/v1", "m", 30, cfg)
    result = reviewer._generate("s", "p")
    assert result["reachable"] is True
    assert result["suggestions"] == "思考过程"


def test_openai_error_object_is_surfaced(monkeypatch):
    """OpenAI reports errors as an object: {"error": {"message": ...}}."""
    cfg = {"protocol": "openai", "max_retries": 0}

    def _fake_urlopen(req, timeout=None):
        return _fake_response({"error": {"message": "Incorrect API key provided",
                                         "type": "invalid_request_error"}})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer = TextReviewer("https://host/v1", "m", 30, cfg)
    result = reviewer._generate("s", "p")
    assert result["reachable"] is False
    assert result["suggestions"] == ""
    assert "Incorrect API key" in result["error"]


def test_openai_is_reachable_uses_models_endpoint(monkeypatch):
    """The OpenAI probe is a token-free GET /v1/models."""
    cfg = {"protocol": "openai"}
    reviewer = TextReviewer("https://api.deepseek.com", "m", 5, cfg, api_key="sk-1")
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _fake_response({"data": []})

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    assert reviewer.is_reachable() is True
    assert seen["url"] == "https://api.deepseek.com/v1/models"
    assert seen["method"] == "GET"
    assert seen["headers"].get("authorization") == "Bearer sk-1"


def test_openai_review_parses_verdicts_from_choices(monkeypatch):
    """The verdict parser is transport-agnostic: OpenAI bodies parse the same."""
    sample = (
        "【复核结论】\n"
        "1. 【确认修改】原文=「你号」建议=「你好」理由=常见错别字\n"
        "2. 【非错误】原文=「己经」理由=人名用字\n\n"
        "【漏报】\n"
        "1. 类型=语法 原文=「他说的对」建议=「他说得对」理由=的地得\n"
    )

    def _fake_urlopen(req, timeout=None):
        return _fake_response(_openai_body(sample))

    monkeypatch.setattr("app.reviewer.urllib.request.urlopen", _fake_urlopen)
    reviewer = TextReviewer("https://host/v1", "m", 30, {"protocol": "openai"})
    typos = [ErrorLocation(line=1, start=0, end=2, original="你号", corrected="你好"),
             ErrorLocation(line=1, start=3, end=5, original="己经", corrected="已经")]
    result = reviewer.review("你号己经", typos=typos, sensitive_hits=[])
    assert result["reachable"] is True
    issues = result["issues"]
    assert issues[0].verdict == "typo_confirmed"
    assert issues[1].verdict == "typo_rejected"
    assert any(i.type == "grammar" for i in issues)


def test_api_key_falls_back_to_config(monkeypatch):
    """api_key may come from config (merged from LLM_API_KEY / OLLAMA_API_KEY)."""
    cfg = {"protocol": "openai", "api_key": "sk-from-config"}
    reviewer = TextReviewer("https://host/v1", "m", 30, cfg)
    assert reviewer.api_key == "sk-from-config"
    # An explicit constructor argument still wins.
    reviewer2 = TextReviewer("https://host/v1", "m", 30, cfg, api_key="sk-explicit")
    assert reviewer2.api_key == "sk-explicit"


# --------------------------------------------------------------------------- #
# Per-step provider selection (5-node config -> {review,audit,final} reviewers)
# --------------------------------------------------------------------------- #
def _config_with_external_final():
    """A 5-node config where review/audit are local Ollama, final is external."""
    base_local = {
        "protocol": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://ollama:11434",
        "api_key": "",
    }
    return {
        "review": {**base_local, "system_prompt": "REVIEW_SYS",
                    "user_template": "{text}", "output_format": "FMT",
                    "temperature": 0.2, "max_tokens": 8192, "num_ctx": 16384},
        "audit": {**base_local, "enabled": True, "system_prompt": "",
                   "audit_prompt": "AUDIT_PROMPT"},
        "final": {**{"protocol": "openai",
                     "model": "deepseek-chat",
                     "base_url": "https://api.deepseek.com",
                     "api_key": "sk-final"},
                  "enabled": True, "system_prompt": "",
                  "final_prompt": "FINAL_PROMPT"},
    }


def test_build_step_reviewers_routes_final_external():
    cfg = _config_with_external_final()
    reviewers = build_step_reviewers(cfg)

    # review + audit stay on the local Ollama; final goes to the external model.
    assert reviewers["review"].protocol == "ollama"
    assert reviewers["review"].model == "qwen3.5:9b"
    assert reviewers["audit"].protocol == "ollama"
    assert reviewers["final"].protocol == "openai"
    assert reviewers["final"].model == "deepseek-chat"
    assert reviewers["final"]._chat_url() == "https://api.deepseek.com/v1/chat/completions"
    assert reviewers["final"].api_key == "sk-final"


def test_build_step_reviewers_inherits_per_step_prompts():
    cfg = _config_with_external_final()
    reviewers = build_step_reviewers(cfg)
    # Each step carries its own prompt so the parser/format stays per-step.
    assert reviewers["review"].config.get("system_prompt") == "REVIEW_SYS"
    assert reviewers["audit"].config.get("audit_prompt") == "AUDIT_PROMPT"
    assert reviewers["final"].config.get("final_prompt") == "FINAL_PROMPT"


def test_build_step_reviewers_falls_back_to_review_node():
    """An old 3-node config (no audit/final) routes every step to review."""
    cfg = {
        "review": {"protocol": "ollama", "model": "qwen3.5:9b",
                   "base_url": "http://ollama:11434", "api_key": ""}
    }
    reviewers = build_step_reviewers(cfg)
    for step in ("review", "audit", "final"):
        assert reviewers[step].model == "qwen3.5:9b"
        assert reviewers[step].protocol == "ollama"


def test_default_config_audit_final_have_own_system_prompt():
    """audit/final must each carry their own non-empty, distinct system_prompt."""
    from app.config import DEFAULT_CONFIG
    reviewers = build_step_reviewers(DEFAULT_CONFIG)
    review_sys = reviewers["review"].config.get("system_prompt", "")
    audit_sys = reviewers["audit"].config.get("system_prompt", "")
    final_sys = reviewers["final"].config.get("system_prompt", "")
    assert review_sys, "review system_prompt should be populated"
    assert audit_sys, "audit system_prompt should be populated (its own, not empty)"
    assert final_sys, "final system_prompt should be populated (its own, not empty)"
    # Distinct steps get distinct personas -- they are not the same string.
    assert audit_sys != review_sys
    assert final_sys != review_sys
    assert audit_sys != final_sys


def test_audit_uses_its_own_system_prompt(monkeypatch):
    """audit() sends the audit node's system_prompt, never review's."""
    captured = {}

    def fake_generate(self, system, prompt):
        captured["system"] = system
        captured["prompt"] = prompt
        return {"model": "m", "reachable": True,
                "suggestions": "1. 【确认修改】理由=ok", "error": None}

    monkeypatch.setattr(TextReviewer, "_generate", fake_generate)
    reviewer = TextReviewer(
        "http://x", "m", 30,
        {"system_prompt": "AUDIT_SYS_UNIQUE",
         "audit_prompt": "issues:{issues}\ntext:{original_text}"},
    )
    reviewer.audit([], "原文")
    assert captured["system"] == "AUDIT_SYS_UNIQUE"


def test_final_uses_its_own_system_prompt(monkeypatch):
    """generate_final_text() sends the final node's system_prompt, never review's."""
    captured = {}

    def fake_generate(self, system, prompt):
        captured["system"] = system
        captured["prompt"] = prompt
        return {"model": "m", "reachable": True,
                "suggestions": "修正后", "error": None}

    monkeypatch.setattr(TextReviewer, "_generate", fake_generate)
    reviewer = TextReviewer(
        "http://x", "m", 30,
        {"system_prompt": "FINAL_SYS_UNIQUE",
         "final_prompt": "原文:{original_text}\n建议:{issues}"},
    )
    reviewer.generate_final_text("原文", [])
    assert captured["system"] == "FINAL_SYS_UNIQUE"

