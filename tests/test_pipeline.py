"""Integration test for the full pipeline using lightweight fakes.

The heavy MacBert model and the Ollama LLM are replaced by fakes so this test
runs anywhere. It verifies that the three steps are orchestrated correctly and
that positions/categories flow through to the final response.
"""

import os
import tempfile

from app.pipeline import TextPipeline
from app.schemas import Issue
from app.sensitive import SensitiveEngine


class FakeCorrector:
    def correct(self, text):
        # Pretend "你号" is corrected to "你好".
        corrected = text.replace("你号", "你好")
        errors = []
        if "你号" in text:
            idx = text.index("你号")
            errors.append(
                {"line": 1, "start": idx, "end": idx + 2, "original": "你号", "corrected": "你好"}
            )
        return {"original": text, "corrected": corrected, "errors": errors}


class FakeReviewer:
    def __init__(self):
        self.calls = []
        self.audit_calls = []
        self.final_calls = []

    def review(self, text, **kwargs):
        self.calls.append(text)
        # Return structured issues
        issues = []
        typos = kwargs.get("typos", [])
        sensitive_hits = kwargs.get("sensitive_hits", [])
        for typo in typos:
            issues.append(Issue(
                type="typo",
                original=typo.original,
                corrected=typo.corrected,
                line=typo.line,
                start=typo.start,
                end=typo.end,
                suggestion="确认修改",
            ))
        for hit in sensitive_hits:
            issues.append(Issue(
                type="sensitive",
                word=hit.word,
                category=hit.category,
                line=hit.line,
                start=hit.start,
                end=hit.end,
                suggestion="确认敏感词",
            ))
        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "整体建议：表达可以更通顺。",
            "issues": issues,
            "error": None,
        }

    def audit(self, issues, original_text):
        self.audit_calls.append({"issues": issues, "original_text": original_text})
        audit_suggestions = {}
        for i, issue in enumerate(issues):
            audit_suggestions[i] = f"复核确认: {issue.suggestion}"
        return {
            "model": "fake",
            "reachable": True,
            "audit_suggestions": audit_suggestions,
            "error": None,
        }

    def generate_final_text(self, original_text, issues):
        self.final_calls.append({"original_text": original_text, "issues": issues})
        # Apply corrections to generate final text
        final_text = original_text
        for issue in issues:
            if issue.type == "typo":
                final_text = final_text.replace(issue.original, issue.corrected)
        return {
            "model": "fake",
            "reachable": True,
            "final_text": final_text,
            "error": None,
        }


def _make_engine(tmp_path):
    d = tmp_path / "sensitive"
    d.mkdir()
    (d / "violence.txt").write_text("炸弹\n", encoding="utf-8")
    return SensitiveEngine(str(d))


def test_pipeline_runs_all_steps(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer)

    text = "你号，这里有一个炸弹"
    result = pipeline.run(text)

    # Step 1: typo corrected.
    assert result["corrected_text"] == "你好，这里有一个炸弹"
    assert result["typos"][0].original == "你号"
    assert result["typos"][0].corrected == "你好"

    # Step 2: sensitive word found in the corrected text.
    assert result["sensitive_words"][0].word == "炸弹"
    assert result["sensitive_words"][0].line == 1

    # Step 3: review called on the CORRECTED text.
    assert reviewer.calls == ["你好，这里有一个炸弹"]

    # Step 3: issues are returned.
    assert len(result["issues"]) == 2
    assert result["issues"][0].type == "typo"
    assert result["issues"][0].original == "你号"
    assert result["issues"][1].type == "sensitive"
    assert result["issues"][1].word == "炸弹"

    # has_issues is True because typos and sensitive words were found.
    assert result["has_issues"] is True


def test_pipeline_has_issues_false_when_clean(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer)

    # No typo and no sensitive word -> has_issues is False.
    result = pipeline.run("这是一段完全正常的文本")
    assert result["has_issues"] is False
    # No issues when text is clean.
    assert len(result["issues"]) == 0
    # final_suggestion is None when not enabled.
    assert result["final_suggestion"] is None


def test_pipeline_audit_enabled(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # enable_audit=True -> the audit step is invoked.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_audit": True})

    result = pipeline.run("你号，这里有一个炸弹")
    assert reviewer.audit_calls  # audit was invoked
    # Issues should have audit suggestions applied.
    assert any("复核确认" in issue.suggestion for issue in result["issues"])


def test_pipeline_final_suggestion_enabled(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # enable_final_suggestion=True -> final text is generated.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_final_suggestion": True})

    result = pipeline.run("你号，这里有一个炸弹")
    assert reviewer.final_calls  # final generation was invoked
    assert result["final_suggestion"] is not None
    assert "你好" in result["final_suggestion"]


def test_pipeline_request_param_forces_audit_on(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # Config defaults audit OFF, but the request forces it ON.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_audit": False})

    result = pipeline.run("你号，这里有一个炸弹", enable_audit=True)
    assert reviewer.audit_calls  # request param overrode the config default
    assert any("复核确认" in issue.suggestion for issue in result["issues"])


def test_pipeline_request_param_forces_audit_off(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # Config defaults audit ON, but the request forces it OFF.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_audit": True})

    result = pipeline.run("你号，这里有一个炸弹", enable_audit=False)
    assert reviewer.audit_calls == []  # request param overrode the config default
    # Issues should have original suggestions, not audit suggestions.
    assert result["issues"][0].suggestion == "确认修改"


class ContextAwareReviewer:
    """A fake reviewer that judges sensitivity based on context."""

    def review(self, text, **kwargs):
        issues = []
        sensitive_hits = kwargs.get("sensitive_hits", [])
        for hit in sensitive_hits:
            if hit.word == "炸弹":
                # Judge based on context
                if "新闻" in text or "报道" in text or "事件" in text:
                    suggestion = "经验证，「炸弹」在新闻报道语境中是正常用法"
                elif "购买" in text or "购买了" in text or "买" in text:
                    suggestion = "确认敏感词: 描述购买行为，需删除"
                else:
                    suggestion = "需根据上下文判断"
            else:
                suggestion = "需根据上下文判断"
            issues.append(Issue(
                type="sensitive",
                word=hit.word,
                category=hit.category,
                line=hit.line,
                start=hit.start,
                end=hit.end,
                suggestion=suggestion,
            ))
        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "",
            "issues": issues,
            "error": None,
        }


def test_sensitive_word_not_sensitive_in_news_context(tmp_path):
    """炸弹 in news reporting context should NOT be sensitive."""
    engine = _make_engine(tmp_path)
    reviewer = ContextAwareReviewer()
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer)

    text = "新闻报道了这次炸弹事件的最新进展"
    result = pipeline.run(text)

    # Sensitive word found
    assert len(result["sensitive_words"]) == 1
    assert result["sensitive_words"][0].word == "炸弹"

    # But review marks it as non-sensitive (news context)
    assert len(result["issues"]) == 1
    assert result["issues"][0].type == "sensitive"
    assert "正常用法" in result["issues"][0].suggestion


def test_sensitive_word_sensitive_in_purchase_context(tmp_path):
    """炸弹 in purchase context SHOULD be sensitive."""
    engine = _make_engine(tmp_path)
    reviewer = ContextAwareReviewer()
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer)

    text = "我在网上购买了炸弹"
    result = pipeline.run(text)

    # Sensitive word found
    assert len(result["sensitive_words"]) == 1
    assert result["sensitive_words"][0].word == "炸弹"

    # Review confirms it as sensitive (purchase context)
    assert len(result["issues"]) == 1
    assert result["issues"][0].type == "sensitive"
    assert "确认敏感词" in result["issues"][0].suggestion


class ContextAwareCorrector:
    """A fake corrector that catches some "typos" which may be false positives."""

    def correct(self, text):
        corrected = text
        errors = []

        # Flag "己" as a potential typo for "已" (形近字误判)
        if "己经" in text:
            idx = text.index("己经")
            errors.append({
                "line": 1, "start": idx, "end": idx + 2,
                "original": "己经", "corrected": "已经"
            })
            corrected = text.replace("己经", "已经")

        return {"original": text, "corrected": corrected, "errors": errors}


class TypoContextReviewer:
    """A fake reviewer that judges typo validity based on context."""

    def review(self, text, **kwargs):
        issues = []
        typos = kwargs.get("typos", [])
        for typo in typos:
            if typo.original == "己经":
                # Check if it's a person's name or intentional usage
                if "张" in text or "李" in text or "姓" in text:
                    # Person's name context - not a typo
                    suggestion = "经验证，「己经」是人名用字，非错别字"
                else:
                    # Regular context - confirm the typo
                    suggestion = "确认修改: 「己经」应为「已经」"
            else:
                suggestion = "确认修改"
            issues.append(Issue(
                type="typo",
                original=typo.original,
                corrected=typo.corrected,
                line=typo.line,
                start=typo.start,
                end=typo.end,
                suggestion=suggestion,
            ))
        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "",
            "issues": issues,
            "error": None,
        }


def test_typo_not_typo_in_name_context(tmp_path):
    """己经 in person name context should NOT be flagged as typo."""
    d = tmp_path / "sensitive"
    d.mkdir()
    (d / "test.txt").write_text("test\n", encoding="utf-8")
    engine = SensitiveEngine(str(d))

    reviewer = TypoContextReviewer()
    pipeline = TextPipeline(ContextAwareCorrector(), engine, reviewer)

    # Person's name "张己经" - the "己" is part of the name
    text = "张己经是我们的同事"
    result = pipeline.run(text)

    # Typo detected by corrector
    assert len(result["typos"]) == 1
    assert result["typos"][0].original == "己经"

    # But review marks it as non-error (person's name context)
    assert len(result["issues"]) == 1
    assert result["issues"][0].type == "typo"
    assert "非错别字" in result["issues"][0].suggestion


def test_typo_is_typo_in_regular_context(tmp_path):
    """己经 in regular context SHOULD be confirmed as typo."""
    d = tmp_path / "sensitive"
    d.mkdir()
    (d / "test.txt").write_text("test\n", encoding="utf-8")
    engine = SensitiveEngine(str(d))

    reviewer = TypoContextReviewer()
    pipeline = TextPipeline(ContextAwareCorrector(), engine, reviewer)

    # Regular text with typo
    text = "这件事己经过去了"
    result = pipeline.run(text)

    # Typo detected by corrector
    assert len(result["typos"]) == 1
    assert result["typos"][0].original == "己经"

    # Review confirms it as typo
    assert len(result["issues"]) == 1
    assert result["issues"][0].type == "typo"
    assert "确认修改" in result["issues"][0].suggestion


class FullContextReviewer:
    """A fake reviewer that simulates LLM context judgment for all scenarios."""

    def review(self, text, **kwargs):
        issues = []
        typos = kwargs.get("typos", [])
        sensitive_hits = kwargs.get("sensitive_hits", [])

        # Process typos
        for typo in typos:
            if typo.original == "你号":
                # "你号" is always a typo, should be "你好"
                suggestion = "确认修改: 「你号」应为「你好」"
            elif typo.original == "己经":
                # "己经" could be a name or typo
                if "张" in text or "李" in text or "王" in text:
                    suggestion = "经验证，「己经」是人名用字，非错别字"
                else:
                    suggestion = "确认修改: 「己经」应为「已经」"
            else:
                suggestion = "确认修改"
            issues.append(Issue(
                type="typo",
                original=typo.original,
                corrected=typo.corrected,
                line=typo.line,
                start=typo.start,
                end=typo.end,
                suggestion=suggestion,
            ))

        # Process sensitive words - use position to distinguish occurrences
        for hit in sensitive_hits:
            if hit.word == "炸弹":
                # Get immediate context (10 chars before and after)
                start = hit.start
                context_start = max(0, start - 10)
                context_end = min(len(text), start + len(hit.word) + 10)
                context = text[context_start:context_end]

                # Judge based on the immediate context
                if "新闻" in context or "报道" in context:
                    suggestion = "经验证，「炸弹」在新闻报道语境中是正常用法"
                elif "制造" in context or "购买" in context:
                    suggestion = "确认敏感词: 描述制造或购买行为，需删除"
                else:
                    suggestion = "需根据上下文判断"
            else:
                suggestion = "需根据上下文判断"
            issues.append(Issue(
                type="sensitive",
                word=hit.word,
                category=hit.category,
                line=hit.line,
                start=hit.start,
                end=hit.end,
                suggestion=suggestion,
            ))

        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "",
            "issues": issues,
            "error": None,
        }


class MultiScenarioCorrector:
    """A fake corrector that detects multiple types of typos."""

    def correct(self, text):
        corrected = text
        errors = []

        # Detect "你号" → "你好"
        if "你号" in text:
            idx = text.index("你号")
            errors.append({
                "line": 1, "start": idx, "end": idx + 2,
                "original": "你号", "corrected": "你好"
            })
            corrected = corrected.replace("你号", "你好")

        # Detect "己经" → "已经" (形近字误判)
        if "己经" in text:
            idx = text.index("己经")
            errors.append({
                "line": 1, "start": idx, "end": idx + 2,
                "original": "己经", "corrected": "已经"
            })
            corrected = corrected.replace("己经", "已经")

        return {"original": text, "corrected": corrected, "errors": errors}


def test_comprehensive_context_review(tmp_path):
    """Comprehensive test covering multiple scenarios in one text.

    Test text:
    "你号，我是张己经。新闻报道了这次炸弹事件，但有人说想制造炸弹。"

    Expected detections:
    - Typo: "你号" → "你好" (real typo)
    - Typo: "己经" → "已经" (but it's a person's name, so false positive)
    - Sensitive: "炸弹" appears twice:
      1. "新闻报道了这次炸弹事件" → non-sensitive (news context)
      2. "有人说想制造炸弹" → sensitive (describes制造行为)

    Expected review results:
    - "你号" → confirmed typo
    - "己经" → non-error (person's name context)
    - First "炸弹" → non-sensitive (news context)
    - Second "炸弹" → confirmed sensitive (制造行为)
    """
    d = tmp_path / "sensitive"
    d.mkdir()
    (d / "violence.txt").write_text("炸弹\n", encoding="utf-8")
    engine = SensitiveEngine(str(d))

    reviewer = FullContextReviewer()
    pipeline = TextPipeline(MultiScenarioCorrector(), engine, reviewer)

    text = "你号，我是张己经。新闻报道了这次炸弹事件，但有人说想制造炸弹。"
    result = pipeline.run(text)

    # --- Step 1: Corrections ---
    # Two typos detected
    assert len(result["typos"]) == 2
    typo_words = {t.original for t in result["typos"]}
    assert "你号" in typo_words
    assert "己经" in typo_words

    # --- Step 2: Sensitive words ---
    # "炸弹" appears twice (both found by scanner)
    assert len(result["sensitive_words"]) == 2
    assert all(h.word == "炸弹" for h in result["sensitive_words"])

    # --- Step 3: Review validates each finding ---
    issues = result["issues"]
    # 2 typos + 2 sensitive = 4 issues
    assert len(issues) == 4

    # Typo issues
    typo_issues = [i for i in issues if i.type == "typo"]
    assert len(typo_issues) == 2

    # "你号" → confirmed typo
    ni_hao = next(i for i in typo_issues if i.original == "你号")
    assert "确认修改" in ni_hao.suggestion

    # "己经" → non-error (person's name context: "张己经")
    ji_jing = next(i for i in typo_issues if i.original == "己经")
    assert "非错别字" in ji_jing.suggestion

    # Sensitive word issues
    sensitive_issues = [i for i in issues if i.type == "sensitive"]
    assert len(sensitive_issues) == 2

    # Both are "炸弹", but different contexts
    # The review should distinguish them
    bomb_suggestions = [i.suggestion for i in sensitive_issues]
    # One should be non-sensitive (news context)
    assert any("正常用法" in s for s in bomb_suggestions)
    # One should be confirmed sensitive (制造行为)
    assert any("确认敏感词" in s for s in bomb_suggestions)
