"""FastAPI application entrypoint for the correct-cn service.

On startup the service:
  * ensures ``DATA_DIR`` exists and seeds it with the built-in sensitive-word
    dictionaries and the unified ``config.json`` (only when they are missing),
  * loads the API keys (``/data/.keys``) used to gate the API endpoints,
  * loads the sensitive-word engine (dictionaries are auto-discovered),
  * loads the MacBert correction model (heavy; failure is non-fatal),
  * prepares the Ollama reviewer and the combined pipeline.

API endpoints are protected by a simple API-key check (see :func:`require_api_key`).
When ``.keys`` is empty, the API is open. ``/health`` is always open.
"""

import json
import logging
import os
import shutil
import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import DEFAULT_CONFIG, Settings, load_config
from app.corrector import TextCorrector
from app.pipeline import TextPipeline
from app.reviewer import TextReviewer, build_step_reviewers
from app.schemas import (
    CorrectRequest,
    CorrectResponse,
    ErrorLocation,
    HealthResponse,
    PipelineRequest,
    PipelineResponse,
    ReviewRequest,
    ReviewResponse,
    SensitiveCheckRequest,
    SensitiveCheckResponse,
    SensitiveHit,
    SensitiveRefreshRequest,
    SensitiveRefreshResponse,
)
from app.sensitive import SensitiveEngine

# Directory that ships the read-only built-in assets inside the image.
DEFAULTS_DIR = Path(__file__).resolve().parent / "defaults"


def _load_api_keys(path: str) -> set:
    """Read API keys (one per line) from ``path`` into a set.

    ``#`` starts a comment and blank lines are ignored. If the file is missing it
    is created empty so the path always exists.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError:
        pass
    if not os.path.isfile(path):
        try:
            open(path, "a", encoding="utf-8").close()
        except OSError:
            return set()
    keys = set()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                keys.add(line)
    except OSError:
        pass
    return keys


def _seed_data_dir(data_dir: str) -> None:
    """Copy built-in dictionaries / config into ``data_dir`` if they are absent.

    This lets users mount an empty ``/data`` volume and get a working default
    dictionary and configuration on first boot.
    """
    os.makedirs(data_dir, exist_ok=True)

    # Sensitive-word dictionaries (auto-discovered at runtime by the engine).
    src_sensitive = DEFAULTS_DIR / "sensitive"
    dst_sensitive = Path(data_dir) / "sensitive"
    if not dst_sensitive.exists() or not any(dst_sensitive.glob("*.txt")):
        dst_sensitive.mkdir(parents=True, exist_ok=True)
        if src_sensitive.is_dir():
            for f in src_sensitive.glob("*.txt"):
                shutil.copy2(f, dst_sensitive / f.name)

    # Unified configuration file (corrector / sensitive / review nodes).
    dst_cfg = Path(data_dir) / "config.json"
    if not dst_cfg.exists():
        src_cfg = DEFAULTS_DIR / "config.json"
        if src_cfg.is_file():
            shutil.copy2(src_cfg, dst_cfg)
        else:
            dst_cfg.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    _seed_data_dir(settings.data_dir)
    config = load_config(settings)

    # API keys (open API when the key file is empty).
    app.state.api_keys = _load_api_keys(settings.api_keys_file)

    sensitive_dir = os.path.join(settings.data_dir, "sensitive")
    sensitive_engine = SensitiveEngine(
        sensitive_dir,
        case_insensitive=config["sensitive"].get("case_insensitive", False),
    )

    # The correction model is heavy; a load failure must not kill the service.
    # In "mock" mode no ML stack is loaded, so local API testing stays light.
    corrector = None
    corrector_error = None
    corrector_mode = config["corrector"].get("mode", "model")
    try:
        corrector = TextCorrector(
            config["corrector"]["model"], mode=corrector_mode
        )
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        corrector_error = str(exc)

    reviewer = TextReviewer(
        base_url=config["review"]["base_url"],
        model=config["review"]["model"],
        timeout=config["review"]["timeout"],
        config=config["review"],
        # ``load_config`` already merges the OLLAMA_API_KEY env default into the
        # step node's api_key when config.json omits it; settings is a backstop.
        api_key=config["review"].get("api_key") or settings.ollama_api_key,
    )
    # Per-step reviewers: review / audit / final each get their own TextReviewer
    # built from its config node, so Step 5 can point at an external LLM while
    # Steps 3/4 stay on the local Ollama. The /api/review endpoint uses the
    # review step's reviewer; the pipeline uses the whole map.
    step_reviewers = build_step_reviewers(config, settings.ollama_api_key)
    pipeline = TextPipeline(
        corrector, sensitive_engine, step_reviewers, review_config=config
    )

    app.state.settings = settings
    app.state.config = config
    app.state.sensitive_engine = sensitive_engine
    app.state.corrector = corrector
    app.state.corrector_error = corrector_error
    app.state.reviewer = step_reviewers["review"]
    app.state.pipeline = pipeline

    yield


app = FastAPI(
    title="correct-cn 中文文本校对服务",
    description=(
        "提供中文文本质量检查能力，包含三大功能：\n"
        "1. **中文错别字矫正** —— MacBertCorrector，返回每个纠正处的位置\n"
        "2. **中文敏感词过滤** —— 基于 Aho-Corasick 的高效多模式匹配\n"
        "3. **语法/用词审校** —— 调用本地大模型（Ollama）\n\n"
        "完整流程串联上述三步，返回综合修改意见。\n\n"
        "⚠️ Swagger 文档仅允许内网访问（私有IP段）。"
    ),
    version="1.2.2",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# --------------------------------------------------------------------------- #
# Middleware: restrict /docs, /redoc, /openapi.json to intranet only
# --------------------------------------------------------------------------- #
DOC_PATHS = {"/docs", "/redoc", "/openapi.json"}


def _is_private_ip(ip_str: str) -> bool:
    """Return True if the IP address belongs to a private/reserved range."""
    try:
        addr = ipaddress.ip_address(ip_str)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_link_local
    except ValueError:
        return False


@app.middleware("http")
async def restrict_docs_to_intranet(request: Request, call_next):
    if request.url.path in DOC_PATHS:
        client_ip = request.client.host if request.client else ""
        if not _is_private_ip(client_ip):
            return JSONResponse(
                status_code=403,
                content={"detail": "Swagger docs are only accessible from intranet (private IP)."},
            )
    return await call_next(request)


# --------------------------------------------------------------------------- #
# API-key guard
# --------------------------------------------------------------------------- #
def require_api_key(
    authorization: str = Header(None),
    x_api_key: str = Header(None),
) -> None:
    """Reject requests without a valid API key (when keys are configured).

    Accepts ``Authorization: Bearer <key>``, a bare ``Authorization: <key>``, or
    the ``X-API-Key: <key>`` header. When no keys are loaded the API stays open.
    """
    keys = getattr(app.state, "api_keys", set())
    if not keys:
        return
    provided = None
    if authorization:
        provided = (
            authorization[7:].strip()
            if authorization.lower().startswith("bearer ")
            else authorization.strip()
        )
    if not provided:
        provided = (x_api_key or "").strip()
    if provided in keys:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# --------------------------------------------------------------------------- #
# Health (open, no API key required)
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["System"])
def health():
    return {}


# --------------------------------------------------------------------------- #
# 1. Typo / error correction
# --------------------------------------------------------------------------- #
@app.post("/api/correct", response_model=CorrectResponse, dependencies=[Depends(require_api_key)], tags=["Correction"])
def correct(req: CorrectRequest):
    if app.state.corrector is None:
        raise HTTPException(
            status_code=503,
            detail=f"Correction model unavailable: {app.state.corrector_error}",
        )
    result = app.state.corrector.correct(req.text)
    return CorrectResponse(
        original=result["original"],
        corrected=result["corrected"],
        errors=[ErrorLocation(**e) for e in result["errors"]],
        model=app.state.config["corrector"]["model"],
    )


# --------------------------------------------------------------------------- #
# 2. Sensitive-word detection
# --------------------------------------------------------------------------- #
@app.post(
    "/api/sensitive/check",
    response_model=SensitiveCheckResponse,
    dependencies=[Depends(require_api_key)],
    tags=["Sensitive"],
)
def sensitive_check(req: SensitiveCheckRequest):
    engine: SensitiveEngine = app.state.sensitive_engine
    result = engine.check_text(req.text, req.categories)
    return SensitiveCheckResponse(
        is_sensitive=result["is_sensitive"],
        count=result["count"],
        sensitive_words=[SensitiveHit(**h) for h in result["sensitive_words"]],
    )


@app.get("/api/sensitive/dictionaries", dependencies=[Depends(require_api_key)], tags=["Sensitive"])
def sensitive_dictionaries():
    """List the auto-discovered dictionary files and their word counts."""
    engine: SensitiveEngine = app.state.sensitive_engine
    return {
        "directory": engine.data_dir,
        "count": len(engine.list_dictionaries()),
        "dictionaries": engine.list_dictionaries(),
    }


@app.post(
    "/api/sensitive/refresh",
    response_model=SensitiveRefreshResponse,
    dependencies=[Depends(require_api_key)],
    tags=["Sensitive"],
)
def sensitive_refresh(req: SensitiveRefreshRequest):
    engine: SensitiveEngine = app.state.sensitive_engine
    cfg = app.state.config
    if req.remote:
        base = req.remote_base or cfg["sensitive"]["remote_base"]
        summary = engine.refresh_from_remote(
            remote_base=base, files=req.files, force=req.force
        )
    else:
        engine.reload()
        summary = {
            "updated": [],
            "failed": [],
            "categories": engine.get_categories(),
            "total_words": engine.get_word_count(),
        }
    return SensitiveRefreshResponse(**summary)


# --------------------------------------------------------------------------- #
# 3. Grammar / wording review (local LLM)
# --------------------------------------------------------------------------- #
@app.post("/api/review", response_model=ReviewResponse, dependencies=[Depends(require_api_key)], tags=["Review"])
def review(req: ReviewRequest):
    reviewer: TextReviewer = app.state.reviewer
    result = reviewer.review(
        req.text,
        typos=req.typos,
        sensitive_hits=req.sensitive_hits,
    )
    logger.info("Review endpoint: issues_count=%d, error=%s", len(result.get("issues", [])), result.get("error"))
    return ReviewResponse(**result)


# --------------------------------------------------------------------------- #
# 4. Full pipeline
# --------------------------------------------------------------------------- #
@app.post(
    "/api/pipeline",
    response_model=PipelineResponse,
    dependencies=[Depends(require_api_key)],
    tags=["Pipeline"],
)
def pipeline(req: PipelineRequest):
    if app.state.corrector is None:
        raise HTTPException(
            status_code=503,
            detail=f"Correction model unavailable: {app.state.corrector_error}",
        )
    result = app.state.pipeline.run(
        req.text,
        enable_audit=req.enable_audit,
        enable_final_suggestion=req.enable_final_suggestion,
    )
    return PipelineResponse(
        original=result["original"],
        corrected_text=result["corrected_text"],
        has_issues=result["has_issues"],
        typos=result["typos"],
        sensitive_words=result["sensitive_words"],
        issues=result["issues"],
        review_suggestions=result.get("review_suggestions"),
        audit=result.get("audit"),
        final_suggestion=result["final_suggestion"],
    )


# --------------------------------------------------------------------------- #
# 5. API-key management
# --------------------------------------------------------------------------- #
@app.post("/api/keys/reload", dependencies=[Depends(require_api_key)], tags=["System"])
def keys_reload():
    """Re-read the API key file without restarting the service."""
    s: Settings = app.state.settings
    app.state.api_keys = _load_api_keys(s.api_keys_file)
    return {"loaded": len(app.state.api_keys)}
