"""Tests for the MacBert corrector.

Two flavors:
  * ``MockCorrector`` (``CORRECTOR_MODE=mock``) -- fully offline, no ML stack,
    so it runs everywhere and is great for local API testing.
  * ``MacBertCorrector`` (``CORRECTOR_MODE=model``) -- requires torch /
    transformers / pycorrector and the downloaded model; skipped automatically
    when unavailable, so the rest of the suite stays green in lightweight CI.
"""

import pytest

from app.corrector import MockCorrector, TextCorrector

MODEL_NAME = "shibing624/macbert4csc-base-chinese"


def test_mock_corrector_fixes_demo_typos():
    corrector = TextCorrector(MODEL_NAME, mode="mock")
    result = corrector.correct("你号，这里有一个炸弹")  # 你号 -> 你好
    assert "你号" not in result["corrected"]
    assert any(e["original"] == "你号" and e["corrected"] == "你好" for e in result["errors"])


def test_mock_corrector_reports_line_and_position():
    corrector = TextCorrector(MODEL_NAME, mode="mock")
    text = "第一行正常\n第二行按装好了"  # 按装 -> 安装
    result = corrector.correct(text)
    err = next(e for e in result["errors"] if e["original"] == "按装")
    assert err["line"] == 2
    assert err["start"] == 3
    assert err["end"] == 5


def test_mock_corrector_lives_in_textcorrector_wrapper():
    # MockCorrector is reachable through the same public interface.
    assert isinstance(TextCorrector(mode="mock")._corrector, MockCorrector)


@pytest.fixture(scope="module")
def corrector():
    pytest.importorskip("pycorrector")
    try:
        return TextCorrector(MODEL_NAME)
    except Exception as exc:  # noqa: BLE001 - model may be missing in CI
        pytest.skip(f"MacBert model not available: {exc}")


def test_corrector_fixes_typo(corrector):
    result = corrector.correct("我爱北京天安们")  # 们 -> 门
    assert result["corrected"] != result["original"]
    assert any(e["original"] != e["corrected"] for e in result["errors"])


def test_corrector_reports_line_and_position(corrector):
    text = "第一行没问题\n第二行有错别子"  # 子 -> 字
    result = corrector.correct(text)
    err = next((e for e in result["errors"] if e["original"] != e["corrected"]), None)
    assert err is not None
    # The error should be reported on the second line.
    assert err["line"] == 2
    assert err["start"] >= 0
    assert err["end"] > err["start"]


def test_corrector_preserves_line_count(corrector):
    text = "line one\nline two\nline three"
    result = corrector.correct(text)
    assert result["corrected"].count("\n") == text.count("\n")
