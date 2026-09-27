"""Grammar / wording review using an LLM (local Ollama or an external API).

Two wire protocols are supported and selected by ``review.protocol``, so the
same review / audit / final-repair flow can run against a cheap local model
(the default) or an external provider:

* ``ollama`` (default) -- Ollama's native ``POST /api/generate`` API: the
  request carries ``system`` / ``options`` / ``think`` and the answer is read
  from the ``response`` field.
* ``openai`` -- any OpenAI-compatible ``POST /v1/chat/completions`` endpoint
  (OpenAI, DeepSeek, Moonshot/Kimi, Qwen DashScope compatible mode,
  SiliconFlow, vLLM, LM Studio, SGLang, and Ollama's own ``/v1`` shim). The
  request uses a ``messages`` array and the answer is read from
  ``choices[0].message.content``.

Communication is done with the standard-library :mod:`urllib` module to avoid
pulling in an extra HTTP client dependency. The endpoint, model name and
timeout are supplied by the caller (typically from settings).
"""

import json
import logging
import re
import time
import urllib.error
import urllib.request

from app.schemas import Issue

logger = logging.getLogger(__name__)

PROTOCOL_OLLAMA = "ollama"
PROTOCOL_OPENAI = "openai"

# Accepted spellings for the OpenAI-compatible protocol.
_OPENAI_ALIASES = {"openai", "openai_compatible", "openai-compatible", "oai", "v1"}


def normalize_protocol(value) -> str:
    """Map a user-supplied protocol string onto a known protocol constant.

    Anything unrecognised falls back to ``ollama`` so existing deployments keep
    working unchanged.
    """
    key = (value or "").strip().lower().replace(" ", "")
    return PROTOCOL_OPENAI if key in _OPENAI_ALIASES else PROTOCOL_OLLAMA


# --------------------------------------------------------------------------- #
# Per-step provider selection
# --------------------------------------------------------------------------- #
# The pipeline runs three LLM steps (review / audit / final). Each step has its
# own config node (review / audit / final) carrying its own protocol / base_url
# / model / api_key, so different steps can use different providers -- e.g. the
# cheap local Ollama for review & audit, and a stronger external model for the
# final repair (Step 5). No nested provider map is needed; we just build one
# TextReviewer per step node.
_STEP_NODES = ("review", "audit", "final")


def _reviewer_from_cfg(cfg: dict, fallback_api_key: str = "") -> "TextReviewer":
    """Build a :class:`TextReviewer` from a fully-resolved step config node."""
    api_key = cfg.get("api_key", "") or fallback_api_key
    return TextReviewer(
        base_url=cfg.get("base_url", "http://localhost:11434"),
        model=cfg.get("model", ""),
        timeout=int(cfg.get("timeout", 60)),
        config=cfg,
        api_key=api_key,
    )


def build_step_reviewers(config: dict, fallback_api_key: str = "") -> dict:
    """Build a ``{step: TextReviewer}`` map from the 5-node config.

    Args:
        config: the full top-level config (corrector / sensitive / review /
            audit / final nodes).
        fallback_api_key: env-provided key used only when a node has none.

    Each step falls back to the ``review`` node (then to an empty local-Ollama
    config) so an old 3-node config without ``audit`` / ``final`` nodes still
    routes every step to the local model and keeps working.
    """
    reviewers = {}
    for step in _STEP_NODES:
        node = (config or {}).get(step) or (config or {}).get("review") or {}
        reviewers[step] = _reviewer_from_cfg(node, fallback_api_key)
    return reviewers

# --- verdict parsing helpers (module-level, no state) --------------------- #
# The review prompt asks the LLM to emit, per finding, a line like:
#   1. 【确认修改】原文=「你号」建议=「你好」理由=人称问候常见错别字
# and a separate 【漏报】 section for problems the tools missed.
_TAG_RE = re.compile(r"\[(确认修改|非错误|确认敏感|非敏感)\]")
_BARE_TAG_RE = re.compile(r"(确认修改|非错误|确认敏感|非敏感)")
_NUM_RE = re.compile(r"^\s*(\d+)\s*[\.、]")
_REASON_RE = re.compile(r"理由=([^\n【】]+)")
# Canonical suggestion format is bracketed: 建议=「...」 (see config output_format).
# The closing 「」 brackets are required so the greedy capture can't stop early
# (a non-greedy capture with optional brackets matched only "你" inside "你好").
_SUGGEST_RE = re.compile(r"建议=「([^」\n]*)」")
# Fallback for models that occasionally drop the brackets: capture up to the
# trailing 理由= / 类别= field or the line end.
_SUGGEST_PLAIN_RE = re.compile(r"建议=([^\n【】理由]+)")
# 漏报 line: 类型=语法 原文=「...」建议=「...」理由=...
# The 「」 brackets are required so the non-greedy captures can't truncate
# (e.g. "纳闷" -> "纳") when the trailing optional groups are absent.
_MISS_RE = re.compile(
    r"类型=(错别字|敏感词|语法|语义)\s*"
    r"原文=「([^」\n]+)」\s*"
    r"(?:建议=「([^」\n]+)」)?\s*"
    r"(?:理由=([^\n【】]+))?"
)
_POS_RE = re.compile(r"位置=(\d+):(\d+)-(\d+)")

# Map a 漏报 类型 to an Issue.type / verdict.
_MISS_TYPE_MAP = {
    "错别字": ("typo", "typo_confirmed"),
    "敏感词": ("sensitive", "sensitive_confirmed"),
    "语法": ("grammar", "grammar_confirmed"),
    "语义": ("grammar", "grammar_confirmed"),
}


def _extract_indexed_verdicts(response_text: str, count: int):
    """Map checklist index -> (tag, reason, suggested).

    Parses lines such as ``1. 【确认修改】原文=「你号」建议=「你好」理由=...``.
    Only indices ``0..count-1`` are accepted so the 【漏报】 section (which is
    also numbered) is ignored here and parsed separately.
    """
    out = {}
    for line in response_text.splitlines():
        m = _NUM_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= count:
            continue
        tag_m = _TAG_RE.search(line) or _BARE_TAG_RE.search(line)
        if not tag_m:
            continue
        reason_m = _REASON_RE.search(line)
        sugg_m = _SUGGEST_RE.search(line) or _SUGGEST_PLAIN_RE.search(line)
        out[idx] = (
            tag_m.group(1),
            reason_m.group(1).strip() if reason_m else "",
            (sugg_m.group(1).strip().strip("「」") if sugg_m else "") or "",
        )
    return out


def _format_typo(original: str, corrected: str, tag: str, reason: str, suggested: str):
    """Build (suggestion, verdict) for a typo checklist item."""
    if tag == "确认修改":
        corr = suggested or corrected or ""
        sug = f"确认修改：建议改为「{corr}」" if corr else "确认修改"
        if reason:
            sug += f"（{reason}）"
        return sug, "typo_confirmed"
    r = f"（{reason}）" if reason else ""
    return f"经验证，「{original}」在上下文中用法正确{r}", "typo_rejected"


def _format_sensitive(word: str, tag: str, reason: str, suggested: str):
    """Build (suggestion, verdict) for a sensitive checklist item."""
    if tag == "确认敏感":
        handling = suggested or "需处理"
        sug = f"确认敏感词：{handling}" if handling else "确认敏感词，需处理"
        if reason:
            sug += f"（{reason}）"
        return sug, "sensitive_confirmed"
    r = f"（{reason}）" if reason else ""
    return f"经验证，「{word}」在上下文中为正常用法{r}", "sensitive_rejected"


def _extract_missed_issues(response_text: str):
    """Parse the 【漏报】 section into Issue objects (errors the tools missed)."""
    issues = []
    # Locate the 【漏报】 block; if absent, there is nothing to parse.
    idx = response_text.find("【漏报】")
    if idx == -1:
        # Fall back to the "二、" header some models emit.
        idx = response_text.find("二、")
    if idx == -1:
        return issues
    block = response_text[idx:]
    for line in block.splitlines():
        m = _MISS_RE.search(line)
        if not m:
            continue
        miss_type, orig, sugg, reason = m.groups()
        itype, verdict = _MISS_TYPE_MAP.get(miss_type, ("grammar", "grammar_confirmed"))
        orig = (orig or "").strip().strip("「」")
        sugg = (sugg or "").strip().strip("「」")
        reason = (reason or "").strip()
        # Best-effort positions (default to whole-text if absent).
        pos = _POS_RE.search(line)
        if pos:
            line_no, start, end = int(pos.group(1)), int(pos.group(2)), int(pos.group(3))
        else:
            line_no, start, end = 1, 0, len(orig)
        if itype == "typo":
            suggestion = f"漏报错别字：建议改为「{sugg}」" if sugg else "漏报错别字"
            issues.append(Issue(
                type="typo", original=orig, corrected=sugg,
                line=line_no, start=start, end=end,
                suggestion=(suggestion + (f"（{reason}）" if reason else "")),
                verdict="typo_confirmed",
            ))
        elif itype == "sensitive":
            suggestion = f"漏报敏感词：{sugg or '需处理'}"
            issues.append(Issue(
                type="sensitive", word=orig, category="漏报",
                line=line_no, start=start, end=end,
                suggestion=(suggestion + (f"（{reason}）" if reason else "")),
                verdict="sensitive_confirmed",
            ))
        else:  # grammar / semantic
            suggestion = f"语法/语义修正：建议改为「{sugg}」" if sugg else "语法/语义问题"
            issues.append(Issue(
                type="grammar", original=orig, corrected=sugg,
                line=line_no, start=start, end=end,
                suggestion=(suggestion + (f"（{reason}）" if reason else "")),
                verdict="grammar_confirmed",
            ))
    return issues


class TextReviewer:
    """Reviews Chinese text through a local Ollama or an OpenAI-compatible API.

    The wire protocol is chosen by the ``protocol`` config key (``ollama`` by
    default, ``openai`` for any OpenAI-compatible endpoint). Everything above
    the transport -- prompt assembly, verdict parsing, retries -- is shared.
    """

    def __init__(self, base_url: str, model: str, timeout: int = 60, config: dict = None, api_key: str = ""):
        # Normalise the base URL; the request path is built per protocol.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.config = config or {}
        # An explicit argument wins, then the config key (which the OLLAMA_API_KEY
        self.api_key = api_key or self.config.get("api_key", "") or ""
        self.protocol = normalize_protocol(self.config.get("protocol", PROTOCOL_OLLAMA))
        # Retry on transient failures (connection error / timeout / 5xx / 429).
        # 4xx client errors (bad request, auth) are NOT retried.
        self.max_retries = int(self.config.get("max_retries", 2))

    # ------------------------------------------------------------------ #
    # Protocol-specific request/response plumbing
    # ------------------------------------------------------------------ #
    def _chat_url(self) -> str:
        """Return the request URL for the configured protocol.

        For the OpenAI protocol the base URL may be given as a bare host
        (``https://api.deepseek.com``), with a version suffix
        (``.../compatible-mode/v1``) or as the full completions path; all three
        forms are normalised to ``.../v1/chat/completions``.
        """
        if self.protocol == PROTOCOL_OPENAI:
            if self.base_url.endswith("/chat/completions"):
                return self.base_url
            if self.base_url.endswith("/v1"):
                return self.base_url + "/chat/completions"
            return self.base_url + "/v1/chat/completions"
        return self.base_url + "/api/generate"

    def _models_url(self) -> str:
        """Return the OpenAI-compatible ``/v1/models`` URL (probe endpoint)."""
        base = self.base_url
        if base.endswith("/chat/completions"):
            base = base[: -len("/chat/completions")]
        if not base.endswith("/v1"):
            base = base + "/v1"
        return base + "/models"

    def _build_payload(self, system: str, prompt: str) -> dict:
        """Build the protocol-specific request body.

        The two payloads are kept strictly separate: OpenAI-compatible servers
        reject unknown parameters (Ollama's ``options`` / ``think`` / prompt
        fields) with HTTP 400, and Ollama ignores the ``messages`` array.
        """
        temperature = self.config.get("temperature", 0.3)
        if self.protocol == PROTOCOL_OPENAI:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": temperature,
            }
            max_tokens = self.config.get("max_tokens")
            if max_tokens:
                # OpenAI's newest models require "max_completion_tokens"; every
                # other compatible server accepts "max_tokens". Overridable.
                payload[self.config.get("max_tokens_param", "max_tokens")] = int(max_tokens)
            # Escape hatch for provider-specific knobs, e.g. {"top_p": 0.9}.
            extra = self.config.get("extra_body")
            if isinstance(extra, dict):
                payload.update(extra)
            return payload
        return {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "think": bool(self.config.get("think", False)),
            "options": {
                "temperature": temperature,
                "num_predict": self.config.get("max_tokens", 4096),
                "num_ctx": self.config.get("num_ctx", 8192),
            },
        }

    def _extract_text(self, body: dict) -> str:
        """Pull the model's answer text out of a protocol-specific body."""
        if self.protocol == PROTOCOL_OPENAI:
            choices = body.get("choices") or []
            if not choices:
                return ""
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            if not content:
                # Reasoning models expose the chain in a sibling field
                # (DeepSeek: reasoning_content, others: reasoning). Fall back to
                # it so the caller still gets usable output.
                content = message.get("reasoning_content") or message.get("reasoning") or ""
            return content or ""
        text = body.get("response", "")
        # Thinking models (e.g. qwen3.5) put reasoning in "thinking" and the
        # final answer in "response". If response is empty, fall back so the
        # caller still gets useful output.
        if not text and body.get("thinking"):
            logger.info("Response empty, falling back to thinking field (len=%d)", len(body["thinking"]))
            text = body["thinking"]
        return text

    def _extract_error(self, body: dict) -> str:
        """Return a readable error message from a response body ("" if none).

        Handles both shapes: Ollama's ``{"error": "..."}`` string and the
        OpenAI style ``{"error": {"message": "...", "type": "..."}}`` object.
        """
        err = body.get("error")
        if not err:
            return ""
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err, ensure_ascii=False)
        return str(err)

    def _finish_reason(self, body: dict) -> str:
        """Return the provider's stop reason, or "" when the protocol omits it."""
        if self.protocol == PROTOCOL_OPENAI:
            choices = body.get("choices") or []
            return (choices[0].get("finish_reason") or "") if choices else ""
        return body.get("done_reason", "") or ""

    def _build_prompt(self, text: str) -> tuple:
        """Compose the (system, prompt) pair from the review config."""
        system = self.config.get("system_prompt", "")
        user_template = self.config.get("user_template", "{text}")
        output_format = self.config.get("output_format", "")
        prompt = user_template.replace("{text}", text)
        if output_format:
            prompt = prompt + "\n\n" + output_format
        return system, prompt

    def _generate(self, system: str, prompt: str) -> dict:
        """Send a single generation request and return a normalised result dict.

        Works for both protocols: the request body / URL come from
        :meth:`_build_payload` and :meth:`_chat_url`, the answer is read by
        :meth:`_extract_text`. The returned dict always contains ``model`` and
        ``reachable``. On success ``suggestions`` holds the model output; on
        failure ``error`` describes what went wrong and ``suggestions`` is empty.
        """
        payload = self._build_payload(system, prompt)
        url = self._chat_url()
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
        logger.info(
            "LLM request: protocol=%s model=%s url=%s prompt_len=%d",
            self.protocol, self.model, url, len(prompt),
        )

        # --- Retry loop: tolerate a flaky / non-deterministic model server. ---
        # Retried: connection errors, timeouts, HTTP 5xx, and 429 (rate limit).
        # NOT retried: HTTP 4xx (except 429) which are client/config errors, and
        # body-level errors (same request would fail again).
        raw = None
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                break  # network OK -> process the body below
            except urllib.error.HTTPError as exc:
                body_text = ""
                try:
                    body_text = exc.read().decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
                # 4xx (except 429 rate-limit) are client/config errors: no retry.
                if exc.code and 400 <= exc.code < 500 and exc.code != 429:
                    logger.error("LLM HTTP %s (client error, no retry): %s", exc.code, body_text[:500])
                    return {
                        "model": self.model,
                        "reachable": False,
                        "suggestions": "",
                        "error": f"LLM HTTP {exc.code}: {body_text[:500]}",
                    }
                last_error = f"LLM HTTP {exc.code}: {body_text[:500]}"
            except urllib.error.URLError as exc:
                last_error = f"LLM unreachable: {exc.reason if hasattr(exc, 'reason') else str(exc)}"
            except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
                logger.exception("LLM request failed: protocol=%s model=%s url=%s", self.protocol, self.model, url)
                last_error = str(exc)
            # Exponential backoff (capped at 8s) before the next attempt.
            if attempt < self.max_retries:
                backoff = min(2 ** attempt, 8)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s -- retrying in %ds",
                    attempt + 1, self.max_retries + 1, last_error, backoff,
                )
                time.sleep(backoff)
        else:
            # All attempts exhausted without a successful response.
            logger.error("LLM unavailable after %d attempts: %s", self.max_retries + 1, last_error)
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": last_error or "unknown error",
            }

        # --- Process a successful (HTTP 200) response. ---
        logger.info("LLM raw response (first 500 chars): %s", raw[:500])
        try:
            body = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("LLM returned non-JSON response: %s", raw[:200])
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": f"invalid JSON from {self.protocol}: {exc}",
            }
        # Servers may return HTTP 200 but include an error in the body. This is a
        # model-level error (e.g. context too long, model not loaded, quota
        # exceeded) -> surface it, but do NOT retry because the same request
        # would fail again.
        body_error = self._extract_error(body)
        if body_error:
            logger.error("LLM returned error in body: model=%s error=%s", self.model, body_error)
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": body_error,
            }
        suggestions = self._extract_text(body)
        finish_reason = self._finish_reason(body)
        if finish_reason == "length":
            # The output was cut off by the token cap; a common cause of a
            # truncated final-repair text on long inputs.
            logger.warning(
                "LLM output hit the token limit (finish_reason=length); "
                "the response may be truncated -- consider raising max_tokens",
            )
        logger.info(
            "LLM response: model=%s response_len=%d finish_reason=%s",
            self.model, len(suggestions), finish_reason or "-",
        )
        if not suggestions:
            logger.warning("LLM returned empty response. Raw body keys: %s", list(body.keys()))
        return {
            "model": self.model,
            "reachable": True,
            "suggestions": suggestions,
            "error": None,
        }

    def _parse_issues_from_response(
        self,
        response_text: str,
        typos: list,
        sensitive_hits: list,
    ) -> list:
        """Parse the LLM review response into structured Issue objects.

        Strategy (robust, order-based):
          * The prompt lists every tool finding as a numbered 【待复核清单】.
            We parse the model's per-item verdict by that index, so a clean
            mapping survives even when the free-form text is noisy.
          * If an index has no parseable verdict, we fall back to the older
            fuzzy heuristics (``_extract_typo_suggestion`` / ``_extract_sensitive_suggestion``).
          * Problems the tools missed (漏报) are parsed from the 【漏报】 section
            into extra Issue objects (typo / sensitive / grammar).
        """
        issues = []
        order = [("typo", t) for t in (typos or [])] + [
            ("sensitive", h) for h in (sensitive_hits or [])
        ]
        verdicts = _extract_indexed_verdicts(response_text, len(order))

        for i, (kind, obj) in enumerate(order):
            v = verdicts.get(i)
            if v:
                tag, reason, suggested = v
                if kind == "typo":
                    suggestion, verdict = _format_typo(
                        obj.original, obj.corrected, tag, reason, suggested
                    )
                else:
                    suggestion, verdict = _format_sensitive(
                        obj.word, tag, reason, suggested
                    )
            else:
                # Fuzzy fallback (preserves pre-existing behaviour).
                if kind == "typo":
                    suggestion = self._extract_typo_suggestion(response_text, obj)
                    verdict = (
                        "typo_rejected" if "非错误" in suggestion else "typo_confirmed"
                    )
                else:
                    suggestion = self._extract_sensitive_suggestion(response_text, obj)
                    verdict = (
                        "sensitive_rejected"
                        if "非敏感" in suggestion
                        else "sensitive_confirmed"
                    )
            if kind == "typo":
                issues.append(Issue(
                    type="typo",
                    original=obj.original,
                    corrected=obj.corrected,
                    line=obj.line,
                    start=obj.start,
                    end=obj.end,
                    suggestion=suggestion,
                    verdict=verdict,
                ))
            else:
                issues.append(Issue(
                    type="sensitive",
                    word=obj.word,
                    category=obj.category,
                    line=obj.line,
                    start=obj.start,
                    end=obj.end,
                    suggestion=suggestion,
                    verdict=verdict,
                ))

        # 漏报: problems the tools missed, found by the LLM on its own.
        issues.extend(_extract_missed_issues(response_text))
        return issues

    def _extract_typo_suggestion(self, response_text: str, typo) -> str:
        """Extract the suggestion for a typo from the LLM response."""
        # Look for patterns indicating the typo is not an error
        # Try to find the typo word in the response and check surrounding context
        word = typo.original

        # Find all occurrences of the word in the response
        for match in re.finditer(re.escape(word), response_text):
            start_pos = match.start()
            # Look at context before and after (up to 100 chars each)
            context_before = response_text[max(0, start_pos - 100):start_pos]
            context_after = response_text[start_pos + len(word):min(len(response_text), start_pos + len(word) + 100)]
            context = context_before + word + context_after

            # Check for non-error patterns in the surrounding context
            non_error_indicators = [
                "非错误", "不是错误", "非错别字", "不是错别字",
                "用法正确", "无需修改", "不需要修改", "正确用法",
                "正常使用", "常规用法", "可接受",
            ]
            for indicator in non_error_indicators:
                if indicator in context:
                    # Extract reason if present
                    reason_match = re.search(r"原因[：:]\s*(.+?)(?:[。；\n]|$)", context)
                    reason = reason_match.group(1).strip() if reason_match else ""
                    if reason:
                        return f"经验证，「{word}」在上下文中用法正确（{reason}）"
                    return f"经验证，「{word}」在上下文中用法正确"

            # Check for confirmation patterns
            confirm_indicators = [
                "确认修改", "需要修改", "建议修改", "应改为",
                "→", "修改为", "改为",
            ]
            for indicator in confirm_indicators:
                if indicator in context:
                    # Try to extract the suggestion
                    suggestion_match = re.search(
                        rf"「?{re.escape(word)}」?\s*(?:→|修改为|改为|建议修改为?)\s*[「」]?([^」\n,，。]+)",
                        context,
                    )
                    if suggestion_match:
                        return f"确认修改: {suggestion_match.group(1).strip()}"
                    return "确认修改"

        # Default: if no specific pattern found, return generic message
        return "确认修改"

    def _extract_sensitive_suggestion(self, response_text: str, hit) -> str:
        """Extract the suggestion for a sensitive word from the LLM response."""
        word = hit.word

        # Find all occurrences of the word in the response
        for match in re.finditer(re.escape(word), response_text):
            start_pos = match.start()
            # Look at context before and after (up to 150 chars each)
            context_before = response_text[max(0, start_pos - 150):start_pos]
            context_after = response_text[start_pos + len(word):min(len(response_text), start_pos + len(word) + 150)]
            context = context_before + word + context_after

            # Check for non-sensitive patterns in the surrounding context
            non_sensitive_indicators = [
                "非敏感", "不是敏感词", "正常用法", "无需处理",
                "不需要处理", "正常使用", "常规用法", "可接受",
                "合理使用", "恰当使用", "语境中正常",
                "上下文中正常", "非违规",
            ]
            for indicator in non_sensitive_indicators:
                if indicator in context:
                    # Extract reason if present
                    reason_match = re.search(r"原因[：:]\s*(.+?)(?:[。；\n]|$)", context)
                    reason = reason_match.group(1).strip() if reason_match else ""
                    if reason:
                        return f"经验证，「{word}」在上下文中是正常用法（{reason}）"
                    return f"经验证，「{word}」在上下文中是正常用法"

            # Check for confirmation patterns (word IS sensitive)
            confirm_indicators = [
                "确认敏感", "敏感词", "需要处理", "建议处理",
                "应删除", "建议删除", "需删除", "需替换",
            ]
            for indicator in confirm_indicators:
                if indicator in context:
                    # Try to extract the suggestion
                    suggestion_match = re.search(
                        rf"「?{re.escape(word)}」?\s*(?:建议|应|需|需要)\s*(.+?)(?:[。；\n]|$)",
                        context,
                    )
                    if suggestion_match:
                        return f"确认敏感词: {suggestion_match.group(1).strip()}"
                    return "确认敏感词，需根据上下文处理"

        # Default: if no specific pattern found, return generic message
        return "需根据上下文判断"

    def review(
        self,
        text: str,
        original_text: str = None,
        typos: list = None,
        sensitive_hits: list = None,
    ) -> dict:
        """Review ``text`` and return a result dict with structured issues.

        The review step is a 复核 (re-check) of the tool findings: it validates
        each typo / sensitive hit (filtering 误报) and actively hunts for 漏报
        (problems the tools missed), proposing fix suggestions for everything
        that is genuinely wrong.

        When ``typos`` / ``sensitive_hits`` are supplied, the prompt builds a
        numbered 【待复核清单】 so the model echoes verdicts in order and the
        parser can map them back by index.
        """
        system = self.config.get("system_prompt", "")
        base = self.config.get("user_template", "请审校以下文本：\n{text}").replace("{text}", text)
        output_format = self.config.get("output_format", "")
        typos = typos or []
        sensitive_hits = sensitive_hits or []
        findings = list(typos) + list(sensitive_hits)

        blocks = [base]
        if findings:
            # Build the numbered checklist (typos first, then sensitive). The
            # model is asked to echo a verdict per item in the same order.
            checklist = []
            n = 0
            for t in typos:
                n += 1
                checklist.append(
                    f"{n}. [错别字] 原文=「{t.original}」建议=「{t.corrected}」"
                    f"（第{t.line}行，位置{t.start}-{t.end}）"
                )
            for h in sensitive_hits:
                n += 1
                checklist.append(
                    f"{n}. [敏感词] 原文=「{h.word}」（{h.category}，"
                    f"第{h.line}行，位置{h.start}-{h.end}）"
                )
            if original_text is not None and original_text != text:
                blocks.append(f"\n\n【原始文本（纠错前）】\n{original_text}")
            blocks.append("\n\n【待复核清单】\n" + "\n".join(checklist))
            blocks.append(
                "\n\n请结合全文语境，对清单中每一条按 output_format 要求逐一复核，"
                "编号须与清单严格一致；并主动扫描全文找出漏报。"
            )
            if output_format:
                blocks.append("\n" + output_format)
        else:
            # Standalone review: no tool findings to validate, so ask for a full
            # 漏报 pass over the text.
            blocks.append(
                "\n\n（本次未提供工具检测结果，请对全文主动审校，按 output_format 的"
                "【漏报】格式输出发现的错别字 / 敏感词 / 语法语义问题；如无误报可跳过【复核结论】。）"
            )
            if output_format:
                blocks.append("\n" + output_format)
        prompt = "".join(blocks)

        result = self._generate(system, prompt)

        # Parse the response into structured issues (always, when reachable).
        if result["reachable"]:
            result["issues"] = self._parse_issues_from_response(
                result["suggestions"], typos, sensitive_hits
            )
        else:
            result["issues"] = []

        return result

    def audit(self, issues: list, original_text: str) -> dict:
        """Re-validate each issue's suggestion via a second LLM call.

        Returns a result dict with ``audit_suggestions`` mapping issue
        indices to their audit results.
        """
        template = self.config.get("audit_prompt")
        if not template:
            return {
                "model": self.model,
                "reachable": False,
                "audit_suggestions": {},
                "error": "audit_prompt is not configured",
            }

        # Build the issue list for the prompt
        issue_lines = []
        for i, issue in enumerate(issues):
            if issue.type == "typo":
                issue_lines.append(
                    f"{i+1}. 错别字：「{issue.original}」→「{issue.corrected}」"
                    f"（第{issue.line}行，位置{issue.start}-{issue.end}）"
                    f" 建议：{issue.suggestion}"
                )
            else:
                issue_lines.append(
                    f"{i+1}. 敏感词：「{issue.word}」（{issue.category}）"
                    f"（第{issue.line}行，位置{issue.start}-{issue.end}）"
                    f" 建议：{issue.suggestion}"
                )

        prompt = template
        prompt = prompt.replace("{issues}", "\n".join(issue_lines))
        prompt = prompt.replace("{original_text}", original_text)

        system = self.config.get("system_prompt", "")
        result = self._generate(system, prompt)

        # Parse audit suggestions from response
        audit_suggestions = {}
        if result["reachable"]:
            for i, issue in enumerate(issues):
                # Simple heuristic: look for the issue number and confirmation
                pattern = f"{i+1}[.、].*(?:确认|保留|需要修改)"
                if re.search(pattern, result["suggestions"]):
                    audit_suggestions[i] = issue.suggestion
                else:
                    # Check for rejection patterns
                    reject_pattern = f"{i+1}[.、].*(?:非错误|非敏感|无需修改|不需要)"
                    if re.search(reject_pattern, result["suggestions"]):
                        audit_suggestions[i] = f"经复核，此条无需修改"
                    else:
                        audit_suggestions[i] = issue.suggestion

        result["audit_suggestions"] = audit_suggestions
        return result

    def generate_final_text(
        self, original_text: str, issues: list
    ) -> dict:
        """Generate the final corrected text based on confirmed issues.

        Returns a result dict with ``final_text`` containing the corrected text.
        """
        template = self.config.get("final_prompt")
        if not template:
            return {
                "model": self.model,
                "reachable": False,
                "final_text": original_text,
                "error": "final_prompt is not configured",
            }

        # Build the confirmed issues list
        issue_lines = []
        for i, issue in enumerate(issues):
            if issue.type == "typo":
                issue_lines.append(
                    f"- 第{issue.line}行，位置{issue.start}-{issue.end}："
                    f"错别字「{issue.original}」→「{issue.corrected}」"
                )
            elif issue.type == "sensitive":
                issue_lines.append(
                    f"- 第{issue.line}行，位置{issue.start}-{issue.end}："
                    f"敏感词「{issue.word}」（{issue.category}）需处理"
                )
            else:  # grammar / semantic 漏报
                corr = f"→「{issue.corrected}」" if issue.corrected else ""
                issue_lines.append(
                    f"- 第{issue.line}行，位置{issue.start}-{issue.end}："
                    f"语法/语义修正「{issue.original}」{corr}"
                )

        prompt = template
        prompt = prompt.replace("{original_text}", original_text)
        prompt = prompt.replace("{issues}", "\n".join(issue_lines) if issue_lines else "无")

        system = self.config.get("system_prompt", "")
        result = self._generate(system, prompt)

        # Extract final text from response
        if result["reachable"]:
            # Try to extract text after an explicit marker; otherwise use the
            # whole response (the final_prompt asks the model to output the
            # corrected text directly).
            text_match = re.search(
                r"(?:修正后的完整文本|修改后的文本|最终文本|完整文本)[：:]\s*\n(.*?)(?:\n\n|\Z)",
                result["suggestions"],
                re.DOTALL,
            )
            if text_match:
                result["final_text"] = text_match.group(1).strip()
            else:
                # Use the full response as the final text
                result["final_text"] = result["suggestions"].strip()
            # Never return an empty fix; fall back to the original text so the
            # pipeline always yields a usable string.
            if not result["final_text"]:
                result["final_text"] = original_text
        else:
            result["final_text"] = original_text

        return result

    def is_reachable(self) -> bool:
        """Probe the configured endpoint.

        For the OpenAI protocol this is a cheap ``GET /v1/models`` (no tokens
        spent); for Ollama it is a tiny ``/api/generate`` request.
        """
        if self.protocol == PROTOCOL_OPENAI:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            req = urllib.request.Request(self._models_url(), headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.status == 200
            except Exception:  # noqa: BLE001
                return False
        probe = {
            "model": self.model,
            "prompt": "ping",
            "stream": False,
            "think": False,
            "options": {"num_predict": 1},
        }
        url = self._chat_url()
        data = json.dumps(probe).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            url, data=data, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status == 200
        except Exception:  # noqa: BLE001
            return False
