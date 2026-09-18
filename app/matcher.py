"""Pure-Python multi-pattern matcher based on the Aho-Corasick algorithm.

This module has zero third-party dependencies so it can be unit-tested and
used without installing the heavy ML stack (torch / transformers / pycorrector).
"""

from collections import deque


class _ACNode:
    """A single node in the Aho-Corasick trie.

    Attributes:
        children: mapping from a single character to the child node.
        fail: failure (fallback) link used when no child matches.
        output: list of (word, category) tuples that end at this node.
    """

    __slots__ = ("children", "fail", "output")

    def __init__(self):
        self.children = {}
        self.fail = None
        self.output = []


class AhoCorasick:
    """Aho-Corasick automaton that finds every occurrence of many keywords.

    Typical usage:
        ac = AhoCorasick()
        ac.add_word("炸弹", "violence")
        ac.build()
        for word, category, start, end in ac.find("这是一个炸弹"):
            ...
    """

    def __init__(self):
        self.root = _ACNode()
        self._built = False

    def add_word(self, word: str, category: str) -> None:
        """Register a keyword ``word`` that belongs to ``category``."""
        if not word:
            return
        node = self.root
        for ch in word:
            nxt = node.children.get(ch)
            if nxt is None:
                nxt = _ACNode()
                node.children[ch] = nxt
            node = nxt
        node.output.append((word, category))
        self._built = False

    def build(self) -> None:
        """Compute failure links. Must be called after all words are added."""
        queue: deque = deque()

        # Initialize: every direct child of the root fails back to the root.
        for child in self.root.children.values():
            child.fail = self.root
            queue.append(child)

        while queue:
            current = queue.popleft()
            for ch, child in current.children.items():
                # Walk the failure chain to find the longest proper suffix
                # that is also a prefix of some registered keyword.
                fail = current.fail
                while fail is not None and ch not in fail.children:
                    fail = fail.fail
                child.fail = fail.children[ch] if fail is not None else self.root
                # Inherit outputs from the failure node (overlapping matches).
                child.output = child.output + child.fail.output
                queue.append(child)

        self._built = True

    def find(self, text: str) -> list:
        """Return all matches in ``text``.

        Each match is a tuple ``(word, category, start, end)`` where ``start``
        is the 0-based index of the first character and ``end`` is exclusive.
        """
        if not self._built:
            self.build()

        matches = []
        node = self.root
        for i, ch in enumerate(text):
            # Follow failure links until we can continue along the trie.
            while node is not None and ch not in node.children:
                node = node.fail
            if node is None:
                node = self.root
                continue
            node = node.children[ch]
            if node.output:
                for word, category in node.output:
                    start = i - len(word) + 1
                    matches.append((word, category, start, i + 1))
        return matches
