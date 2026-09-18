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


class ReviewResponse(BaseModel):
    """Response of the review endpoint."""

    model: str
    reachable: bool = Field(..., description="Whether the Ollama endpoint answered.")
    suggestions: str = Field("", description="Model output / review suggestions.")
    error: Optional[str] = Field(None, description="Error detail if the call failed.")


# --------------------------------------------------------------------------- #
# 4. Full pipeline
# --------------------------------------------------------------------------- #
class PipelineRequest(BaseModel):
    """Request body for the combined pipeline endpoint."""

    text: str = Field(..., description="Text to process end-to-end.")
    enable_summary: Optional[bool] = Field(
        None,
        description=(
            "Force or disable the final summary LLM call. None (default) falls "
            "back to the config.json -> review.enable_summary setting."
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
    review: ReviewResponse
    summary: Optional[ReviewResponse] = Field(
        None,
        description=(
            "Result of the optional final summary LLM call. None when "
            "enable_summary is False or the model was unreachable."
        ),
    )
    final_suggestion: str


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
