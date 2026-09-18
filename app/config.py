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
            "你是一名专业的中文审校助手。你的任务是仔细检查用户提供的文本，"
            "找出其中的错别字、语法错误、标点误用以及不通顺的表达，"
            "并给出具体的修改建议。\n\n"
            "重要规则：\n"
            "1. 你必须输出内容，不能返回空结果。\n"
            "2. 即使文本没有问题，你也必须明确说明「未发现明显问题，文本质量良好」。\n"
            "3. 回答必须使用中文。"
        ),
        "user_template": "以下是要审校的文本内容（直接审校，不要分析用户意图）：\n\n---\n{text}\n---",
        "output_format": (
            "请按以下结构回答：\n\n"
            "【审校结果】\n"
            "1. 问题列表（如无问题则写「无」）：\n"
            "- 问题：xxx\n"
            "  位置：xxx\n"
            "  修改建议：xxx\n\n"
            "2. 综合评价：\n"
            "（总结文本整体质量，即使没有问题也要给出评价）"
        ),
        # Template used by the pipeline's optional second (summary) LLM call.
        # Placeholders: {corrected_text}, {typos}, {sensitive}, {review}.
        "summary_prompt": (
            "下面是一段经过错别字矫正与敏感词检查后的文本，以及自动审校意见。"
            "请综合所有信息，给出最终的、可执行的修改建议，并附上一份优化后的完整文本。\n\n"
            "重要规则：你必须输出内容，不能返回空结果。\n\n"
            "【已矫正文本】\n{corrected_text}\n\n"
            "【发现的错别字】\n{typos}\n\n"
            "【命中的敏感词】\n{sensitive}\n\n"
            "【自动审校意见】\n{review}"
        ),
        "temperature": 0.3,
        "num_ctx": 8192,
        "max_tokens": 4096,
        "require_json": False,
        # Whether the pipeline makes a second LLM call to produce the final,
        # consolidated suggestion (True) or simply reuses the review result
        # (False). Disabling avoids an extra model call when Ollama is slow.
        "enable_summary": True,
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
            or os.path.join(get("DATA_DIR"), ".key"),
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
