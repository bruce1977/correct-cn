# correct-cn 中文文本校对服务

一个可通过 Docker 部署的 FastAPI 服务，提供中文文本质量检查能力。它将三大功能整合在统一、一致的 API 之下：

1. **中文错别字矫正** —— 使用 `MacBertCorrector`（`shibing624/macbert4csc-base-chinese`），
   并返回每个纠正处的位置（行号 + 字符偏移）。
2. **中文敏感词过滤** —— 基于内置词库（15 个分类，约 7.9 万词）的 Aho-Corasick
   高效匹配，返回行号/位置，并提供远程刷新接口。**词库列表在启动时按目录
   自动发现**（不写死），并缓存到磁盘。
3. **语法/错别字初级复核** —— 调用本地大模型（Ollama，如 `qwen3.5:9b` / `qwen3.5:4b`），
   提示词与输出格式可通过配置文件灵活定制。
4. **完整流程** —— 串联 错别字检查 → 敏感词检查 → 文字复核，返回综合修改意见，
   并带有 `has_issues` 标记。可选的「复核」步骤对复核结果再做一遍验证，
   可选的「最终修改建议」步骤生成修改后的完整文本。

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
- **单一配置文件**：所有行为集中在 `config.json` 的 `corrector` / `sensitive` /
  `review` 三个节点中，修改无需重新构建镜像。
- **词库自动发现 + 启动缓存**：敏感词引擎扫描目录并缓存发现的文件列表；
  刷新操作也基于该列表，不再写死文件名。
- **可配置复核步骤**：编辑 `config.json` 的 `review` 节点（系统提示词、用户模板、
  输出格式、总结模板、temperature、max_tokens）即可。
- **远程词库刷新**：`/api/sensitive/refresh` 可从远程源（默认 konsheng/Sensitive-lexicon CDN）
  重新拉取词库文件。
- **可选 API Key 鉴权**：API 接口由 `/data/keys.txt`（每行一个 key）保护；文件为空时接口开放。

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
│   ├── reviewer.py        # Ollama 客户端（仅 urllib）+ 总结调用
│   ├── pipeline.py        # 组合工作流
│   └── defaults/
│       ├── sensitive/     # 15 个内置词库 .txt 文件
│       └── configs/
│           └── config.json   # 单一配置（corrector / sensitive / review）
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

首次运行时，容器会把 `/data/sensitive/*.txt` 与 `/data/configs/config.json`
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

所有行为集中在 `$DATA_DIR/configs/config.json`，包含三个节点。每个值均可被
环境变量 / `.env` 覆盖。

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
    "model": "qwen3.5:9b",
    "base_url": "http://host.docker.internal:11434",
    "timeout": 120,
    "system_prompt": "你是一名中文审校助手……",
    "user_template": "以下是要审校的文本及自动检测结果，请逐条验证：\n\n---\n{text}\n---",
    "output_format": "请按以下结构回答：\n\n【错别字验证】\n- 原文「xxx」→ 建议「xxx」（确认/非错误，原因：xxx）\n\n【敏感词验证】\n- 「xxx」（分类：xxx）→ 确认敏感/非敏感（原因：xxx）\n\n【需保留的修改】\n（只列出确认需要修改的问题，每条一行）",
    "audit_prompt": "请根据以下修改建议列表，逐条验证是否确实需要修改……",
    "final_prompt": "请根据以下确认的修改建议，生成修改后的完整文本……",
    "temperature": 0.3,
    "max_tokens": 2048,
    "require_json": false,
    "enable_review": false,
    "enable_final_suggestion": false
  }
}
```

- `corrector.model` —— MacBert 模型名（也可由 `CORRECTOR_MODEL` 覆盖）。
- `sensitive.auto_discover` —— 为 `true` 时，刷新操作针对磁盘上发现的文件。
- `sensitive.case_insensitive` —— 匹配前对词库与文本统一小写。
- `review.*` —— Ollama 模型/地址/超时、提示词、采样参数。
- `review.enable_audit` —— 为 `true` 时，pipeline 会对复核结果再做一遍验证
  （Step 4），为 `false` 时跳过此步骤。
- `review.enable_final_suggestion` —— 为 `true` 时，pipeline 会根据所有确认的
  修改建议生成最终的修改后文本（Step 5），为 `false` 时跳过此步骤。
- `review.audit_prompt` —— 复核验证的提示词模板。
- `review.final_prompt` —— 生成最终文本的提示词模板。

`user_template` 中的 `{text}` 会在请求时被替换为输入文本。

### 环境变量 / `.env`

启动时解析工作目录下的 `.env` 文件（无第三方依赖）。`example.env` 为提交模板。

| 变量                    | 默认值                                                                          | 说明                                       |
| ----------------------- | ------------------------------------------------------------------------------- | ------------------------------------------ |
| `DATA_DIR`              | `/data`                                                                         | 配置与敏感词词库所在目录。                  |
| `CORRECTOR_MODEL`       | `shibing624/macbert4csc-base-chinese`                                           | MacBert 纠错模型名称（覆盖 config）。       |
| `CORRECTOR_MODE`        | `model`                                                                         | `model`（加载真实模型）或 `mock`（无 ML 依赖，便于本地测试）。 |
| `OLLAMA_BASE_URL`       | `http://localhost:11434`                                                        | Ollama API 地址。                          |
| `OLLAMA_MODEL`          | `qwen3.5:9b`                                                                    | 复核步骤使用的模型。                        |
| `OLLAMA_TIMEOUT`        | `60`                                                                            | Ollama 请求超时（秒）。                    |
| `SENSITIVE_REMOTE_BASE` | `https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/`     | `/api/sensitive/refresh` 的远程词库源。     |
| `CONFIG_PATH`           | *(自动解析为 `$DATA_DIR/configs/config.json`)*                                    | 覆盖配置文件路径。                          |
| `API_KEYS_FILE`         | *(自动解析为 `$DATA_DIR/keys.txt`)*                                              | API Key 文件（每行一个 key，空则接口开放）。|

### API Key 鉴权

API 接口（`/api/*`）采用简单的 Key 校验。在 `$DATA_DIR/keys.txt` 中每行放一个 key，
`#` 开头为注释，空行忽略。文件为空（默认）时接口开放。通过
`Authorization: Bearer <key>` 或 `X-API-Key: <key>` 请求头提供 key。

```bash
# 开启鉴权
echo "my-secret-key" >> data/keys.txt
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
```jsonc
// 请求
{ "text": "你号，这里有一个炸弹" }
// 响应
{
  "model": "qwen3.5:9b",
  "reachable": true,
  "issues": [
    {
      "type": "typo",
      "original": "你号",
      "corrected": "你好",
      "line": 1,
      "start": 0,
      "end": 2,
      "suggestion": "错别字，建议修改"
    },
    {
      "type": "sensitive",
      "word": "炸弹",
      "category": "violence",
      "line": 1,
      "start": 7,
      "end": 9,
      "suggestion": "敏感词，需根据上下文判断"
    }
  ],
  "error": null
}
```

### `POST /api/pipeline`
**并发**执行 错别字检查 与 敏感词检查，再把两者结果一起交给复核步骤；
可选的「复核」和「最终修改建议」步骤。

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
                      ▼  enable_review=true?
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
| `enable_audit` | bool | null | 是否启用复核验证步骤（Step 4）。null 时使用 config.json 设置 |
| `enable_final_suggestion` | bool | null | 是否生成最终修改文本（Step 5）。null 时使用 config.json 设置 |

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
      "line": 1,
      "start": 0,
      "end": 2,
      "suggestion": "确认修改",
      "audit_suggestion": "确认修改"  // 仅 enable_review=true 时存在
    }
  ],
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
- **复核的核心职责是验证**：Step 3 不是寻找新问题，而是验证 Step 1-2 的发现是否属实。
  特别是敏感词需要结合上下文判断（如"炸弹"在军事新闻中可能是正常用法）。
- **可选的复核验证**：pipeline 是否对复核结果再做一遍验证由 `enable_audit` 控制。
  关闭时直接使用 Step 3 的结果，开启时增加可靠性但多一次 LLM 调用。
- **可选的最终文本生成**：pipeline 是否生成修改后的完整文本由 `enable_final_suggestion` 控制。
  关闭时不生成最终文本，开启时根据所有确认的修改建议生成完整文本。
- **优雅降级**：若纠错模型加载失败，`/api/correct` 与 `/api/pipeline` 返回 HTTP 503
  而非崩溃；敏感词与复核接口仍可正常工作。
