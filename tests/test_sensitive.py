"""Tests for the sensitive-word engine (offline; uses a temp dictionary)."""

import os
import tempfile
from unittest import mock

from app.sensitive import SensitiveEngine


def _make_engine(tmp_path):
    # Create a small deterministic dictionary.
    d = tmp_path / "sensitive"
    d.mkdir()
    (d / "violence.txt").write_text("炸弹\n枪支\n# comment line\n", encoding="utf-8")
    (d / "politics.txt").write_text("敏感词A\n敏感词B\n", encoding="utf-8")
    return SensitiveEngine(str(d))


def test_loads_categories_and_counts(tmp_path):
    engine = _make_engine(tmp_path)
    assert set(engine.get_categories()) == {"violence", "politics"}
    # 2 words per file (comment ignored)
    assert engine.get_word_count() == 4
    assert engine.get_word_count("violence") == 2


def test_detection_with_line_and_position(tmp_path):
    engine = _make_engine(tmp_path)
    text = "今天去买了一个炸弹\n他在网上卖了枪支"
    result = engine.check_text(text)
    assert result["is_sensitive"] is True
    assert result["count"] == 2
    hits = {(h["word"], h["line"], h["start"], h["end"]) for h in result["sensitive_words"]}
    # 炸弹 -> line 1, index 6-8
    # 枪支 -> line 2, index 7-9
    assert hits == {("炸弹", 1, 7, 9), ("枪支", 2, 6, 8)}


def test_category_filter(tmp_path):
    engine = _make_engine(tmp_path)
    result = engine.check_text("炸弹和敏感词A", categories=["politics"])
    assert result["count"] == 1
    assert result["sensitive_words"][0]["word"] == "敏感词A"


def test_reload_picks_up_new_file(tmp_path):
    engine = _make_engine(tmp_path)
    # Add a new dictionary file and reload.
    (tmp_path / "sensitive" / "extra.txt").write_text("测试词\n", encoding="utf-8")
    engine.reload()
    assert "extra" in engine.get_categories()
    res = engine.check_text("这里有测试词")
    assert res["count"] == 1


def test_dictionaries_are_auto_discovered(tmp_path):
    engine = _make_engine(tmp_path)
    # Nothing is hard-coded: the discovered list comes from the directory.
    files = {d["file"] for d in engine.list_dictionaries()}
    assert files == {"violence.txt", "politics.txt"}
    # Every entry reports its word count.
    for d in engine.list_dictionaries():
        assert d["words"] == 2


def test_refresh_default_uses_discovered_files(tmp_path):
    engine = _make_engine(tmp_path)
    # When no explicit file list is given, refresh targets the discovered files.
    captured = {}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "NEW词1\nNEW词2\n".encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return _FakeResp()

    with mock.patch("app.sensitive.urllib.request.urlopen", _fake_urlopen):
        summary = engine.refresh_from_remote(
            remote_base="https://example.com/", force=True
        )
    # Both discovered dictionaries are refreshed (no hard-coded list involved).
    assert set(summary["updated"]) == {"violence.txt", "politics.txt"}


def test_refresh_from_remote_writes_and_reloads(tmp_path):
    engine = _make_engine(tmp_path)

    # Fake HTTP response returning a dictionary for a known file.
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return "远程词1\n远程词2\n# ignore\n".encode("utf-8")

    def _fake_urlopen(req, timeout=None):
        return _FakeResp()

    with mock.patch("app.sensitive.urllib.request.urlopen", _fake_urlopen):
        summary = engine.refresh_from_remote(
            remote_base="https://example.com/", files=["violence.txt"], force=True
        )
    # The violence.txt file should have been overwritten with remote words.
    assert "violence.txt" in summary["updated"]
    assert summary["failed"] == []
    res = engine.check_text("包含远程词1的内容")
    assert res["count"] == 1
    assert res["sensitive_words"][0]["word"] == "远程词1"


def test_refresh_skips_existing_when_not_forced(tmp_path):
    engine = _make_engine(tmp_path)
    before = (tmp_path / "sensitive" / "violence.txt").read_text(encoding="utf-8")

    with mock.patch(
        "app.sensitive.urllib.request.urlopen",
        side_effect=AssertionError("should not be called when not forced"),
    ):
        summary = engine.refresh_from_remote(
            remote_base="https://example.com/", files=["violence.txt"], force=False
        )
    after = (tmp_path / "sensitive" / "violence.txt").read_text(encoding="utf-8")
    assert before == after
    assert summary["updated"] == []
