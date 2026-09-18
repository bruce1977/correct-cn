"""Integration test for the full pipeline using lightweight fakes.

The heavy MacBert model and the Ollama LLM are replaced by fakes so this test
runs anywhere. It verifies that the three steps are orchestrated correctly and
that positions/categories flow through to the final response.
"""

import os
import tempfile

from app.pipeline import TextPipeline
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
        self.summary_calls = []

    def review(self, text, **kwargs):
        self.calls.append(text)
        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "整体建议：表达可以更通顺。",
            "error": None,
        }

    def summarize(self, context):
        self.summary_calls.append(context)
        return {
            "model": "fake",
            "reachable": True,
            "suggestions": "最终建议：请按上述修改。",
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

    # Step 4: a second summary call consolidates everything.
    assert reviewer.summary_calls  # summary was invoked
    assert "最终建议" in result["final_suggestion"]

    # has_issues is True because typos and sensitive words were found.
    assert result["has_issues"] is True


def test_pipeline_has_issues_false_when_clean(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer)

    # No typo and no sensitive word -> has_issues is False.
    result = pipeline.run("这是一段完全正常的文本")
    assert result["has_issues"] is False
    # final_suggestion uses the summary text (enable_summary defaults to True).
    assert "最终建议" in result["final_suggestion"]


def test_pipeline_summary_disabled_reuses_review(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # enable_summary=False -> the review text is reused, no summary call.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_summary": False})

    result = pipeline.run("你号，这里有一个炸弹")
    assert reviewer.summary_calls == []
    assert "整体建议" in result["final_suggestion"]
    assert result["summary"] is None


def test_pipeline_request_param_forces_summary_on(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # Config defaults summary OFF, but the request forces it ON.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_summary": False})

    result = pipeline.run("你号，这里有一个炸弹", enable_summary=True)
    assert reviewer.summary_calls  # request param overrode the config default
    assert "最终建议" in result["final_suggestion"]
    assert result["summary"] is not None


def test_pipeline_request_param_forces_summary_off(tmp_path):
    engine = _make_engine(tmp_path)
    reviewer = FakeReviewer()
    # Config defaults summary ON, but the request forces it OFF.
    pipeline = TextPipeline(FakeCorrector(), engine, reviewer, review_config={"enable_summary": True})

    result = pipeline.run("你号，这里有一个炸弹", enable_summary=False)
    assert reviewer.summary_calls == []
    assert "整体建议" in result["final_suggestion"]
    assert result["summary"] is None
