"""End-to-end pipeline: typo correction -> sensitive check -> LLM review."""

import concurrent.futures

from app.schemas import ErrorLocation, SensitiveHit


class TextPipeline:
    """Combines the three sub-services into one workflow.

    Flow:
        1. :class:`TextCorrector` fixes typos and returns the corrected text.
        2. :class:`SensitiveEngine` scans the *original* text for sensitive words.
           Steps 1 and 2 run concurrently (see :meth:`run`).
        3. :class:`TextReviewer` asks a local LLM for an overall review of the
           corrected text, given the findings from steps 1-2.
        4. (Optional) A second LLM call consolidates everything into the final
           modification suggestion. Controlled by the ``enable_summary`` argument
           (request parameter), falling back to the ``review_config`` default.
           When disabled, or when the model is unreachable, the review text is
           reused as the final suggestion.
    """

    def __init__(self, corrector, sensitive_engine, reviewer, review_config=None):
        self.corrector = corrector
        self.sensitive_engine = sensitive_engine
        self.reviewer = reviewer
        self.review_config = review_config or {}

    def run(self, text: str, enable_summary: bool = None) -> dict:
        """Run the full pipeline over ``text`` and return an aggregated result.

        Args:
            text: the input text to process.
            enable_summary: force/disable the final summary LLM call. ``None``
                (default) falls back to ``review_config["enable_summary"]``.
        """
        # Steps 1 & 2 run concurrently: correction and sensitive-word scanning
        # are independent when the scan operates on the original text.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_correct = pool.submit(self.corrector.correct, text)
            f_sensitive = pool.submit(self.sensitive_engine.check_text, text)
            correction = f_correct.result()
            sensitive_raw = f_sensitive.result()

        corrected_text = correction["corrected"]
        typos = [ErrorLocation(**e) for e in correction["errors"]]
        sensitive_hits = [SensitiveHit(**h) for h in sensitive_raw["sensitive_words"]]

        # Step 3: LLM review with full context (original, corrections, sensitive).
        review = self.reviewer.review(
            corrected_text,
            original_text=text,
            typos=typos,
            sensitive_hits=sensitive_hits,
        )

        # Step 4: optional consolidated summary call.
        if enable_summary is None:
            enable_summary = self.review_config.get("enable_summary", True)
        summary = None
        if enable_summary:
            summary = self.reviewer.summarize(
                {
                    "corrected_text": corrected_text,
                    "typos": "\n".join(
                        f"- {t.original} -> {t.corrected}" for t in typos
                    )
                    or "（无）",
                    "sensitive": "\n".join(
                        f"- {h.word}（{h.category}）" for h in sensitive_hits
                    )
                    or "（无）",
                    "review": review.get("suggestions") or "（无）",
                }
            )
            final_suggestion = summary.get("suggestions") or review.get("suggestions", "")
        else:
            final_suggestion = review.get("suggestions", "")

        # Whether the input has any detectable problem.
        has_issues = bool(typos) or bool(sensitive_hits)

        return {
            "original": text,
            "corrected_text": corrected_text,
            "has_issues": has_issues,
            "typos": typos,
            "sensitive_words": sensitive_hits,
            "review": review,
            "summary": summary,
            "final_suggestion": final_suggestion,
        }
