"""Unit tests for the Aho-Corasick matcher (no external dependencies)."""

from app.matcher import AhoCorasick


def test_single_word_match():
    ac = AhoCorasick()
    ac.add_word("炸弹", "violence")
    ac.build()
    matches = ac.find("今天去买了一个炸弹")
    assert matches == [("炸弹", "violence", 7, 9)]


def test_multiple_keywords():
    ac = AhoCorasick()
    ac.add_word("枪支", "weapons")
    ac.add_word("毒品", "drugs")
    ac.build()
    text = "他携带枪支并且贩卖毒品"
    found = {(w, s, e) for w, _, s, e in ac.find(text)}
    # 枪支 at index 3-5, 毒品 at index 9-11
    assert found == {("枪支", 3, 5), ("毒品", 9, 11)}


def test_overlapping_patterns():
    ac = AhoCorasick()
    ac.add_word("枪", "weapons")
    ac.add_word("枪支", "weapons")
    ac.build()
    matches = ac.find("枪支")
    words = {w for w, _, _, _ in matches}
    assert words == {"枪", "枪支"}


def test_no_match():
    ac = AhoCorasick()
    ac.add_word("暴恐", "violence")
    ac.build()
    assert ac.find("今天天气真好") == []


def test_empty_word_ignored():
    ac = AhoCorasick()
    ac.add_word("", "x")
    ac.build()
    assert ac.find("anything") == []


def test_add_after_build_rebuilds():
    ac = AhoCorasick()
    ac.add_word("a", "x")
    ac.build()
    ac.add_word("b", "y")  # should trigger a rebuild on next find
    matches = {(w, s, e) for w, _, s, e in ac.find("ab")}
    assert matches == {("a", 0, 1), ("b", 1, 2)}
