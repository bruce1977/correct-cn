"""Chinese sensitive-word detection and dictionary management.

The engine loads every ``.txt`` dictionary file (one keyword per line,
``#`` starts a comment line) it finds in its data directory. Each file becomes a
category named after the file stem (e.g. ``political.txt`` -> category
``political``). The list of dictionaries is *discovered at runtime* -- nothing is
hard-coded -- and cached on disk at startup so subsequent reloads/refreshes are
fast and the API can report what is available.

Detection uses the Aho-Corasick automaton from :mod:`app.matcher` so that tens
of thousands of keywords can be matched in linear time.

All external network access goes through the standard library only
(:mod:`urllib`), keeping the dependency footprint minimal.
"""

import json
import os
import urllib.request

from app.matcher import AhoCorasick

# Default remote source used by the "refresh" endpoint. It mirrors the
# well-known konsheng/Sensitive-lexicon repository. Override via settings.
DEFAULT_REMOTE_BASE = (
    "https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/"
)

# Read timeout (seconds) for remote dictionary fetches.
_HTTP_TIMEOUT = 15

# Name of the on-disk cache that records the discovered dictionaries at startup.
_CACHE_SUBDIR = ".cache"
_CACHE_FILENAME = "dictionaries.json"


class SensitiveEngine:
    """Loads sensitive-word dictionaries and finds them in text."""

    def __init__(self, data_dir: str, case_insensitive: bool = False):
        self.data_dir = data_dir
        self.case_insensitive = case_insensitive
        self._matcher = AhoCorasick()
        self._categories = {}  # category -> set(words)
        # Discovered dictionary file names (e.g. ["violence.txt", ...]). This is
        # the in-memory cache used by refresh/skip logic so nothing is hard-coded.
        self._discovered_files = []
        self.reload()

    # ------------------------------------------------------------------ #
    # Loading / reloading
    # ------------------------------------------------------------------ #
    def reload(self) -> None:
        """(Re)load every ``*.txt`` dictionary file found in ``data_dir``."""
        self._matcher = AhoCorasick()
        self._categories = {}
        self._discovered_files = []
        if not os.path.isdir(self.data_dir):
            return
        for name in sorted(os.listdir(self.data_dir)):
            # Skip non-dictionary files and the hidden cache directory.
            if name.startswith("."):
                continue
            if not name.lower().endswith(".txt"):
                continue
            category = os.path.splitext(name)[0]
            path = os.path.join(self.data_dir, name)
            words = self._read_word_file(path)
            if words:
                # Normalise (e.g. lower-case) each keyword when configured.
                words = {self._normalize(w) for w in words}
                self._categories[category] = words
                for w in words:
                    self._matcher.add_word(w, category)
                self._discovered_files.append(name)
        self._matcher.build()
        # Persist the discovered list as a startup cache.
        self._write_cache()

    @staticmethod
    def _read_word_file(path: str) -> set:
        """Read a dictionary file into a set of trimmed, non-empty keywords.

        Raw words are returned here; case normalisation (if enabled) is applied
        by the caller in :meth:`reload` so the same logic covers every source.
        """
        words = set()
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                words.add(line)
        return words

    def _normalize(self, word: str) -> str:
        """Apply case-insensitivity normalisation if enabled."""
        return word.lower() if self.case_insensitive else word

    @staticmethod
    def _write_word_file(path: str, words) -> None:
        """Persist a collection of keywords (one per line) to ``path``."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(sorted(words)))
            if words:
                fh.write("\n")

    def _write_cache(self) -> None:
        """Write the discovered dictionary list to a startup cache file."""
        try:
            cache_dir = os.path.join(self.data_dir, _CACHE_SUBDIR)
            os.makedirs(cache_dir, exist_ok=True)
            payload = {
                "files": self._discovered_files,
                "categories": sorted(self._categories.keys()),
                "total_words": self.get_word_count(),
            }
            with open(
                os.path.join(cache_dir, _CACHE_FILENAME), "w", encoding="utf-8"
            ) as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        except OSError:
            # A non-writable cache is non-fatal; the in-memory index still works.
            pass

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def get_categories(self) -> list:
        """Return the list of loaded category names."""
        return sorted(self._categories.keys())

    def get_word_count(self, category: str = None) -> int:
        """Return the number of keywords for ``category`` or the total."""
        if category:
            return len(self._categories.get(category, set()))
        return sum(len(w) for w in self._categories.values())

    def list_dictionaries(self) -> list:
        """Return metadata for every discovered dictionary file.

        Each entry is ``{"file": str, "category": str, "words": int}``.
        """
        result = []
        for fname in self._discovered_files:
            category = os.path.splitext(fname)[0]
            result.append(
                {
                    "file": fname,
                    "category": category,
                    "words": self.get_word_count(category),
                }
            )
        return result

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #
    def check_text(self, text: str, categories: list = None) -> dict:
        """Scan ``text`` and return every sensitive-word hit.

        Args:
            text: the document to scan (may contain multiple lines).
            categories: optional list of category names to restrict to.

        Returns:
            A dict with ``is_sensitive``, ``count`` and ``sensitive_words``.
            Each hit is ``{word, category, line, start, end}`` where ``line``
            is 1-based and ``start``/``end`` are 0-based char offsets within
            that line (``end`` is exclusive).
        """
        if self.case_insensitive:
            text = text.lower()
        cat_filter = set(categories) if categories else None
        hits = []
        for line_no, line in enumerate(text.split("\n"), start=1):
            for word, category, start, end in self._matcher.find(line):
                if cat_filter is not None and category not in cat_filter:
                    continue
                hits.append(
                    {
                        "word": word,
                        "category": category,
                        "line": line_no,
                        "start": start,
                        "end": end,
                    }
                )
        return {
            "is_sensitive": len(hits) > 0,
            "count": len(hits),
            "sensitive_words": hits,
            "categories_checked": sorted(cat_filter) if cat_filter else self.get_categories(),
        }

    # ------------------------------------------------------------------ #
    # Remote refresh
    # ------------------------------------------------------------------ #
    def refresh_from_remote(
        self,
        remote_base: str = DEFAULT_REMOTE_BASE,
        files: list = None,
        force: bool = False,
    ) -> dict:
        """Fetch dictionary files from a remote source and persist them.

        Args:
            remote_base: base URL that serves the dictionary files.
            files: specific filenames to fetch. When ``None`` the engine
                refreshes the dictionaries it discovered on disk (nothing is
                hard-coded).
            force: when False, existing local files are kept (only missing
                files are downloaded). When True, every file is re-downloaded.

        Returns:
            A summary dict with ``updated`` (list of filenames written) and
            ``failed`` (list of filenames that could not be fetched).
        """
        os.makedirs(self.data_dir, exist_ok=True)
        # Discover the target files from the directory instead of a hard-coded list.
        targets = files if files else list(self._discovered_files)
        updated, failed = [], []
        for filename in targets:
            dest = os.path.join(self.data_dir, filename)
            if not force and os.path.exists(dest):
                continue
            url = remote_base.rstrip("/") + "/" + urllib.request.quote(filename)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "correct-cn"})
                with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
                    raw = resp.read().decode("utf-8")
                words = {
                    ln.strip()
                    for ln in raw.split("\n")
                    if ln.strip() and not ln.strip().startswith("#")
                }
                self._write_word_file(dest, words)
                updated.append(filename)
            except Exception as exc:  # noqa: BLE001 - network errors are non-fatal
                failed.append({"file": filename, "error": str(exc)})
        # Reload in-memory structures from the (possibly updated) directory.
        self.reload()
        return {
            "updated": updated,
            "failed": failed,
            "categories": self.get_categories(),
            "total_words": self.get_word_count(),
        }

    def save_category(self, category: str, words) -> int:
        """Replace the keyword set of ``category`` and persist it.

        Returns the number of keywords written.
        """
        word_set = {self._normalize(w.strip()) for w in words if w and w.strip()}
        self._categories[category] = word_set
        self._write_word_file(
            os.path.join(self.data_dir, category + ".txt"), word_set
        )
        # Rebuild the whole matcher for consistency.
        self.reload()
        return len(word_set)
