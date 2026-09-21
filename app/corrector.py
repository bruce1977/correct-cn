"""Chinese typo / error correction backed by MacBertCorrector.

The heavy ML dependencies (torch / transformers / pycorrector) are imported
lazily inside :meth:`TextCorrector.__init__` so that this module can be imported
in lightweight environments (e.g. running the pure-Python tests) without them.

The correction model is expected to be present in the HuggingFace cache
(``/root/.cache/huggingface`` inside the container). The container is built with
the model baked in and ``HF_HUB_OFFLINE=1`` so no network access is required.

For local API testing without installing torch / transformers / pycorrector, run
with ``CORRECTOR_MODE=mock`` (see :class:`MockCorrector`).
"""

import os

# Make the offline HuggingFace cache the default and silence noisy warnings.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ["TOKENIZERS_PARALLELISM"] = "false"


class MockCorrector:
    """Dependency-free stand-in used when ``CORRECTOR_MODE=mock``.

    It performs a small, deterministic set of substring replacements so the rest
    of the service (and the full pipeline) can be exercised locally without
    installing torch / transformers / pycorrector. The output shape is identical
    to :class:`TextCorrector` so callers need no special-casing.
    """

    # A tiny built-in typo map -- enough to demonstrate the API end-to-end.
    DEMO_MAP = {
        "你号": "你好",
        "在现": "在线",
        "按装": "安装",
        "克苦": "刻苦",
        "他门": "他们",
        "以经": "已经",
        "在次": "再次",
        "那吗": "那么",
    }

    def correct(self, text: str) -> dict:
        errors = []
        corrected_lines = []
        for line_no, line in enumerate(text.split("\n"), start=1):
            new_line = line
            for wrong, right in self.DEMO_MAP.items():
                idx = new_line.find(wrong)
                while idx != -1:
                    errors.append(
                        {
                            "line": line_no,
                            "start": idx,
                            "end": idx + len(wrong),
                            "original": wrong,
                            "corrected": right,
                        }
                    )
                    new_line = new_line[:idx] + right + new_line[idx + len(wrong):]
                    idx = new_line.find(wrong)
            corrected_lines.append(new_line)
        return {
            "original": text,
            "corrected": "\n".join(corrected_lines),
            "errors": errors,
        }


class TextCorrector:
    """Thin wrapper around ``pycorrector.MacBertCorrector``.

    ``correct`` works on multi-line text: each line is corrected independently
    so that error positions can be reported relative to their source line.

    When ``mode="mock"`` a :class:`MockCorrector` is used instead, which needs no
    heavy ML dependencies and is convenient for local API testing.
    """

    def __init__(
        self,
        model_name: str = "shibing624/macbert4csc-base-chinese",
        mode: str = "model",
    ):
        self.model_name = model_name
        self.mode = mode
        if mode == "mock":
            # No ML stack required -- perfect for fast local API testing.
            self._corrector = MockCorrector()
            return
        # Imported here on purpose (see module docstring).
        from pycorrector.macbert.macbert_corrector import MacBertCorrector

        self._corrector = MacBertCorrector(model_name)

    def correct(self, text: str) -> dict:
        """Correct ``text`` and return positions of every change.

        Returns:
            dict with ``original``, ``corrected`` and ``errors``. Each error is
            ``{line, start, end, original, corrected}`` (line is 1-based, start
            and end are 0-based char offsets within that line, end exclusive).
        """
        errors = []
        corrected_lines = []
        for line_no, line in enumerate(text.split("\n"), start=1):
            result = self._corrector.correct(line)
            corrected = result.get("corrected", line)
            corrected_lines.append(corrected)
            for err in result.get("errors", []):
                # pycorrector historically returns a 4-tuple
                # (word, begin, end, corrected_word) but newer versions may
                # return a dict; handle both shapes defensively.
                if isinstance(err, dict):
                    word = err.get("word", err.get("original", ""))
                    begin = err.get("begin", err.get("start", 0))
                    end = err.get("end", begin)
                    corrected_word = err.get("corrected", err.get("target", word))
                elif len(err) == 3:
                    word, corrected_word, begin = err
                    end = begin + len(word)
                elif len(err) >= 4:
                    word, begin, end, corrected_word = err[:4]
                else:
                    continue
                errors.append(
                    {
                        "line": line_no,
                        "start": begin,
                        "end": end,
                        "original": word,
                        "corrected": corrected_word,
                    }
                )
        return {
            "original": text,
            "corrected": "\n".join(corrected_lines),
            "errors": errors,
        }
