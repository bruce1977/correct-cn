"""End-to-end pipeline: typo correction -> sensitive check -> LLM review."""

import concurrent.futures

from app.schemas import ErrorLocation, SensitiveHit


class TextPipeline:
    """Combines the three sub-services into one workflow.

    Flow:
        1. :class:`TextCorrector` fixes typos and returns the corrected text.
        2. :class:`SensitiveEngine` scans the *original* text for sensitive words.
           Steps 1 and 2 run concurrently (see :meth:`run`).
        3. :class:`TextReviewer` asks a local LLM to validate the findings from
           steps 1-2, returning structured issues with suggestions.
        4. (Optional) A second LLM call re-validates each issue's suggestion
           when ``enable_review`` is True.
        5. (Optional) A third LLM call generates the final corrected text when
           ``enable_final_suggestion`` is True.
    """

    def __init__(self, corrector, sensitive_engine, reviewer, review_config=None):
        self.corrector = corrector
        self.sensitive_engine = sensitive_engine
        self.reviewer = reviewer
        self.review_config = review_config or {}

    def run(
        self,
        text: str,
        enable_audit: bool = None,
        enable_final_suggestion: bool = None,
    ) -> dict:
        """Run the full pipeline over ``text`` and return an aggregated result.

        Args:
            text: the input text to process.
            enable_audit: force/disable the audit step (Step 4). ``None``
                (default) falls back to ``review_config["enable_audit"]``.
            enable_final_suggestion: force/disable final text generation
                (Step 5). ``None`` falls back to
                ``review_config["enable_final_suggestion"]``.
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

        issues = review.get("issues", [])

        # Step 4: optional audit (re-validate suggestions).
        if enable_audit is None:
            enable_audit = self.review_config.get("enable_audit", False)
        if enable_audit and issues:
            audit_result = self.reviewer.audit(issues, text)
            audit_suggestions = audit_result.get("audit_suggestions", {})
            # Apply audit suggestions to issues
            for idx, suggestion in audit_suggestions.items():
                if idx < len(issues):
                    issues[idx].suggestion = suggestion
            review["audit"] = audit_result

        # Step 5: optional final text generation.
        if enable_final_suggestion is None:
            enable_final_suggestion = self.review_config.get(
                "enable_final_suggestion", False
            )
        final_suggestion = None
        if enable_final_suggestion:
            final_result = self.reviewer.generate_final_text(text, issues)
            final_suggestion = final_result.get("final_text", text)

        # Whether the input has any detectable problem.
        has_issues = bool(typos) or bool(sensitive_hits)

        return {
            "original": text,
            "corrected_text": corrected_text,
            "has_issues": has_issues,
            "typos": typos,
            "sensitive_words": sensitive_hits,
            "issues": issues,
            "final_suggestion": final_suggestion,
        }
