"""Configuration loading for the correct-cn service.

Configuration has two sources:

1. A single ``config.json`` file with **five** top-level nodes under the data
   directory so it can be edited without rebuilding the image:

     * ``corrector``  -- MacBert typo model
     * ``sensitive``  -- sensitive-word engine
     * ``review``     -- Step 3 复核 (re-check tool findings)
     * ``audit``      -- Step 4 二次校验 (optional second LLM pass)
     * ``final``      -- Step 5 最终修复 (optional final repair text)

   The three LLM steps (review / audit / final) each carry their own
   ``protocol`` / ``base_url`` / ``model`` / ``api_key``, so different steps can
   use different providers -- e.g. the cheap local Ollama for review & audit and
   a stronger external model for the final repair.

2. Environment variables (see :data:`ENV_DEFAULTS`) as a **small default set**.
   These can also be supplied through a ``.env`` file, parsed by
   :func:`load_env_file` with only the standard library. The env surface is kept
   minimal on purpose:

     * ``LLM_*``  -- one default for the three LLM steps' transport fields
       (protocol / base_url / model / api_key / timeout). A single set covers
       review / audit / final; a step that needs a different provider is simply
       set explicitly in ``config.json``.
     * ``CORRECTOR_MODEL`` / ``CORRECTOR_MODE`` -- the MacBert corrector.
     * ``SENSITIVE_REMOTE_BASE`` -- sensitive-word remote source.

   **Precedence (lowest -> highest): built-in defaults < config.json < env.**
   Crucially, env vars are a *fallback*: they are only applied to a field that
   ``config.json`` did NOT specify (left empty / absent). So config.json always
   wins -- you never need many env vars; you only set the ``LLM_*`` defaults you
   want, and override per step inside ``config.json`` when a step differs.

Only the standard library is used here so this module can be imported in
environments where the ML dependencies are not installed.
"""

import copy
import json
import os
from dataclasses import dataclass, field

ENV_DEFAULTS = {
    "DATA_DIR": "/data",
    # --- Single LLM default set. Fills the transport fields of review / audit /
    # final ONLY when config.json leaves them empty. protocol: "ollama" -> native
    # POST /api/generate; "openai" -> OpenAI-compatible POST /v1/chat/completions.
    "LLM_PROTOCOL": "ollama",
    "LLM_BASE_URL": "http://localhost:11434",
    "LLM_MODEL": "qwen3.5:9b",
    "LLM_API_KEY": "",  # Bearer token for an external (openai) provider
    "LLM_TIMEOUT": "300",
    # --- Corrector (MacBert) ---
    "CORRECTOR_MODEL": "shibing624/macbert4csc-base-chinese",
    "CORRECTOR_MODE": "model",  # "model" | "mock"
    # --- Sensitive-word engine ---
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
    # --------------------------------------------------------------------- #
    # Step 3 复核 (always on): validate the tool findings, drop 误报, find 漏报.
    # 默认本地 Ollama，省成本。
    # --------------------------------------------------------------------- #
    "review": {
        # Wire protocol for this step:
        #   "ollama" (default) -> native POST /api/generate
        #   "openai"           -> OpenAI-compatible POST /v1/chat/completions
        #                         (OpenAI / DeepSeek / Kimi / Qwen 兼容模式 /
        #                          SiliconFlow / vLLM / LM Studio / Ollama 的 /v1)
        "protocol": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://host.docker.internal:11434",
        "timeout": 300,
        # Bearer token for the endpoint. Required by most external providers,
        # ignored by a local Ollama. Env: OLLAMA_API_KEY (or FINAL_API_KEY).
        "api_key": "",
        # review 的核心职责：复核工具性检查（错别字 / 敏感词）的结果。
        # 工具脱离语境，必有 误报（把正确判为错误）与 漏报（漏掉真实问题）。
        # review 既要剔除误报，也要主动发现漏报，并对确有问题处给出修复性意见。
        "system_prompt": (
            "你是一名严谨的中文审校复核助手。上游「错别字检查」与「敏感词扫描」属于工具性检查，"
            "由于脱离语境，必然存在一定数量的误报（把正确的用法判为错误）与漏报（漏掉了真实的问题）。\n\n"
            "你的职责是结合全文语境：\n"
            "1. 复核工具已列出的疑似问题，剔除误报；\n"
            "2. 主动发现工具漏报的真实问题（未被检出的错别字、敏感词，以及语法/语义不通之处）；\n"
            "3. 对每一处确有问题之处，给出可执行的修复性意见。\n\n"
            "规则：\n"
            "- 对【待复核清单】每条给出结论：确为错误→【确认修改】/【确认敏感】；误报→【非错误】/【非敏感】。\n"
            "- 主动扫描全文，找出漏报问题，归入【漏报】清单，类型可为 错别字 / 敏感词 / 语法。\n"
            "- 错别字须给出正确写法；敏感词须给出删除或替换方案；语法/语义问题须给出修正后的表述。\n"
            "- 结论必须可机器解析，使用固定标签并附简短理由（不超过 30 字）。\n"
            "- 不评价文笔、风格、表达优美度；只处理确属错误 / 确属违规之处。\n"
            "- 全程使用中文。"
        ),
        "user_template": (
            "请对下列文本进行审校复核。\n\n"
            "【待审校文本】\n{text}\n"
        ),
        # 结构化输出格式：先逐条复核清单，再列出工具漏报。供解析器按编号/标签提取。
        "output_format": (
            "请严格按以下格式输出。\n\n"
            "一、针对【待复核清单】的逐条复核（编号须与清单一致，一条一审）：\n"
            "【复核结论】\n"
            "1. 【确认修改】原文=「你号」建议=「你好」理由=人称问候常见错别字\n"
            "2. 【非错误】原文=「的地得」理由=此处用法正确\n"
            "3. 【确认敏感】原文=「炸弹」建议=「删除」理由=描述购买行为存在风险\n"
            "4. 【非敏感】原文=「炸弹」理由=新闻报道中性提及\n\n"
            "二、工具漏报的真实问题（若全文无漏报，写「无」）：\n"
            "【漏报】\n"
            "1. 类型=语法 原文=「由于下雨了所以比赛取消」建议=「因为下雨，比赛取消了」理由=关联词冗余\n"
            "2. 类型=错别字 原文=「他那天很纳闷」建议=「他那天很纳闷」理由=形近字误写\n\n"
            "标签说明：\n"
            "- 【确认修改】：确为错别字，正确写法填于 建议=\n"
            "- 【非错误】：并非错别字 / 用法正确，无需修改\n"
            "- 【确认敏感】：确为敏感词，删除或替换方案填于 建议=\n"
            "- 【非敏感】：语境中正常，无需处理\n\n"
            "要求：理由置于 理由= 之后；不要输出清单之外的额外分析；漏报项必须给出可执行的修复建议。"
        ),
        "temperature": 0.2,
        "num_ctx": 16384,
        "max_tokens": 8192,
        # OpenAI 协议下 token 上限的字段名。绝大多数兼容服务用 "max_tokens"，
        # OpenAI 最新模型需改成 "max_completion_tokens"。
        # "max_tokens_param": "max_tokens",
        # 逃逸口：合并进 OpenAI 协议请求体的额外字段，例如 {"top_p": 0.9}。
        # "extra_body": {},
        "require_json": False,
        # 对 Ollama 的瞬时失败（连接错误 / 超时 / 5xx / 429）重试次数；
        # 4xx（除 429）与 body 级错误不重试。0 表示不重试。
        "max_retries": 2,
        # Ollama 思考模式（仅 ollama 协议生效）。默认关闭：审校只需最终答案，
        # 不需要思维链；设为 true 可获得推理过程（但会拖慢并增大输出）。
        "think": False,
    },
    # --------------------------------------------------------------------- #
    # Step 4 二次校验 (可选)：对 review 意见再做一次 LLM 终审，提升可靠性。
    # 与 review 默认同样用本地 Ollama；enabled=false 时跳过（省一次模型调用）。
    # --------------------------------------------------------------------- #
    "audit": {
        "enabled": False,
        "protocol": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://host.docker.internal:11434",
        "timeout": 300,
        "api_key": "",
        # audit 的专属 system 人设（Step 4 二次校验）；核对数据与格式放在 audit_prompt。
        "system_prompt": (
            "你是一名中文审校终审复核员，负责对第一轮复核的逐条修改建议做二次终审（Step 4 二次校验）。\n\n"
            "职责与规则：\n"
            "1. 只复核已给出的建议，不新增建议。\n"
            "2. 结合原文语境判断：原文用法正确→推翻为【非错误】；确为错别字→保留【确认修改】。\n"
            "3. 敏感词建议：结合上下文判断真实风险，给出【确认敏感】或【非敏感】。\n"
            "4. 每条附简短理由（不超过 30 字），使用固定标签【确认修改】【非错误】【确认敏感】【非敏感】。\n"
            "5. 全程使用中文，输出严格可解析，不输出建议之外的额外分析。"
        ),
        # 对「第一轮复核」的逐条建议做终审：确认或推翻。Placeholders: {issues}, {original_text}.
        "audit_prompt": (
            "下面是「第一轮复核」对自动检测结果的逐条修改建议，以及原文。"
            "请结合原文语境，对每一条建议再做一次终审：确认其成立，或推翻误报。\n\n"
            "【第一轮复核建议】\n{issues}\n\n"
            "【原文】\n{original_text}\n\n"
            "请逐条终审，格式：\n1. 【标签】理由=xxx"
        ),
        "temperature": 0.2,
        "num_ctx": 16384,
        "max_tokens": 4096,
        "max_retries": 2,
        # Ollama 思考模式（仅 ollama 协议生效）。默认关闭：审校只需最终答案，
        # 不需要思维链；设为 true 可获得推理过程（但会拖慢并增大输出）。
        "think": False,
    },
    # --------------------------------------------------------------------- #
    # Step 5 最终修复 (可选)：结合已确认意见产出修正后的完整文本。
    # 质量敏感，通常改用外部大模型（protocol=openai）；enabled=false 时跳过。
    # --------------------------------------------------------------------- #
    "final": {
        "enabled": False,
        "protocol": "ollama",
        "model": "qwen3.5:9b",
        "base_url": "http://host.docker.internal:11434",
        "timeout": 300,
        "api_key": "",
        # final 的专属 system 人设（Step 5 最终修复）；数据与格式放在 final_prompt。
        "system_prompt": (
            "你是中文文本修正助手，负责结合「已确认的修改建议」对原文做最终修复（Step 5），并输出修正后的完整文本。\n\n"
            "修复范围与原则：\n"
            "1. 应用所有【确认修改】的错别字修正，以及所有【确认敏感】的敏感词处理（删除或替换为中性表述）。\n"
            "2. 同时修正文中其余明显的语法错误与语义不通之处（搭配不当、成分残缺、逻辑矛盾等），但仅限于「纠错」。\n"
            "3. 不润色文笔、不改写文风；保持原文结构、人称、事实与语气不变，仅修改确有错误之处。\n"
            "4. 直接输出修正后的完整文本，不要附带解释或讨论。\n"
            "5. 全程使用中文。"
        ),
        # 结合「已确认的修改建议」对原文最终修复，输出修正后的完整文本。Placeholders: {original_text}, {issues}.
        "final_prompt": (
            "请结合「已确认的修改建议」对原文进行最终修复，并直接输出修正后的完整文本。\n\n"
            "【原文】\n{original_text}\n\n"
            "【已确认的修改建议】\n{issues}\n\n"
            "请直接输出修正后的完整文本："
        ),
        "temperature": 0.2,
        "num_ctx": 16384,
        "max_tokens": 8192,
        "max_retries": 2,
        # Ollama 思考模式（仅 ollama 协议生效）。默认关闭：审校只需最终答案，
        # 不需要思维链；设为 true 可获得推理过程（但会拖慢并增大输出）。
        "think": False,
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
    llm_protocol: str = field(default=ENV_DEFAULTS["LLM_PROTOCOL"])
    llm_base_url: str = field(default=ENV_DEFAULTS["LLM_BASE_URL"])
    llm_model: str = field(default=ENV_DEFAULTS["LLM_MODEL"])
    llm_api_key: str = field(default="")
    llm_timeout: int = field(default=300)
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
            corrector_mode=get("CORRECTOR_MODE"),
            llm_protocol=get("LLM_PROTOCOL"),
            llm_base_url=get("LLM_BASE_URL"),
            llm_model=get("LLM_MODEL"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_timeout=int(get("LLM_TIMEOUT")),
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
    """Recursively merge ``override`` into a deep copy of ``base``.

    The base is deep-copied so that later in-place edits (e.g. the env-var
    overrides applied in :func:`load_config`) can never mutate the module-level
    :data:`DEFAULT_CONFIG` and leak state across calls.
    """
    result = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(settings: Settings) -> dict:
    """Load the merged configuration dict.

    Precedence (lowest -> highest): built-in ``DEFAULT_CONFIG`` < ``config.json``
    < environment variables. Env vars are a **fallback default**: a field is
    taken from the env only when ``config.json`` did NOT specify it (absent or
    empty). So config.json always wins, and you only need to set the ``LLM_*``
    env defaults you want -- a step that uses a different provider is simply set
    explicitly in its node.

    The three LLM steps (review / audit / final) share one ``LLM_*`` default set;
    the corrector and sensitive engine have their own single vars.
    """
    path = resolve_config_path(settings)
    # Read the raw file first so we can distinguish "config.json omitted this
    # field" from "the built-in default seeded it" when applying env fallbacks.
    raw_file: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw_file = json.load(fh) or {}
        except (json.JSONDecodeError, OSError):
            # Missing/corrupt file -> fall back to built-in defaults.
            raw_file = {}

    cfg = _deep_merge(DEFAULT_CONFIG, raw_file)

    # Env fallback: applied only to fields config.json left empty/unspecified.
    # Precedence (lowest -> highest): built-in defaults < config.json <
    #   OLLAMA_* (shared local model for review + audit) < FINAL_* (Step 5
    #   external override) < LLM_* (legacy catch-all).
    #
    # Each row is (node, field, env_var).
    _OLLAMA_FALLBACKS = [
        ("review", "protocol", "OLLAMA_PROTOCOL"),
        ("review", "base_url", "OLLAMA_BASE_URL"),
        ("review", "model", "OLLAMA_MODEL"),
        ("review", "api_key", "OLLAMA_API_KEY"),
        ("review", "timeout", "OLLAMA_TIMEOUT"),
        ("audit", "protocol", "OLLAMA_PROTOCOL"),
        ("audit", "base_url", "OLLAMA_BASE_URL"),
        ("audit", "model", "OLLAMA_MODEL"),
        ("audit", "api_key", "OLLAMA_API_KEY"),
        ("audit", "timeout", "OLLAMA_TIMEOUT"),
    ]
    _FINAL_FALLBACKS = [
        ("final", "protocol", "FINAL_PROTOCOL"),
        ("final", "base_url", "FINAL_BASE_URL"),
        ("final", "model", "FINAL_MODEL"),
        ("final", "api_key", "FINAL_API_KEY"),
        ("final", "timeout", "FINAL_TIMEOUT"),
    ]
    _LLM_FALLBACKS = [
        ("review", "protocol", "LLM_PROTOCOL"),
        ("review", "base_url", "LLM_BASE_URL"),
        ("review", "model", "LLM_MODEL"),
        ("review", "api_key", "LLM_API_KEY"),
        ("review", "timeout", "LLM_TIMEOUT"),
        ("audit", "protocol", "LLM_PROTOCOL"),
        ("audit", "base_url", "LLM_BASE_URL"),
        ("audit", "model", "LLM_MODEL"),
        ("audit", "api_key", "LLM_API_KEY"),
        ("audit", "timeout", "LLM_TIMEOUT"),
        ("final", "protocol", "LLM_PROTOCOL"),
        ("final", "base_url", "LLM_BASE_URL"),
        ("final", "model", "LLM_MODEL"),
        ("final", "api_key", "LLM_API_KEY"),
        ("final", "timeout", "LLM_TIMEOUT"),
    ]

    def _apply_fallbacks(fallback_list):
        for node, field, envvar in fallback_list:
            envval = os.environ.get(envvar)
            if not envval:
                continue
            # Skip if config.json explicitly specified the field (present and
            # non-empty) OR if an earlier fallback layer already filled it
            # (detected by comparing against the raw-file value or the
            # built-in default — if the merged config still holds the
            # default value, the field was NOT set by config.json).
            raw_val = (raw_file.get(node) or {}).get(field)
            if raw_val:
                continue
            merged_val = (cfg.get(node) or {}).get(field)
            default_val = (DEFAULT_CONFIG.get(node) or {}).get(field)
            # A field set by an earlier env fallback will differ from the
            # built-in default, so we can detect it that way.
            if merged_val and merged_val != default_val:
                continue
            cfg_node = cfg.setdefault(node, {})
            cfg_node[field] = int(envval) if field == "timeout" else envval

    # Corrector / sensitive single-var fallbacks (no node-specific overrides).
    for node, field, envvar in [
        ("corrector", "model", "CORRECTOR_MODEL"),
        ("corrector", "mode", "CORRECTOR_MODE"),
        ("sensitive", "remote_base", "SENSITIVE_REMOTE_BASE"),
    ]:
        envval = os.environ.get(envvar)
        if envval and not (raw_file.get(node) or {}).get(field):
            cfg.setdefault(node, {})[field] = envval

    # 1. OLLAMA_* fills shared local model for review + audit.
    _apply_fallbacks(_OLLAMA_FALLBACKS)
    # 2. FINAL_* overrides the final node (Step 5 external provider).
    _apply_fallbacks(_FINAL_FALLBACKS)
    # 3. Legacy LLM_* catch-all (ignored when OLLAMA_*/FINAL_* already set).
    _apply_fallbacks(_LLM_FALLBACKS)

    return cfg
