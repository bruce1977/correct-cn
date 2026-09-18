"""Configuration loading for the correct-cn service.

Configuration has two sources:

1. Environment variables (see :data:`ENV_DEFAULTS`) for runtime wiring such as
   the Ollama endpoint, the correction model name and the data directory. These
   can also be supplied through a ``.env`` file, which is parsed by
   :func:`load_env_file` using only the standard library (no third-party
   dependency such as python-dotenv is required).
2. A single ``config.json`` file with three top-level nodes -- ``corrector``,
   ``sensitive`` and ``review`` -- that lives under the data directory so it can
   be edited without rebuilding the image.

Precedence (lowest -> highest): built-in defaults < config.json < environment
variable / .env entry. Only the standard library is used here so this module can
be imported in environments where the ML dependencies are not installed.
"""

import json
import os
from dataclasses import dataclass, field

ENV_DEFAULTS = {
    "DATA_DIR": "/data",
    "CORRECTOR_MODEL": "shibing624/macbert4csc-base-chinese",
    "CORRECTOR_MODE": "model",  # "model" | "mock"
    "OLLAMA_BASE_URL": "http://localhost:11434",
    "OLLAMA_MODEL": "qwen3.5:9b",
    "OLLAMA_TIMEOUT": "60",
    "OLLAMA_API_KEY": "",  # optional API key for authenticated Ollama endpoints
    "SENSITIVE_REMOTE_BASE": (
        "https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/"
    ),
    "CONFIG_PATH": None,  # resolved against DATA_DIR when None
}

# Built-in default configuration. Every node can be overridden by the JSON file
# and every value can be overridden again by an explicit environment variable.
DEFAULT_CONFIG = {
    "corrector": {
        "model": "shibing624/macbert4csc-base-chinese",
    },
    "sensitive": {
        "remote_base": (
            "https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/"
        ),
        # When True, refresh discovers the dictionary files present in the data
        # directory instead of relying on a hard-coded file list.
        "auto_discover": True,
        # Lower-case both the dictionary and the scanned text before matching.
        # Useful when dictionary words contain ASCII letters/digits.
        "case_insensitive": False,
    },
    "review": {
        "model": "qwen3.5:9b",
        "base_url": "http://host.docker.internal:11434",
        "timeout": 300,
        "system_prompt": (
            "你是一名中文审校助手。自动检测工具（错别字检查、敏感词扫描）会产出疑似问题，"
            "但会产生大量误报。你的职责是用逻辑和上下文推断这些发现是否合理，"
            "过滤掉误报，只保留真正需要修改的问题。\n\n"
            "判断原则：\n"
            "1. 只验证已发现的问题，不要自行寻找新问题。\n"
            "2. 敏感词判断：同一个词在不同语境下可能敏感也可能不敏感。\n"
            "   例如「炸弹」在新闻报道中是正常提及（非敏感），在描述购买行为时才是敏感。\n"
            "   判断依据是该词在当前语境中是否有实际危害性。\n"
            "3. 错别字判断：自动纠错可能误判，如果该词在语境中用法正确，则标记为「非错误」。\n"
            "4. 不要给出文笔优化建议，只报告确认存在的问题。\n"
            "5. 回答必须使用中文。"
        ),
        "user_template": (
            "以下是要审校的文本及自动检测结果，请逐条验证：\n\n"
            "---\n{text}\n---"
        ),
        "output_format": (
            "请按以下格式逐条验证（每条验证结果占一行）：\n\n"
            "【错别字验证】\n"
            "1.「你号」→确认修改：建议改为「你好」\n"
            "2.「的地得」→非错误：在上下文中用法正确\n\n"
            "【敏感词验证】\n"
            "（同一词在不同上下文中可能敏感也可能不敏感，需结合前后文判断）\n"
            "1.「炸弹」→非敏感：新闻报道语境，正常提及\n"
            "2.「炸弹」→确认敏感：描述购买行为，需删除\n"
            "3.「枪支」→非敏感：博物馆展览语境，正常描述\n"
            "4.「枪支」→确认敏感：描述非法持有，需处理\n\n"
            "【需保留的修改】\n"
            "（只列出确认需要修改的问题，每条一行）"
        ),
        # Template used by the pipeline's optional audit (Step 4) LLM call.
        # Placeholders: {issues}, {original_text}.
        "audit_prompt": (
            "请根据以下修改建议列表，逐条验证是否确实需要修改。\n"
            "对于每条建议，给出「确认」或「非错误/非敏感」的判断。\n\n"
            "重要规则：\n"
            "1. 只验证已有建议，不要添加新的修改建议。\n"
            "2. 敏感词必须结合上下文判断。\n\n"
            "【修改建议列表】\n{issues}\n\n"
            "【原始文本】\n{original_text}\n\n"
            "请逐条验证，格式：\n"
            "1. [确认/非错误] 原因：xxx"
        ),
        # Template used by the pipeline's optional final text generation (Step 5).
        # Placeholders: {original_text}, {issues}.
        "final_prompt": (
            "请根据以下确认的修改建议，生成修改后的完整文本。\n"
            "只应用确认需要修改的问题，不要进行文笔优化。\n\n"
            "重要规则：\n"
            "1. 保持原文结构和风格不变。\n"
            "2. 只修改确认需要修改的部分。\n\n"
            "【原始文本】\n{original_text}\n\n"
            "【确认的修改建议】\n{issues}\n\n"
            "请输出修改后的完整文本："
        ),
        "temperature": 0.3,
        "num_ctx": 8192,
        "max_tokens": 4096,
        "require_json": False,
        # Whether the pipeline makes a second LLM call to re-validate review
        # suggestions (audit step). Disabling avoids an extra model call.
        "enable_audit": False,
        # Whether the pipeline makes a third LLM call to generate the final
        # corrected text. Disabling avoids an extra model call.
        "enable_final_suggestion": False,
    },
}


def load_env_file(path: str = None) -> None:
    """Parse a ``.env`` file into ``os.environ`` (only missing keys are set).

    No third-party dependency is used. Candidate locations, in order: the
    explicit ``path`` argument, the ``ENV_FILE`` environment variable, and
    ``./.env`` in the current working directory.
    """
    candidates = [path, os.environ.get("ENV_FILE"), os.path.join(os.getcwd(), ".env")]
    target = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not target:
        return
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        # A missing/unreadable .env is non-fatal; env vars may come from elsewhere.
        pass


@dataclass
class Settings:
    """Resolved runtime settings."""

    data_dir: str = field(default=ENV_DEFAULTS["DATA_DIR"])
    corrector_model: str = field(default=ENV_DEFAULTS["CORRECTOR_MODEL"])
    corrector_mode: str = field(default=ENV_DEFAULTS["CORRECTOR_MODE"])
    ollama_base_url: str = field(default=ENV_DEFAULTS["OLLAMA_BASE_URL"])
    ollama_model: str = field(default=ENV_DEFAULTS["OLLAMA_MODEL"])
    ollama_timeout: int = field(default=60)
    ollama_api_key: str = field(default="")
    sensitive_remote_base: str = field(default=ENV_DEFAULTS["SENSITIVE_REMOTE_BASE"])
    config_path: str = field(default=None)
    api_keys_file: str = field(default=None)

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables (and an optional .env)."""
        # Load .env before reading os.environ so file-based configuration works
        # for local, non-Docker runs without extra dependencies.
        load_env_file()

        def get(key):
            val = os.environ.get(key)
            return val if val not in (None, "") else ENV_DEFAULTS[key]

        return cls(
            data_dir=get("DATA_DIR"),
            corrector_model=get("CORRECTOR_MODEL"),
            ollama_base_url=get("OLLAMA_BASE_URL"),
            ollama_model=get("OLLAMA_MODEL"),
            ollama_timeout=int(get("OLLAMA_TIMEOUT")),
            ollama_api_key=os.environ.get("OLLAMA_API_KEY", ""),
            sensitive_remote_base=get("SENSITIVE_REMOTE_BASE"),
            config_path=os.environ.get("CONFIG_PATH") or None,
            api_keys_file=os.environ.get("API_KEYS_FILE")
            or os.path.join(get("DATA_DIR"), ".keys"),
        )


def resolve_config_path(settings: Settings) -> str:
    """Return the absolute path of the main config JSON file."""
    if settings.config_path:
        return settings.config_path
    return os.path.join(settings.data_dir, "config.json")


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` into a copy of ``base``."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(settings: Settings) -> dict:
    """Load the merged configuration dict (defaults < file < env).

    Environment variables (including those loaded from ``.env``) have the
    highest precedence so they can override values set in ``config.json``.
    """
    path = resolve_config_path(settings)
    cfg = _deep_merge(DEFAULT_CONFIG, {})
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            cfg = _deep_merge(cfg, data)
        except (json.JSONDecodeError, OSError):
            # Fall back to defaults if the file is missing or corrupt.
            pass

    # Environment variables have the highest precedence.
    if os.environ.get("CORRECTOR_MODEL"):
        cfg["corrector"]["model"] = os.environ["CORRECTOR_MODEL"]
    if os.environ.get("CORRECTOR_MODE"):
        cfg["corrector"]["mode"] = os.environ["CORRECTOR_MODE"]
    if os.environ.get("SENSITIVE_REMOTE_BASE"):
        cfg["sensitive"]["remote_base"] = os.environ["SENSITIVE_REMOTE_BASE"]
    if os.environ.get("OLLAMA_MODEL"):
        cfg["review"]["model"] = os.environ["OLLAMA_MODEL"]
    if os.environ.get("OLLAMA_BASE_URL"):
        cfg["review"]["base_url"] = os.environ["OLLAMA_BASE_URL"]
    if os.environ.get("OLLAMA_TIMEOUT"):
        cfg["review"]["timeout"] = int(os.environ["OLLAMA_TIMEOUT"])
    if os.environ.get("OLLAMA_API_KEY"):
        cfg["review"]["api_key"] = os.environ["OLLAMA_API_KEY"]
    return cfg
