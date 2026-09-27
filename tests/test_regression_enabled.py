"""Regression test: audit & final steps must read their own enabled flag and
per-step system_prompt from the (5-node) config.

This guards the refactor where audit/final became independent config nodes with
their own `system_prompt`. It proves that:

* enabling `audit.enabled` / `final.enabled` actually triggers Step 4 / Step 5,
* each step sends *its own* node's `system_prompt` (not review's, not empty),
* the request params `enable_audit` / `enable_final_suggestion` still override,
* and the whole thing works when the real `data/config.json` is loaded.
"""

import copy

from app.config import DEFAULT_CONFIG, Settings, load_config
from app.pipeline import TextPipeline
from app.reviewer import TextReviewer, build_step_reviewers
from app.schemas import Issue


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeCorrector:
    def correct(self, text):
        return {"original": text, "corrected": text, "errors": []}


class FakeSensitive:
    def check_text(self, text, categories=None):
        return {"sensitive_words": [], "is_sensitive": False, "count": 0}


def _one_issue():
    return Issue(
        type="typo",
        original="你号",
        corrected="你好",
        line=1,
        start=0,
        end=2,
        suggestion="错别字：你号→你好",
        verdict="typo_confirmed",
    )


# Captured system prompts, keyed by step name, so we can assert that each step
# used *its own* node's system_prompt.
CAPTURED = {}


def fake_review(self, corrected_text, original_text=None, typos=None, sensitive_hits=None):
    CAPTURED["review"] = self.config.get("system_prompt", "")
    return {
        "model": self.model,
        "reachable": True,
        "issues": [_one_issue()],
        "suggestions": "1. 【确认修改】原文=「你号」建议=「你好」理由=常见错别字",
    }


def fake_audit(self, issues, original_text):
    CAPTURED["audit"] = self.config.get("system_prompt", "")
    return {
        "model": self.model,
        "reachable": True,
        "suggestions": "1. 【确认修改】理由=确为错别字",
        "audit_suggestions": {0: issues[0].suggestion},
    }


def fake_final(self, original_text, issues):
    CAPTURED["final"] = self.config.get("system_prompt", "")
    return {
        "model": self.model,
        "reachable": True,
        "suggestions": original_text,
        "final_text": original_text,
    }


def _patch_methods(monkeypatch):
    CAPTURED.clear()
    monkeypatch.setattr(TextReviewer, "review", fake_review)
    monkeypatch.setattr(TextReviewer, "audit", fake_audit)
    monkeypatch.setattr(TextReviewer, "generate_final_text", fake_final)


def _cfg_with(enabled_audit, enabled_final, base=None):
    cfg = copy.deepcopy(base or DEFAULT_CONFIG)
    cfg["audit"]["enabled"] = enabled_audit
    cfg["final"]["enabled"] = enabled_final
    return cfg


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_load_config_reads_enabled_from_data_file():
    """The real runtime config (data/config.json) now has both steps enabled."""
    cfg = load_config(Settings(data_dir="data"))
    assert cfg["audit"]["enabled"] is True
    assert cfg["final"]["enabled"] is True
    # And each carries its own non-empty, distinct system_prompt.
    assert "终审复核员" in cfg["audit"]["system_prompt"]
    assert "文本修正助手" in cfg["final"]["system_prompt"]
    assert cfg["review"]["system_prompt"] != cfg["audit"]["system_prompt"]
    assert cfg["audit"]["system_prompt"] != cfg["final"]["system_prompt"]


def test_pipeline_reads_enabled_and_new_system_prompts(monkeypatch):
    """With config enabled, Step 4 + Step 5 fire and send their own system."""
    cfg = load_config(Settings(data_dir="data"))
    _patch_methods(monkeypatch)
    reviewers = build_step_reviewers(cfg)
    pipeline = TextPipeline(FakeCorrector(), FakeSensitive(), reviewers, review_config=cfg)

    result = pipeline.run("他那天很纳闷，你号打错了。")

    # Both steps were invoked and each used its OWN system_prompt.
    assert "review" in CAPTURED and "audit" in CAPTURED and "final" in CAPTURED
    assert CAPTURED["review"] == cfg["review"]["system_prompt"]
    assert CAPTURED["audit"] == cfg["audit"]["system_prompt"]
    assert CAPTURED["final"] == cfg["final"]["system_prompt"]
    # The new per-step prompts (not review's, not empty) were used.
    assert "审校复核助手" in CAPTURED["review"]
    assert "终审复核员" in CAPTURED["audit"]
    assert "文本修正助手" in CAPTURED["final"]
    # Output reflects both optional steps running.
    assert result["audit"] is not None
    assert result["final_suggestion"] is not None


def test_pipeline_disabled_skips_audit_and_final(monkeypatch):
    """When both steps are disabled, neither fires and output omits them."""
    cfg = _cfg_with(False, False)
    _patch_methods(monkeypatch)
    reviewers = build_step_reviewers(cfg)
    pipeline = TextPipeline(FakeCorrector(), FakeSensitive(), reviewers, review_config=cfg)

    result = pipeline.run("他那天很纳闷，你号打错了。")

    assert "audit" not in CAPTURED
    assert "final" not in CAPTURED
    assert result["audit"] is None
    assert result["final_suggestion"] is None


def test_request_param_overrides_config_enabled(monkeypatch):
    """enable_audit=True forces Step 4 even when config has it disabled."""
    cfg = _cfg_with(False, False)
    _patch_methods(monkeypatch)
    reviewers = build_step_reviewers(cfg)
    pipeline = TextPipeline(FakeCorrector(), FakeSensitive(), reviewers, review_config=cfg)

    # Force-enable only audit via request param.
    pipeline.run("你号打错了。", enable_audit=True, enable_final_suggestion=False)
    assert "audit" in CAPTURED
    assert "final" not in CAPTURED

    # And force-disable final even though config would have it on.
    CAPTURED.clear()
    cfg_on = _cfg_with(True, True)
    reviewers_on = build_step_reviewers(cfg_on)
    pipeline_on = TextPipeline(
        FakeCorrector(), FakeSensitive(), reviewers_on, review_config=cfg_on
    )
    pipeline_on.run("你号打错了。", enable_audit=False, enable_final_suggestion=False)
    assert "audit" not in CAPTURED
    assert "final" not in CAPTURED
