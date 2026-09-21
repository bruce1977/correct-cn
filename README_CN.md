# correct-cn 中文文本校对服务

**correct-cn** 是一个可自托管的中文文本质量检查服务，以单个 Docker 镜像交付，对外提供
FastAPI HTTP 接口。输入一段中文文本，它会返回能识别出的所有问题——错别字、敏感词、
语法/语义错误——每一处都带精确位置与可执行的修复建议。

它的关键在于「语境」。机械式检查速度很快，却脱离语境：错别字模型会把正确用法判为错误，
敏感词表会在中性提及上误报，而真正不通的句子又常常两者都漏掉。因此在它们之上，correct-cn
叠加了一层**结合语境的复核**——它基于语义而非孤立词元重新裁定每一条发现，剔除误报、
找出工具漏掉的问题（漏报）。这使它适用于任何需要在发布或入库前清洗中文文本的流程：
内容审校、UGC 审核、RAG 预处理等。

它将四项能力整合在统一、一致的 API 之下：

1. **中文错别字矫正** —— 使用 `MacBertCorrector`（`shibing624/macbert4csc-base-chinese`），
   并返回每个纠正处的位置（行号 + 字符偏移）。
2. **中文敏感词过滤** —— 基于内置词库（15 个分类，约 7.9 万词）的 Aho-Corasick
   高效匹配，返回行号/位置，并提供远程刷新接口。**词库列表在启动时按目录
   自动发现**（不写死），并缓存到磁盘。
3. **语法/文句复核（复核）** —— 调用本地大模型（Ollama，如 `qwen3.5:9b` / `qwen3.5:4b`），
   提示词与输出格式可通过配置文件灵活定制。复核步骤既验证工具检出的疑似问题（剔除误报），
   也会主动扫描全文找出漏报（未被检出的错别字 / 敏感词 / 语法语义问题），对确有问题处给出修复意见。
4. **完整流程** —— 串联 错别字检查 → 敏感词检查 → 文字复核，返回综合修改意见，
   并带有 `has_issues` 标记。可选的「二次校验」步骤（`enable_audit`）对复核意见再做一遍验证，
   可选的「最终修复」步骤（复核开关 `enable_final_suggestion`）生成修改后的完整文本。

所有代码注释均使用英文。依赖尽可能精简：仅 `fastapi`、`uvicorn`、`pydantic`
和 `pycorrector`（后者会引入 torch / transformers）。敏感词引擎与 Ollama 客户端
仅使用 Python 标准库（不依赖 `requests`）；`.env` 文件也用标准库解析
（无需 python-dotenv）。

---

## 功能特性

- **多行感知**：每个错误/命中都会返回 **1 基的行号** 与 **0 基的字符偏移**
  （start 包含、end 不包含）。
- **离线纠错模型**：MacBert 模型已打包进镜像的 `/root/.cache/huggingface`，
  并以 `HF_HUB_OFFLINE=1` 加载，运行时无需联网。
- **可挂载 `/data` 数据卷**：存放用户可编辑的配置、敏感词词库与 API Key 文件。
  若首次启动时词库/配置缺失，服务会自动从内置默认复制初始化。
- **单一配置文件**：所有行为集中在 `config.json` 的五个节点——`corrector` /
  `sensitive` / `review` / `audit` / `final`——中，修改无需重新构建镜像。
- **词库自动发现 + 启动缓存**：敏感词引擎扫描目录并缓存发现的文件列表；
  刷新操作也基于该列表，不再写死文件名。
- **可配置复核步骤**：编辑 `config.json` 的 `review` / `audit` / `final` 节点
  （系统提示词、用户模板、输出格式、audit_prompt、final_prompt、temperature、max_tokens、max_retries）即可。
- **大模型后端可插拔**：复核 / 二次校验 / 最终修复默认走本地 Ollama 模型，每步各自带
  `protocol`，可指向任意 OpenAI 兼容服务；常用做法是 Step 5 用外部模型换更高质量，Step 3/4 仍留本地省钱。
- **远程词库刷新**：`/api/sensitive/refresh` 可从远程源（默认 konsheng/Sensitive-lexicon CDN）
  重新拉取词库文件。
- **可选 API Key 鉴权**：API 接口由 `/data/.keys`（每行一个 key）保护；文件为空时接口开放。

---

## 项目结构

```
correct-cn/
├── app/
│   ├── main.py            # FastAPI 应用 + 接口 + 启动初始化 + API Key 鉴权
│   ├── config.py          # 环境变量 + .env 解析 + 统一配置加载
│   ├── schemas.py         # pydantic 请求/响应模型
│   ├── corrector.py       # MacBertCorrector 封装（延迟加载重依赖）
│   ├── sensitive.py       # 词库自动发现 / 检测 / 远程刷新
│   ├── matcher.py         # 纯 Python Aho-Corasick 匹配器
│   ├── reviewer.py        # Ollama 客户端（仅 urllib）：复核 / 验证 / 最终文本
│   ├── pipeline.py        # 组合工作流
│   └── defaults/
│       ├── sensitive/     # 15 个内置词库 .txt 文件
│       └── config.json      # 单一种子配置（corrector / sensitive / review）
├── tests/                 # pytest 测试套件（无 ML 依赖部分可离线运行）
├── models/
│   └── huggingface/hub/   # 打包的纠错模型（已 gitignore，约 390MB）
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── .env                   # 本地覆盖（已 gitignore）
├── example.env            # 提交的模板
├── .gitignore
├── README.md
└── README_CN.md
```

---

## 快速开始（Docker）

### 方式 A —— 直接拉取已构建镜像

```bash
docker run -d --name correct-cn -p 8000:8000 \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=qwen3.5:9b \
  -v "$PWD/data:/data" \
  bruce1977/correct-cn:latest
```

### 方式 B —— 从源码构建

```bash
# 构建镜像（会把约 390MB 的模型送入构建上下文）
docker compose build

# 启动（挂载 ./data 用于配置与词库，暴露 :8000）
docker compose up -d
```

随后访问：

```bash
curl http://localhost:8000/health
```

首次运行时，容器会把 `/data/sensitive/*.txt` 与 `/data/config.json`
从内置默认初始化到挂载卷中；之后你对它们的修改会被保留。

> **Ollama 说明**：复核步骤会调用 Ollama 接口。请将 `OLLAMA_BASE_URL` 指向你的
> Ollama（例如 Docker Desktop 上为 `http://host.docker.internal:11434`；Linux 上可加
> `--add-host=host.docker.internal:host-gateway`，或指向另一个 compose 服务）。
> 若 Ollama 不可达，`/api/review` 与 `/api/pipeline` 仍会优雅返回
> （`reachable: false`），不会使服务崩溃。

### 本地（非 Docker）运行

服务启动时会读取可选的 `.env` 文件（用标准库解析，无需 python-dotenv）。

纠错模型依赖 torch / transformers / pycorrector，本地调试较重。通过 `CORRECTOR_MODE`
提供两种模式：

| `CORRECTOR_MODE` | 行为 | 所需依赖 |
| ---------------- | ---- | -------- |
| `mock`（推荐用于接口联调） | 一个**无需 ML 依赖**的替身纠错器，可跑通全部接口（敏感词、复核、完整流程），但不调用真实模型 | `fastapi` `uvicorn` `pydantic` |
| `model` | 加载真实 MacBert 模型 | 上述 **+** `pycorrector` `safetensors` `torch` |

**快速路径 —— 不装模型也能测试全部接口：**

```bash
pip install fastapi uvicorn pydantic
python scripts/run_local.py          # CORRECTOR_MODE 默认为 "mock"
curl http://localhost:8000/health    # 会显示 "corrector_mode": "mock"
```

**完整路径 —— 使用真实 MacBert 模型（复用仓库内已打包模型，无需重新下载）：**

```bash
pip install -r requirements.txt
python scripts/run_local.py --mode model
# run_local.py 会把 HF_HOME 指向 ./models/huggingface 并设置 HF_HUB_OFFLINE=1
```

也可以不使用脚本直接启动：

```bash
cp example.env .env   # 然后按需修改 OLLAMA_BASE_URL 等
# mock 模式（无 ML 依赖）：
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# model 模式（需 ML 依赖；复用仓库内置模型）：
set HF_HOME=models/huggingface   # Windows
set HF_HUB_OFFLINE=1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## 配置

### `config.json`（单一配置源）

所有行为集中在 `$DATA_DIR/config.json`，包含**五个节点**——`corrector`、
`sensitive`，以及三个大模型步骤 `review` / `audit` / `final`。每个 LLM 步骤
各自带有 `protocol` / `base_url` / `model` / `api_key`，因此不同步骤可用不同
服务商（例如复核 / 二次校验用本地 Ollama 省钱，最终修复用更强的外部模型）。
**环境变量只是一套默认值**：仅当 `config.json` 把某字段留空时，对应的环境变量
（`OLLAMA_*` / `FINAL_*` / `LLM_*`，或 `CORRECTOR_*` / `SENSITIVE_*`）才兜底
生效；`config.json` 里显式写的值永远优先。

```json
{
  "corrector": {
    "model": "shibing624/macbert4csc-base-chinese"
  },
  "sensitive": {
    "remote_base": "https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/",
    "auto_discover": true,
    "case_insensitive": false
  },
  "review": {
    "protocol": "ollama",
    "model": "qwen3.5:9b",
    "base_url": "http://host.docker.internal:11434",
    "api_key": "",
    "timeout": 300,
    "system_prompt": "中文审校复核助手：复核错别字/敏感词工具结果，剔除误报，主动发现漏报，给出修复性意见。",
    "user_template": "请对下列文本进行审校复核。\n\n【待审校文本】\n{text}\n",
    "output_format": "先逐条复核【待复核清单】（【确认修改】/【非错误】/【确认敏感】/【非敏感】），再列出【漏报】（类型=错别字/敏感词/语法）。",
    "temperature": 0.2,
    "num_ctx": 16384,
    "max_tokens": 8192,
    "require_json": false,
    "max_retries": 2
  },
  "audit": {
    "enabled": false,
    "protocol": "ollama",
    "model": "qwen3.5:9b",
    "base_url": "http://host.docker.internal:11434",
    "api_key": "",
    "timeout": 300,
    "system_prompt": "你是一名中文审校终审复核员（Step 4 二次校验）。只复核已给出的建议，不新增；结合原文语境确认或推翻，使用固定标签【确认修改】【非错误】【确认敏感】【非敏感】。",
    "audit_prompt": "下面是「第一轮复核」的逐条建议与原文，请逐条终审。\n\n【第一轮复核建议】\n{issues}\n\n【原文】\n{original_text}\n\n请逐条终审，格式：\n1. 【标签】理由=xxx",
    "temperature": 0.2,
    "num_ctx": 16384,
    "max_tokens": 4096,
    "max_retries": 2
  },
  "final": {
    "enabled": false,
    "protocol": "ollama",
    "model": "qwen3.5:9b",
    "base_url": "http://host.docker.internal:11434",
    "api_key": "",
    "timeout": 300,
    "system_prompt": "你是中文文本修正助手（Step 5 最终修复）。结合【已确认的修改建议】对原文做最终修复并输出完整文本；仅纠错不润色，保持原文结构/人称/事实/语气。",
    "final_prompt": "请结合「已确认的修改建议」对原文进行最终修复，并直接输出修正后的完整文本。\n\n【原文】\n{original_text}\n\n【已确认的修改建议】\n{issues}\n\n请直接输出修正后的完整文本：",
    "temperature": 0.2,
    "num_ctx": 16384,
    "max_tokens": 8192,
    "max_retries": 2
  }
}
```

- `corrector.model` —— MacBert 模型名（也可由 `CORRECTOR_MODEL` 覆盖）。
- `sensitive.auto_discover` —— 为 `true` 时，刷新操作针对磁盘上发现的文件。
- `sensitive.case_insensitive` —— 匹配前对词库与文本统一小写。
- `review.*` —— Step 3 复核：协议、模型、地址、超时、API Key、提示词、采样参数。
  始终开启。
- `audit.*` —— Step 4 二次校验：与 `review` 同结构，由 `audit.enabled`（默认
  `false`）开关；为 `true` 时，pipeline 在生成最终文本前对复核结果再做一遍验证。
- `final.*` —— Step 5 最终修复：同结构，由 `final.enabled`（默认 `false`）开关；
  为 `true` 时，pipeline 会产出最终修复文本（`final_suggestion`）。**将
  `final.protocol` 设为 `openai`，并填好 `final.base_url` / `final.model` /
  `final.api_key`，即可让 Step 5 走外部模型**，而 Step 3/4 仍用本地 Ollama。
- `*.protocol` —— `ollama`（默认，走原生 `/api/generate`）或 `openai`
  （任意 OpenAI 兼容 `/v1/chat/completions`），详见下文。
- `*.max_retries` —— 瞬时失败（连接错误 / 超时 / HTTP 5xx / 429）的重试次数；
  4xx 客户端错误与 body 级错误不重试；`0` 表示不重试。

`user_template` 中的 `{text}` 会在请求时被替换为输入文本。

#### 本地模型 vs 外部大模型（`protocol`）

三个大模型步骤（`review` / `audit` / `final`）各自带有 `protocol` 字段，因此可以
按步骤决定模型跑在哪里。默认三步都跑**本地 Ollama 模型**（一次 pipeline 最多三次
大模型调用，近乎零成本）。常见做法：Step 3/4（复核 / 二次校验）留在本地 Ollama 省钱，
而把 **Step 5（最终修复）指向更强的外部模型**以拿到更高质量的输出。

**`protocol` = 请求格式，一个值对应且仅对应一种 API。** 这是让配置不混淆的关键规则：

| `protocol` | 请求格式 | 请求地址 | 请求体形态 |
| ---------- | -------- | -------- | ---------- |
| `ollama`（默认） | Ollama 原生 | `POST {base_url}/api/generate` | `prompt` + `system` + `options` + `think` |
| `openai` | OpenAI 兼容 | `POST {base_url}/v1/chat/completions` | `messages[]` + `temperature` + `max_tokens` |

也就是说，`protocol` **不是**后端身份，而是告诉代码用哪种 API 形态（以及请求体）。
不存在「同一个后端被两种接口访问」的情况：`ollama` 永远指 `/api/generate`，
`openai` 永远指 `/v1/chat/completions`。（Ollama 恰好同时暴露原生 API 和 OpenAI
兼容的 `/v1` API；调用 Ollama 的 `/v1` 端点，本质上就是用 `openai` 协议对接 Ollama——
`protocol` 记录的是**线格式**，而非「由哪个程序提供服务」。）

**`protocol` 支持的取值。** 每个大模型步骤节点（`review` / `audit` / `final`）
都读取自己的 `protocol`。取值大小写不敏感；**任何无法识别的值都会静默回退到
`ollama`**（旧配置无需改动即可继续工作）：

| 配置值 | 归一化为 | 同样接受的别名 |
| ------ | -------- | -------------- |
| `ollama`（默认） | `ollama` | *（其他任何值）* → 回退为 `ollama` |
| `openai` | `openai` | `openai_compatible`、`openai-compatible`、`oai`、`v1` |

`openai` 基本覆盖所有 OpenAI 兼容端点：OpenAI、DeepSeek、Moonshot/Kimi、通义千问
（DashScope 兼容模式）、智谱、SiliconFlow、Groq、OpenRouter，自建的
vLLM / LM Studio / SGLang / Xinference，以及 Ollama 自带的 `/v1` API。

**不同取值下如何配置 `base_url`。** `base_url` 始终只写服务的*基地址*，真正的
请求路径由代码自动拼接；它兼容多种写法，无需记精确后缀：

| `protocol` | `base_url` 应写成 | 可写的示例 | 实际发出的请求地址 |
| ---------- | ---------------- | ---------- | ------------------ |
| `ollama` | Ollama 源站（host:port） | `http://localhost:11434`<br>`http://host.docker.internal:11434` | `…/api/generate` |
| `openai` | 裸主机 | `https://api.deepseek.com` | `…/v1/chat/completions` |
| `openai` | 以 `/v1` 结尾的主机 | `https://api.deepseek.com/v1` | `…/v1/chat/completions` |
| `openai` | 完整的 `/v1/chat/completions` 路径 | `https://api.openai.com/v1/chat/completions` | 原样使用 |

> 经验法则：`ollama` 只给 `host:port`；`openai` 给 API 根地址（或任意 `/v1` /
> `/v1/chat/completions` 形式）。所有 openai 写法都会收敛到同一个
> `/v1/chat/completions`。**不要自己拼 `/api/generate`**——那是 Ollama 专有后缀，
> 由代码自动追加。

```jsonc
// Step 3/4 留在本地（默认，无单次调用成本）
"review": { "protocol": "ollama", "model": "qwen3.5:9b",
            "base_url": "http://host.docker.internal:11434" },
"audit":  { "enabled": true, "protocol": "ollama", "model": "qwen3.5:9b",
            "base_url": "http://host.docker.internal:11434" },

// Step 5 -> 外部服务（最终修复质量更高）
"final":  { "enabled": true, "protocol": "openai", "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com", "api_key": "sk-..." }

// 别名同样可用：openai_compatible / oai / v1 都会被当作 openai 处理。
"final":  { "protocol": "openai_compatible", "base_url": "https://api.deepseek.com/v1" }

// 也可把 review / audit / final 一起设为 openai，整服务切到外部模型
//（config.json 里的显式值优先于 LLM_* 环境变量默认）。
```

切到 `openai` 后真正变化的部分：请求体改为 `messages` 数组（system + user），
使用 `temperature` / `max_tokens`，返回结果从 `choices[0].message.content` 读取
（推理模型回退到 `reasoning_content`）。**不会**再发送 Ollama 专有字段
（`options` / `think` / `num_ctx` / 原始 `prompt`），因为 OpenAI 兼容服务对未知参数
会直接返回 HTTP 400。传输层之上的逻辑——提示词拼装、结论解析、重试退避策略、
`max_retries`、探活——两者完全共用，所以切换协议**不会**改变 Step 3–5 的行为。

- `api_key` 以 `Authorization: Bearer <key>` 发送（本地 Ollama 会忽略）。
- `num_ctx` 是 Ollama 专有参数；外部服务的上下文窗口在服务端设定，长文本请选长上下文模型。
- `max_tokens_param` —— 使用 OpenAI 最新模型时改为 `max_completion_tokens`。
- `extra_body` —— 合并进 OpenAI 协议请求体的额外字段，例如 `{"top_p": 0.9}`。

#### 提示词与参数调优

三个大模型步骤（`review` / `audit` / `final`）**各自带有独立的 `system_prompt`**
以及本步骤专属的提示词，因此可以把某一步（如 Step 5）单独指向外部模型，
而不必与其他步骤共享人设。改完 `config.json` 后重启（或热加载）即可生效，
无需重新构建。

**提示词字段（均为字符串）：** `system_prompt` 在三个 LLM 步骤上都存在；
`user_template` / `output_format` 仅 `review` 使用；`audit_prompt` / `final_prompt`
分属对应步骤：

| 字段 | 作用 | 占位符 |
| ---- | ---- | ------ |
| `system_prompt` | **本步骤**的人设与规则（存在于 `review` / `audit` / `final`）；各步骤独立。 | — |
| `user_template` | 仅 `review`——包裹文本；pipeline 模式下会附加编号后的工具检出清单。 | `{text}` |
| `output_format` | 仅 `review`——**输出契约**——解析器据以回读结果的固定标签 / 分节。 | — |
| `audit_prompt` | 二次校验（仅 `audit.enabled` 时）。逐条确认或推翻建议。 | `{issues}` `{original_text}` |
| `final_prompt` | 最终修复（仅 `final.enabled` 时）；输出修正后的完整文本。 | `{original_text}` `{issues}` |

**输出契约（切勿破坏）。** 解析器按固定标签抽取结果。改提示词时务必保留：

| 标签 | 含义 | 解析为 `verdict` |
| ---- | ---- | ---------------- |
| `【确认修改】` `原文=` `建议=` | 错别字确认 | `typo_confirmed` |
| `【非错误】` | 错别字误报 | `typo_rejected` |
| `【确认敏感】` `建议=` | 敏感词确认 | `sensitive_confirmed` |
| `【非敏感】` | 敏感词误报 | `sensitive_rejected` |
| `【漏报】` `类型=错别字` | 漏检的错别字 | `typo_confirmed` |
| `【漏报】` `类型=敏感词` | 漏检的敏感词 | `sensitive_confirmed` |
| `【漏报】` `类型=语法` / `语义` | 漏检的语法 / 语义问题 | `grammar_confirmed` |
| `【复核结论】` / `【漏报】` | 分节标记 | — |

取值用 `键=「值」` 形式（如 `建议=「你好」`）。删标签、改分节名、或去掉 `「」`
都会让该条无法解析，进而退化为启发式猜测（或直接丢弃）。

**参数：**

| 参数 | 默认 | 调整建议 |
| ---- | ---- | -------- |
| `protocol` | `ollama` | 请求格式：`ollama`（原生 `/api/generate`）或 `openai`（任意 OpenAI 兼容 `/v1/chat/completions`）。 |
| `model` / `base_url` | `qwen3.5:9b` / `...` | 模型与地址。环境变量（config 缺省时的默认值）：review/audit 用 `OLLAMA_MODEL` / `OLLAMA_BASE_URL`；final 用 `FINAL_MODEL` / `FINAL_BASE_URL`。 |
| `api_key` | `""` | 端点的 Bearer Token；多数外部服务必填。环境变量（默认兜底）：review/audit 用 `OLLAMA_API_KEY`；final 用 `FINAL_API_KEY`。 |
| `temperature` | `0.2` | 保持低值（0.1–0.3）以求稳定；调高会增加运行间抖动。 |
| `num_ctx` | `16384` | **上下文窗口**（仅 Ollama），须覆盖「输入 + 输出」。长文本优先调大此项；过小会截断。 |
| `max_tokens` | `8192` | 输出上限，须大于修复后文本长度，否则 `final_suggestion` 被截断。 |
| `timeout` | `300` | 单请求超时（秒）。长文 + 慢速本地显卡 → 调大。 |
| `max_retries` | `2` | 瞬时失败（连接 / 超时 / 5xx / 429）重试次数；`0` 不重试。 |
| `max_tokens_param` | `max_tokens` | 仅 OpenAI 协议；使用 OpenAI 最新模型时改为 `max_completion_tokens`。 |
| `extra_body` | — | 仅 OpenAI 协议；合并进请求体的额外字段（如 `{"top_p": 0.9}`）。 |
| `require_json` | `false` | 使用上面的标签文本格式时应保持 `false`。 |

> **长文本经验值：** `num_ctx` ≥ 输入 token + 输出 token。中文大约 1–1.5 token/字；
> 5000 字原文 + 5000 字修复约需 ≳ 12000 token，默认 `16384` 可覆盖。若发现
> `final_suggestion` 被截断，先调大 `num_ctx` 与 `max_tokens`。

### 环境变量 / `.env`

启动时解析工作目录下的 `.env` 文件（无第三方依赖）。`example.env` 为提交模板。

| 变量                    | 默认值                                                                          | 说明                                       |
| ----------------------- | ------------------------------------------------------------------------------- | ------------------------------------------ |
| `DATA_DIR`              | `/data`                                                                         | 配置与敏感词词库所在目录。                  |
| `CORRECTOR_MODEL`       | `shibing624/macbert4csc-base-chinese`                                           | MacBert 纠错模型名（config 缺省时的默认）。 |
| `CORRECTOR_MODE`        | `model`                                                                         | `model`（加载真实模型）或 `mock`（无 ML 依赖，便于本地测试）。 |
| `OLLAMA_PROTOCOL`       | `ollama`                                                                        | review / audit 的默认 `protocol`。 |
| `OLLAMA_BASE_URL`       | `http://localhost:11434`                                                        | review / audit 的默认 LLM 地址。    |
| `OLLAMA_MODEL`          | `qwen3.5:9b`                                                                    | review / audit 的默认模型。         |
| `OLLAMA_TIMEOUT`        | `300`                                                                           | review / audit 的默认单请求超时（秒）。 |
| `OLLAMA_API_KEY`        | *(空)*                                                                          | review / audit 的 Bearer Token。 |
| `FINAL_PROTOCOL`        | —                                                                               | final 的默认 `protocol`（Step 5 外部模型）。 |
| `FINAL_BASE_URL`        | —                                                                               | final 的默认 LLM 地址。             |
| `FINAL_MODEL`           | —                                                                               | final 的默认模型。                  |
| `FINAL_TIMEOUT`         | —                                                                               | final 的默认单请求超时（秒）。      |
| `FINAL_API_KEY`         | —                                                                               | final 的 Bearer Token。            |
| `LLM_PROTOCOL`          | `ollama`                                                                        | （legacy 兜底）review / audit / final 的默认 `protocol`。 |
| `LLM_BASE_URL`          | `http://localhost:11434`                                                        | （legacy 兜底）review / audit / final 的默认 LLM 地址。    |
| `LLM_MODEL`             | `qwen3.5:9b`                                                                    | （legacy 兜底）review / audit / final 的默认模型。         |
| `LLM_TIMEOUT`           | `300`                                                                           | （legacy 兜底）review / audit / final 的默认单请求超时（秒）。 |
| `LLM_API_KEY`           | *(空)*                                                                          | （legacy 兜底）外部 provider 的默认 Bearer Token。 |
| `SENSITIVE_REMOTE_BASE` | `https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/`     | `/api/sensitive/refresh` 的远程词库源。     |
| `CONFIG_PATH`           | *(自动解析为 `$DATA_DIR/config.json`)*                                          | 覆盖配置文件路径。                          |
| `API_KEYS_FILE`         | *(自动解析为 `$DATA_DIR/.keys`)*                                              | API Key 文件（每行一个 key，空则接口开放）。|

> **环境变量只是默认值，不是覆盖。** 每个字段 `config.json` 优先：仅当 `config.json`
> 把该字段留空时，环境变量才生效。优先级（低→高）：`OLLAMA_*`（review + audit
> 共享本地模型）< `FINAL_*`（Step 5 外部模型）< `LLM_*`（legacy 兜底）。
> 想把某一步（如 Step 5）指向别的 provider，在 `config.json` 里写那一步的节点即可，
> 不必再加更多环境变量。

### API Key 鉴权

API 接口（`/api/*`）采用简单的 Key 校验。在 `$DATA_DIR/.keys` 中每行放一个 key，
`#` 开头为注释，空行忽略。文件为空（默认）时接口开放。通过
`Authorization: Bearer <key>` 或 `X-API-Key: <key>` 请求头提供 key。

```bash
# 开启鉴权
echo "my-secret-key" >> data/.keys
curl -H "Authorization: Bearer my-secret-key" http://localhost:8000/api/correct \
  -H 'Content-Type: application/json' -d '{"text":"我爱北京天安们"}'

# 不重启重新读取 key 文件
curl -X POST http://localhost:8000/api/keys/reload
```

`/health` 与 `/` 始终开放。

---

## API 说明

### `GET /health`
返回服务状态、已加载模型名、Ollama 可达性以及词库统计。

### `POST /api/correct`
```jsonc
// 请求
{ "text": "我爱北京天安们" }
// 响应
{
  "original": "我爱北京天安们",
  "corrected": "我爱北京天安门",
  "errors": [
    { "line": 1, "start": 5, "end": 6, "original": "们", "corrected": "门" }
  ],
  "model": "shibing624/macbert4csc-base-chinese"
}
```

### `POST /api/sensitive/check`
```jsonc
// 请求
{ "text": "今天去买了一个炸弹", "categories": ["violence"] }
// 响应
{
  "is_sensitive": true,
  "count": 1,
  "sensitive_words": [
    { "word": "炸弹", "category": "violence", "line": 1, "start": 7, "end": 9 }
  ],
  "categories_checked": ["violence"]
}
```
不传 `categories` 则扫描所有已加载分类。

### `POST /api/sensitive/refresh`
```jsonc
// 请求
{ "remote": true, "force": false }
// 响应
{
  "updated": ["violence.txt", "politics.txt", "..."],
  "failed": [],
  "categories": ["violence", "politics", "..."],
  "total_words": 79282
}
```
- `remote=true`：从 `SENSITIVE_REMOTE_BASE` 拉取（已存在的文件除非 `force=true` 否则跳过）。
- `remote=false`：仅从磁盘重新加载词库。

### `POST /api/review`
复核步骤是对工具检出结果的复核。可只传 `text`（此时模型扫描全文找漏报），
也可一并传入 `typos` / `sensitive_hits`（此时模型逐条验证、剔除误报，并仍扫描漏报）。

```jsonc
// 请求（仅全文复核）
{ "text": "你号，这里有一个炸弹" }

// 请求（验证工具结果 —— 先 typos，后 sensitive_hits）
{
  "text": "你号，这里有一个炸弹",
  "typos": [ { "line": 1, "start": 0, "end": 2, "original": "你号", "corrected": "你好" } ],
  "sensitive_hits": [ { "word": "炸弹", "category": "violence", "line": 1, "start": 7, "end": 9 } ]
}
// 响应
{
  "model": "qwen3.5:9b",
  "reachable": true,
  "suggestions": "（模型原始输出：含【复核结论】与【漏报】）",
  "issues": [
    {
      "type": "typo",
      "original": "你号",
      "corrected": "你好",
      "line": 1,
      "start": 0,
      "end": 2,
      "suggestion": "确认修改：建议改为「你好」",
      "verdict": "typo_confirmed"
    },
    {
      "type": "sensitive",
      "word": "炸弹",
      "category": "violence",
      "line": 1,
      "start": 7,
      "end": 9,
      "suggestion": "确认敏感词：删除（描述购买行为）",
      "verdict": "sensitive_confirmed"
    },
    {
      "type": "grammar",          // 模型主动发现的漏报
      "original": "由于下雨了所以比赛取消",
      "corrected": "因为下雨，比赛取消了",
      "line": 1, "start": 0, "end": 0,
      "suggestion": "语法/语义修正：建议改为「因为下雨，比赛取消了」",
      "verdict": "grammar_confirmed"
    }
  ],
  "error": null
}
```

> `suggestions` 为模型原始输出（供人工查看）；`issues` 为结构化、可机读结果，
> 每条带 `verdict`：`typo_confirmed` / `typo_rejected` / `sensitive_confirmed` /
> `sensitive_rejected` / `grammar_confirmed`。模型不可达时 `reachable` 为 `false`、
> `issues` 为 `[]`、`error` 说明原因，服务不会崩溃。

### `POST /api/pipeline`
**并发**执行 错别字检查 与 敏感词检查，再把两者结果一起交给复核步骤（Step 3，必跑）；
可选的「二次校验」（`enable_audit`）与「最终修复」（复核开关 `enable_final_suggestion`）步骤。

#### Pipeline 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│                      输入文本                                │
└─────────────────────┬───────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  Step 1: 纠错   │     │  Step 2: 敏感词  │
│  (MacBert)      │     │  (Aho-Corasick) │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │  并发执行 (ThreadPool) │
         └───────────┬───────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 复核 (LLM)                                         │
│  - 验证 Step 1 发现的错别字是否属实                           │
│  - 验证 Step 2 发现的敏感词是否真正敏感                       │
│  - 输出: issues 列表 (每个 issue 带 suggestion)              │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼  enable_audit=true?
        ┌─────────────┴─────────────┐
        │ 是                        │ 否
        ▼                           │
┌─────────────────────────────┐     │
│  Step 4: 复核验证 (LLM)     │     │
│  - 对 Step 3 的每个 issue   │     │
│    的 suggestion 再次验证    │     │
│  - 输出: audit_suggestion   │     │
└─────────────┬───────────────┘     │
              │                     │
              └──────────┬──────────┘
                         │
                         ▼  enable_final_suggestion=true?
           ┌─────────────┴─────────────┐
           │ 是                        │ 否
           ▼                           │
┌─────────────────────────────┐        │
│  Step 5: 生成最终文本 (LLM)  │        │
│  - 根据所有确认的修改建议    │        │
│  - 生成修改后的完整文本      │        │
│  - 输出: final_suggestion   │        │
└─────────────┬───────────────┘        │
              │                        │
              └──────────┬─────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      输出结果                                │
│  - original: 原始文本                                        │
│  - corrected_text: Step 1 纠错后的文本                       │
│  - issues: 复核后的修改建议列表                               │
│  - final_suggestion: 最终修改后的完整文本 (可选)              │
└─────────────────────────────────────────────────────────────┘
```

#### 关键设计说明

- **Step 1-2 并发执行**：纠错和敏感词扫描相互独立，通过线程池并行处理，提高效率
- **Step 3 的核心职责**：不是寻找新问题，而是**验证** Step 1-2 的发现是否属实
  - 敏感词必须结合上下文判断（如"炸弹"在军事新闻中可能是正常用法）
  - 错别字要确认是否真的写错（如"的地得"的使用需结合语境）
- **Step 4 (可选)**：对 Step 3 的判断再做一遍验证，增加可靠性
- **Step 5 (可选)**：根据所有确认的修改建议，生成最终的修改后文本

#### 请求参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | string | (必填) | 要处理的文本 |
| `enable_audit` | bool | null | 是否启用复核验证步骤（Step 4）。null 时回退到 `audit.enabled` |
| `enable_final_suggestion` | bool | null | 是否生成最终修改文本（Step 5）。null 时回退到 `final.enabled` |

#### 响应结构

```jsonc
{
  "original": "原始文本",
  "corrected_text": "Step 1 纠错后的文本",
  "has_issues": true,
  "typos": [/* 错别字列表 */],
  "sensitive_words": [/* 敏感词列表 */],
  "issues": [
    {
      "type": "typo",
      "original": "你号",
      "corrected": "你好",
      "line": 1, "start": 0, "end": 2,
      "suggestion": "确认修改：建议改为「你好」",
      "verdict": "typo_confirmed"
    },
    {
      "type": "grammar",          // 复核发现的漏报
      "original": "由于下雨了所以比赛取消",
      "corrected": "因为下雨，比赛取消了",
      "line": 1, "start": 0, "end": 0,
      "suggestion": "语法/语义修正：建议改为「因为下雨，比赛取消了」",
      "verdict": "grammar_confirmed"
    }
  ],
  "review_suggestions": "复核步骤的原始输出文本（供查看）",
  "audit": { /* 仅 enable_audit=true 时存在，含二次校验结论 audit_suggestions */ },
  "final_suggestion": "修改后的完整文本"  // 仅 enable_final_suggestion=true 时存在
}
```

---

## 运行测试

测试套件使用 `pytest`。纯 Python 部分（匹配器、敏感词引擎、复核客户端、流程编排）
**无需** ML 依赖或网络即可运行。MacBert 测试在 `pycorrector` / 模型不可用时会被自动跳过。

```bash
pip install pytest pydantic
pytest -q
```

> 说明：pipeline 接口测试需要 `pydantic` 以导入相关模型；MacBert 纠错测试还需
> `pycorrector` 及已下载的模型。

---

## 设计说明

- **最小依赖**：`sensitive.py` 与 `matcher.py` 仅使用标准库；Ollama 客户端
  （`reviewer.py`）使用 `urllib` 而非 `requests`。唯一的第三方包是
  `fastapi`、`uvicorn`、`pydantic` 与 `pycorrector`。
- **离线优先纠错**：模型打包进镜像，并设置 `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1`，
  启动时绝不访问网络。
- **延迟加载重依赖**：`pycorrector` 在 `TextCorrector.__init__` 内部导入，因此应用的其余部分
  （以及离线测试）无需 torch 即可运行。
- **前两步并发**：`TextPipeline` 用 `ThreadPoolExecutor` 并行执行纠错与敏感词扫描；
  扫描基于原始文本，因此不依赖（更慢的）纠错步骤。两步结果随后一起交给复核步骤。
- **复核的核心职责是验证 + 主动发现漏报**：Step 3 既验证 Step 1-2 的发现是否属实
  （剔除误报），也主动扫描全文找出工具漏报的真实问题（未被检出的错别字 / 敏感词 /
  语法语义错误），对确有问题处给出修复意见。特别是敏感词需要结合上下文判断
  （如"炸弹"在军事新闻中可能是正常用法）。每条 issue 都会带 `verdict`，
  供 pipeline 判断哪些该应用。
- **可选的复核验证**：pipeline 是否对复核结果再做一遍验证由 `enable_audit` 控制。
  关闭时直接使用 Step 3 的结果，开启时增加可靠性但多一次 LLM 调用。
- **可选的最终文本生成**：pipeline 是否生成修改后的完整文本由 `enable_final_suggestion` 控制。
  关闭时不生成最终文本，开启时根据所有确认的修改建议生成完整文本。
- **优雅降级**：若纠错模型加载失败，`/api/correct` 与 `/api/pipeline` 返回 HTTP 503
  而非崩溃；敏感词与复核接口仍可正常工作。
