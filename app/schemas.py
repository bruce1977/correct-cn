"""Pydantic models for API request and response validation."""

from typing import List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 1. Typo / error correction
# --------------------------------------------------------------------------- #
class CorrectRequest(BaseModel):
    """Request body for the correction endpoint."""

    text: str = Field(..., description="Text to be corrected (multi-line allowed).")


class ErrorLocation(BaseModel):
    """A single corrected character / word with its position."""

    line: int = Field(..., description="1-based line number in the input.")
    start: int = Field(..., description="0-based start index within the line.")
    end: int = Field(..., description="0-based exclusive end index within the line.")
    original: str = Field(..., description="Original (wrong) substring.")
    corrected: str = Field(..., description="Suggested correction.")


class CorrectResponse(BaseModel):
    """Response of the correction endpoint."""

    original: str
    corrected: str
    errors: List[ErrorLocation]
    model: str


# --------------------------------------------------------------------------- #
# 2. Sensitive-word detection
# --------------------------------------------------------------------------- #
class SensitiveCheckRequest(BaseModel):
    """Request body for sensitive-word checking."""

    text: str = Field(..., description="Text to scan.")
    categories: Optional[List[str]] = Field(
        None, description="Restrict to these categories; None means all."
    )


class SensitiveHit(BaseModel):
    """A single sensitive-word occurrence."""

    word: str
    category: str
    line: int
    start: int
    end: int


class SensitiveCheckResponse(BaseModel):
    """Response of the sensitive-word check endpoint."""

    is_sensitive: bool
    count: int
    sensitive_words: List[SensitiveHit]


class SensitiveRefreshRequest(BaseModel):
    """Request body for refreshing the sensitive-word dictionaries."""

    remote: bool = Field(
        True, description="Fetch from remote source when True, else reload from disk."
    )
    remote_base: Optional[str] = Field(
        None, description="Override the remote base URL."
    )
    files: Optional[List[str]] = Field(
        None, description="Specific files to fetch; None means the known set."
    )
    force: bool = Field(
        False, description="Re-download even files that already exist locally."
    )


class SensitiveRefreshResponse(BaseModel):
    """Response of the refresh endpoint."""

    updated: List[str]
    failed: List[dict]
    categories: List[str]
    total_words: int


# --------------------------------------------------------------------------- #
# 3. Grammar / wording review (local LLM via Ollama)
# --------------------------------------------------------------------------- #
class ReviewRequest(BaseModel):
    """Request body for the review endpoint."""

    text: str = Field(..., description="Text to be reviewed.")
    typos: Optional[List[ErrorLocation]] = Field(
        None, description="Typos detected by Step 1 (corrector)."
    )
    sensitive_hits: Optional[List[SensitiveHit]] = Field(
        None, description="Sensitive words detected by Step 2."
    )


class Issue(BaseModel):
    """A single validated issue from the review.

    ``verdict`` disambiguates a confirmed problem from a false positive so the
    pipeline can decide whether to apply the fix in the final text:
      - typo_confirmed / typo_rejected
      - sensitive_confirmed / sensitive_rejected
      - grammar_confirmed   (漏报: grammar / semantic error found by review)
    ``None`` means the parser could not assign a verdict (legacy / unknown).
    """

    type: str = Field(
        ...,
        description="'typo' | 'sensitive' | 'grammar' (grammar = 漏报 found by review).",
    )
    original: Optional[str] = Field(None, description="Original text (for typo/grammar).")
    corrected: Optional[str] = Field(None, description="Corrected text (for typo/grammar).")
    word: Optional[str] = Field(None, description="Sensitive word (for sensitive).")
    category: Optional[str] = Field(None, description="Category (for sensitive).")
    line: int = Field(..., description="1-based line number.")
    start: int = Field(..., description="0-based start index.")
    end: int = Field(..., description="0-based exclusive end index.")
    suggestion: str = Field("", description="Human-readable fix suggestion for this issue.")
    verdict: Optional[str] = Field(
        None,
        description=(
            "typo_confirmed / typo_rejected / sensitive_confirmed / "
            "sensitive_rejected / grammar_confirmed. None = unknown."
        ),
    )


class ReviewResponse(BaseModel):
    """Response of the review endpoint."""

    model: str
    reachable: bool = Field(..., description="Whether the Ollama endpoint answered.")
    suggestions: Optional[str] = Field(
        None, description="Raw LLM review text (human-readable 复核结论 + 漏报)."
    )
    issues: List[Issue] = Field(default_factory=list, description="Validated issues.")
    error: Optional[str] = Field(None, description="Error detail if the call failed.")


# --------------------------------------------------------------------------- #
# 4. Full pipeline
# --------------------------------------------------------------------------- #
class PipelineRequest(BaseModel):
    """Request body for the combined pipeline endpoint."""

    text: str = Field(..., description="Text to process end-to-end.")
    enable_audit: Optional[bool] = Field(
        None,
        description=(
            "Enable the audit step (Step 4) to re-validate review suggestions. "
            "None (default) falls back to config.json -> audit.enabled."
        ),
    )
    enable_final_suggestion: Optional[bool] = Field(
        None,
        description=(
            "Enable final text generation (Step 5). When true, generates the "
            "corrected text based on confirmed issues. "
            "None (default) falls back to config.json -> final.enabled."
        ),
    )


class PipelineResponse(BaseModel):
    """Response of the combined pipeline endpoint."""

    original: str
    corrected_text: str
    has_issues: bool = Field(
        ..., description="True when typos or sensitive words were found."
    )
    typos: List[ErrorLocation]
    sensitive_words: List[SensitiveHit]
    issues: List[Issue] = Field(
        default_factory=list,
        description=(
            "Validated issues from review: tool findings (typo/sensitive) plus "
            "漏报 (grammar) found by the LLM, each with a verdict + fix suggestion."
        ),
    )
    review_suggestions: Optional[str] = Field(
        None,
        description="Raw LLM review text (复核结论 + 漏报), for human inspection.",
    )
    audit: Optional[dict] = Field(
        None,
        description=(
            "Optional 二次校验 (re-validation) result, present only when the "
            "audit step ran (enable_audit=true)."
        ),
    )
    final_suggestion: Optional[str] = Field(
        None,
        description=(
            "Final corrected text (复核开关 / enable_final_suggestion=true). "
            "Repairs confirmed errors + grammar/semantic, without style polishing."
        ),
    )


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
class HealthResponse(BaseModel):
    """Service health / capability report."""

    status: str
    corrector_model: str
    corrector_mode: str
    ollama_model: str
    ollama_base_url: str
    ollama_reachable: bool
    categories_loaded: int
    sensitive_word_count: int
