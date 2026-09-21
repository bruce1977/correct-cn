"""End-to-end pipeline: typo correction -> sensitive check -> LLM review."""

import concurrent.futures

from app.schemas import ErrorLocation, SensitiveHit

# Verdicts that mean "apply this fix in the final text". ``None`` (no verdict,
# e.g. a legacy/fake issue) is treated as confirmed so old callers keep working.
CONFIRMED_VERDICTS = {"typo_confirmed", "sensitive_confirmed", "grammar_confirmed"}


def _verdict_from_audit(issue) -> str:
    """Recompute an issue's verdict from its (audited) suggestion text."""
    if issue.type == "grammar":
        return "grammar_confirmed"
    if "非错误" in issue.suggestion or "非敏感" in issue.suggestion:
        return "typo_rejected" if issue.type == "typo" else "sensitive_rejected"
    return "typo_confirmed" if issue.type == "typo" else "sensitive_confirmed"


def _is_confirmed(issue) -> bool:
    return issue.verdict is None or issue.verdict in CONFIRMED_VERDICTS


class TextPipeline:
    """Combines the three sub-services into one workflow.

    Flow:
        1. :class:`TextCorrector` fixes typos and returns the corrected text.
        2. :class:`SensitiveEngine` scans the *original* text for sensitive words.
           Steps 1 and 2 run concurrently (see :meth:`run`).
        3. :class:`TextReviewer` asks a local LLM to validate the findings from
           steps 1-2, returning structured issues with suggestions.
        4. (Optional) A second LLM call re-validates each issue's suggestion
           when the ``audit`` config node is enabled.
        5. (Optional) A third LLM call generates the final corrected text when
           the ``final`` config node is enabled.

    ``reviewers`` is a ``{step: TextReviewer}`` map (review / audit / final).
    Passing a single reviewer instance is still supported (all steps share it),
    which keeps the existing tests working.
    """

    def __init__(self, corrector, sensitive_engine, reviewers, review_config=None):
        self.corrector = corrector
        self.sensitive_engine = sensitive_engine
        if isinstance(reviewers, dict):
            self.reviewers = reviewers
        else:
            # Backward-compatible: a lone reviewer drives every step.
            self.reviewers = {
                "review": reviewers,
                "audit": reviewers,
                "final": reviewers,
            }
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
                (default) falls back to the ``audit`` config node's ``enabled``
                flag (or the legacy top-level ``enable_audit``).
            enable_final_suggestion: force/disable final text generation
                (Step 5). ``None`` falls back to the ``final`` config node's
                ``enabled`` flag (or the legacy top-level
                ``enable_final_suggestion``).
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
        review = self.reviewers["review"].review(
            corrected_text,
            original_text=text,
            typos=typos,
            sensitive_hits=sensitive_hits,
        )

        issues = review.get("issues", [])
        review_suggestions = review.get("suggestions")

        # Step 4: optional audit (二次校验) — re-validate review suggestions.
        # Reads the audit config node's "enabled" flag; a legacy top-level
        # "enable_audit" key is honoured as a fallback.
        if enable_audit is None:
            audit_cfg = self.review_config.get("audit", {})
            enable_audit = audit_cfg.get(
                "enabled", self.review_config.get("enable_audit", False)
            )
        if enable_audit and issues:
            audit_result = self.reviewers["audit"].audit(issues, text)
            audit_suggestions = audit_result.get("audit_suggestions", {})
            # Apply audit suggestions to issues and keep verdict in sync.
            for idx, suggestion in audit_suggestions.items():
                if idx < len(issues):
                    issues[idx].suggestion = suggestion
                    issues[idx].verdict = _verdict_from_audit(issues[idx])
            review["audit"] = audit_result

        # Step 5: 复核开关 (final.enabled) — combine review opinions with the
        # text and produce the final repaired text. Only confirmed issues
        # (verdict) are applied, so 误报 filtered by review/audit are dropped;
        # grammar/semantic 漏报 found by review are included. Reads the final
        # config node's "enabled" flag; a legacy top-level
        # "enable_final_suggestion" key is honoured as a fallback.
        if enable_final_suggestion is None:
            final_cfg = self.review_config.get("final", {})
            enable_final_suggestion = final_cfg.get(
                "enabled", self.review_config.get("enable_final_suggestion", False)
            )
        final_suggestion = None
        if enable_final_suggestion:
            confirmed = [i for i in issues if _is_confirmed(i)]
            if confirmed:
                final_result = self.reviewers["final"].generate_final_text(
                    text, confirmed
                )
                final_suggestion = final_result.get("final_text", text)
            else:
                # Nothing confirmed -> text needs no change.
                final_suggestion = text

        # Whether the input has any detectable problem.
        has_issues = bool(typos) or bool(sensitive_hits)

        return {
            "original": text,
            "corrected_text": corrected_text,
            "has_issues": has_issues,
            "typos": typos,
            "sensitive_words": sensitive_hits,
            "issues": issues,
            "review_suggestions": review_suggestions,
            "audit": review.get("audit"),
            "final_suggestion": final_suggestion,
        }
