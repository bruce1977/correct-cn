"""Grammar / wording review using a local LLM exposed by Ollama.

Communication with Ollama is done with the standard-library :mod:`urllib`
module to avoid pulling in an extra HTTP client dependency. The endpoint,
model name and timeout are supplied by the caller (typically from settings).
"""

import json
import logging
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class TextReviewer:
    """Calls an Ollama ``/api/generate`` endpoint to review Chinese text."""

    def __init__(self, base_url: str, model: str, timeout: int = 60, config: dict = None, api_key: str = ""):
        # Normalise the base URL and append the generate path.
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.config = config or {}
        self.api_key = api_key

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
        """Send a single generate request to Ollama and return a result dict.

        The returned dict always contains ``model`` and ``reachable``. On
        success ``suggestions`` holds the model output; on failure ``error``
        describes what went wrong and ``suggestions`` is empty.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "think": False,
            "options": {
                "temperature": self.config.get("temperature", 0.3),
                "num_predict": self.config.get("max_tokens", 4096),
                "num_ctx": self.config.get("num_ctx", 8192),
            },
        }
        url = self.base_url + "/api/generate"
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
        logger.info("Ollama request: model=%s url=%s prompt_len=%d", self.model, url, len(prompt))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
            logger.info("Ollama raw response (first 500 chars): %s", raw[:500])
            body = json.loads(raw)
            # Ollama may return HTTP 200 but include an error in the body.
            if "error" in body:
                logger.error("Ollama returned error in body: model=%s error=%s", self.model, body["error"])
                return {
                    "model": self.model,
                    "reachable": False,
                    "suggestions": "",
                    "error": body["error"],
                }
            suggestions = body.get("response", "")
            # Thinking models (e.g. qwen3.5) output reasoning in "thinking" field
            # and the final answer in "response". If response is empty, fall back
            # to thinking content so the caller still gets useful output.
            if not suggestions and body.get("thinking"):
                logger.info("Response empty, falling back to thinking field (len=%d)", len(body["thinking"]))
                suggestions = body["thinking"]
            logger.info("Ollama response: model=%s response_len=%d", self.model, len(suggestions))
            if not suggestions:
                logger.warning("Ollama returned empty response. Raw body keys: %s", list(body.keys()))
            return {
                "model": self.model,
                "reachable": True,
                "suggestions": suggestions,
                "error": None,
            }
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
            error_msg = f"Ollama HTTP {exc.code}: {body_text[:500]}"
            logger.error("Ollama request failed: model=%s url=%s %s", self.model, url, error_msg)
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": error_msg,
            }
        except urllib.error.URLError as exc:
            error_msg = f"Ollama unreachable: {exc.reason if hasattr(exc, 'reason') else str(exc)}"
            logger.error("Ollama request failed: model=%s url=%s error=%s", self.model, url, error_msg)
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": error_msg,
            }
        except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
            logger.exception("Ollama request failed: model=%s url=%s", self.model, url)
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": str(exc),
            }

    def review(
        self,
        text: str,
        original_text: str = None,
        typos: list = None,
        sensitive_hits: list = None,
    ) -> dict:
        """Review ``text`` and return a result dict (see :meth:`_generate`).

        When ``original_text`` / ``typos`` / ``sensitive_hits`` are provided the
        prompt includes the full correction context so the LLM can make an
        informed recommendation.
        """
        system, prompt = self._build_prompt(text)
        # Enrich the prompt with pipeline context when available.
        if original_text is not None and original_text != text:
            context_parts = [
                f"\n\n【原始文本】\n{original_text}",
                f"\n\n【自动纠错结果】\n{text}",
            ]
            if typos:
                typo_lines = [f"- {t.original} -> {t.corrected}（第{t.line}行，位置{t.start}-{t.end}）" for t in typos]
                context_parts.append("\n\n【发现的错别字】\n" + "\n".join(typo_lines))
            if sensitive_hits:
                hit_lines = [f"- {h.word}（{h.category}，第{h.line}行）" for h in sensitive_hits]
                context_parts.append("\n\n【命中的敏感词】\n" + "\n".join(hit_lines))
            context_parts.append("\n\n请基于以上信息，判断是否需要进一步修改，并给出审校意见。")
            prompt = prompt + "".join(context_parts)
        return self._generate(system, prompt)

    def summarize(self, context: dict) -> dict:
        """Produce a consolidated final suggestion from aggregated findings.

        ``context`` maps placeholder names (without braces) to values that are
        substituted into the ``summary_prompt`` template, e.g.
        ``{"corrected_text": ..., "typos": ..., "sensitive": ..., "review": ...}``.
        Falls back to an error result when no ``summary_prompt`` is configured.
        """
        template = self.config.get("summary_prompt")
        if not template:
            return {
                "model": self.model,
                "reachable": False,
                "suggestions": "",
                "error": "summary_prompt is not configured",
            }
        prompt = template
        for key, value in context.items():
            prompt = prompt.replace("{" + key + "}", str(value))
        system = self.config.get("system_prompt", "")
        return self._generate(system, prompt)

    def is_reachable(self) -> bool:
        """Probe the Ollama endpoint with a tiny request."""
        probe = {
            "model": self.model,
            "prompt": "ping",
            "stream": False,
            "think": False,
            "options": {"num_predict": 1},
        }
        url = self.base_url + "/api/generate"
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
