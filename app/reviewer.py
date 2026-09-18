"""Grammar / wording review using a local LLM exposed by Ollama.

Communication with Ollama is done with the standard-library :mod:`urllib`
module to avoid pulling in an extra HTTP client dependency. The endpoint,
model name and timeout are supplied by the caller (typically from settings).
"""

import json
import logging
import re
import urllib.error
import urllib.request

from app.schemas import Issue

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

    def _parse_issues_from_response(
        self,
        response_text: str,
        typos: list,
        sensitive_hits: list,
    ) -> list:
        """Parse LLM response into structured Issue objects.

        This is a best-effort parser that tries to extract validated issues
        from the LLM's free-form response. It matches findings against the
        original typos and sensitive_hits to preserve position information.
        """
        issues = []

        # Process typos
        for typo in typos:
            suggestion = self._extract_typo_suggestion(response_text, typo)
            issues.append(Issue(
                type="typo",
                original=typo.original,
                corrected=typo.corrected,
                line=typo.line,
                start=typo.start,
                end=typo.end,
                suggestion=suggestion,
            ))

        # Process sensitive words
        for hit in sensitive_hits:
            suggestion = self._extract_sensitive_suggestion(response_text, hit)
            issues.append(Issue(
                type="sensitive",
                word=hit.word,
                category=hit.category,
                line=hit.line,
                start=hit.start,
                end=hit.end,
                suggestion=suggestion,
            ))

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

        When ``original_text`` / ``typos`` / ``sensitive_hits`` are provided the
        prompt includes the full correction context so the LLM can validate
        the findings.
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
            context_parts.append("\n\n请结合全文语境，逐条验证以上发现是否属实，并给出审校意见。")
            prompt = prompt + "".join(context_parts)

        result = self._generate(system, prompt)

        # Parse the response into structured issues
        if result["reachable"] and (typos or sensitive_hits):
            issues = self._parse_issues_from_response(
                result["suggestions"], typos or [], sensitive_hits or []
            )
            result["issues"] = issues
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
                    f"「{issue.original}」→「{issue.corrected}」"
                )
            else:
                issue_lines.append(
                    f"- 第{issue.line}行，位置{issue.start}-{issue.end}："
                    f"敏感词「{issue.word}」（{issue.category}）需处理"
                )

        prompt = template
        prompt = prompt.replace("{original_text}", original_text)
        prompt = prompt.replace("{issues}", "\n".join(issue_lines) if issue_lines else "无")

        system = self.config.get("system_prompt", "")
        result = self._generate(system, prompt)

        # Extract final text from response
        if result["reachable"]:
            # Try to extract text between markers or use the full response
            text_match = re.search(
                r"(?:修改后的文本|最终文本|完整文本)[：:]\s*\n(.*?)(?:\n\n|\Z)",
                result["suggestions"],
                re.DOTALL,
            )
            if text_match:
                result["final_text"] = text_match.group(1).strip()
            else:
                # Use the full response as the final text
                result["final_text"] = result["suggestions"]
        else:
            result["final_text"] = original_text

        return result

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
