# correct-cn

**correct-cn** is a self-hostable Chinese text-quality-control service, delivered as a
single Docker image behind a FastAPI HTTP API. Hand it a block of Chinese text and it
returns every problem it can find — misspelled characters, sensitive words, and grammar
/ semantic errors — each with an exact location and an actionable fix.

Its whole point is context. Mechanical checks are fast but context-blind: the typo model
flags a legitimate word, the sensitive-word list fires on a neutral mention, and genuinely
broken sentences slip past both. correct-cn therefore layers a **context-aware review
(复核)** on top of them — it re-judges each finding on meaning instead of isolated tokens,
drops the false positives (误报), and hunts down what the tools missed (漏报). That makes
it a fit for any pipeline that has to clean Chinese text before it is published or indexed:
editorial proofreading, UGC moderation, and RAG preprocessing among them.

It bundles four capabilities behind a single, consistent API:

1. **Typo / error correction** — `MacBertCorrector` (`shibing624/macbert4csc-base-chinese`),
   returning the location (line number + character offset) of every correction.
2. **Sensitive-word filtering** — a fast Aho-Corasick matcher over a built-in
   dictionary (15 categories, ~79k words) with line/position reporting and a
   remote-refresh endpoint. The dictionary list is **auto-discovered** from the
   directory (nothing is hard-coded).
3. **Grammar / review (复核)** — delegates to a local LLM (Ollama, e.g.
   `qwen3.5:9b` / `qwen3.5:4b`) via a configurable prompt + output format. The
   review step re-checks the tool findings (filtering 误报 / false positives) and
   actively hunts for 漏报 (missed errors), proposing a fix for every genuine issue.
4. **Full pipeline** — chains 1 → 2 → 3 and returns a consolidated set of
   modification suggestions, plus a `has_issues` flag and optional **二次校验**
   (`enable_audit`) and **最终修复** (`enable_final_suggestion`, the 复核开关) steps.

All code comments are in English. The dependency footprint is intentionally
small: `fastapi`, `uvicorn`, `pydantic` and `pycorrector` (the last pulls in
torch / transformers). The sensitive-word engine and the Ollama client use only
the Python standard library (no `requests`). `.env` files are parsed with the
standard library too (no python-dotenv dependency).

---

## Features

- Multi-line aware: every error / hit reports its **1-based line number** and
  **0-based character offset** (start inclusive, end exclusive).
- Offline correction model: the MacBert model is baked into the image under
  `/root/.cache/huggingface` and loaded with `HF_HUB_OFFLINE=1`. No network
  needed at runtime.
- Mountable `/data` volume: holds user-editable config, the sensitive-word
  dictionaries and the API-key file. If the dictionaries/config are missing on
  first boot, the service seeds them automatically from the built-in defaults.
- Single configuration file: everything lives in one `config.json` with five
  nodes — `corrector`, `sensitive`, `review`, `audit`, `final` — so no rebuild
  is needed to change behaviour.
- Auto-discovered dictionaries: the sensitive-word engine scans its directory
  and caches the discovered list at startup; refresh operates on that list.
- Configurable review step: edit the `review` node in `config.json` (system
  prompt, user template, output format, `audit_prompt`, `final_prompt`,
  temperature, max tokens, `max_retries`).
- Pluggable LLM backend: the review / 二次校验 / final-repair calls run on a
  local Ollama model by default, or against any OpenAI-compatible API via
  `review.protocol` — so Steps 3–5 can trade cost against quality.
- Remote dictionary refresh: `/api/sensitive/refresh` can re-pull the dictionary
  files from a remote source (default: the konsheng/Sensitive-lexicon CDN).
- Optional API-key auth: API endpoints are gated by keys listed in
  `/data/.keys` (one per line). When the file is empty the API is open.

---

## Project layout

```
correct-cn/
├── app/
│   ├── main.py            # FastAPI app + endpoints + startup seeding + API-key guard
│   ├── config.py          # env vars + .env loader + unified config loading
│   ├── schemas.py         # pydantic request/response models
│   ├── corrector.py       # MacBertCorrector wrapper (lazy heavy import)
│   ├── sensitive.py       # dictionary auto-discovery / detection / remote refresh
│   ├── matcher.py         # pure-Python Aho-Corasick matcher
│   ├── reviewer.py        # LLM client (Ollama / OpenAI, urllib only): review / audit / final
│   ├── pipeline.py        # combined workflow
│   └── defaults/
│       ├── sensitive/     # 15 built-in dictionary .txt files
│   └── config.json       # single seed config (corrector / sensitive / review / audit / final)
├── tests/                 # pytest suite (offline parts run without ML stack)
├── models/
│   └── huggingface/hub/   # baked-in correction model (gitignored, ~390MB)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .dockerignore
├── .env                   # local overrides (git-ignored)
├── example.env            # committed template
├── .gitignore
├── README.md
└── README_CN.md
```

---

## Quick start (Docker)

### Option A — pull the prebuilt image

```bash
docker run -d --name correct-cn -p 8000:8000 \
  -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -e OLLAMA_MODEL=qwen3.5:9b \
  -v "$PWD/data:/data" \
  bruce1977/correct-cn:latest
```

### Option B — build from source

```bash
# Build the image (this sends the ~390MB model into the build context).
docker compose build

# Run (mounts ./data for config + dictionaries, exposes :8000).
docker compose up -d
```

Then:

```bash
curl http://localhost:8000/health
```

The first time you run it, the container seeds `/data/sensitive/*.txt` and
`/data/config.json` from the built-in defaults. On subsequent starts your
edited versions are preserved.

> **Ollama note:** the review step calls an Ollama endpoint. Point
> `OLLAMA_BASE_URL` at your Ollama (e.g. `http://host.docker.internal:11434` on
> Docker Desktop, or add `--add-host=host.docker.internal:host-gateway` on Linux).
> If Ollama is unreachable, `/api/review` and `/api/pipeline` still return
> gracefully (`reachable: false`) — they never crash the service.

### Local (non-Docker) run

The service reads an optional `.env` file at startup (parsed with the standard
library, no python-dotenv needed).

The correction model needs torch / transformers / pycorrector, which makes local
debugging heavy. Two modes are supported via `CORRECTOR_MODE`:

| `CORRECTOR_MODE` | What it does | Deps needed |
| ---------------- | ------------ | ----------- |
| `mock` (recommended for API testing) | A dependency-free stand-in corrector. Exercises every endpoint (sensitive check, review, full pipeline) without the ML stack. | `fastapi` `uvicorn` `pydantic` |
| `model` | Loads the real MacBert model. | the above **+** `pycorrector` `safetensors` `torch` |

**Fast path — test the whole API without the model:**

```bash
pip install fastapi uvicorn pydantic
python scripts/run_local.py            # CORRECTOR_MODE defaults to "mock"
curl http://localhost:8000/health      # shows "corrector_mode": "mock"
```

**Full path — real MacBert model, reusing the baked model (no re-download):**

```bash
pip install -r requirements.txt
python scripts/run_local.py --mode model
# run_local.py points HF_HOME at ./models/huggingface and sets HF_HUB_OFFLINE=1
```

You can also launch manually instead of the script:

```bash
cp example.env .env   # then edit OLLAMA_BASE_URL etc.
# mock mode (no ML deps):
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# model mode (needs ML stack; reuse the repo's baked model):
set HF_HOME=models/huggingface   # Windows
set HF_HUB_OFFLINE=1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Configuration

### `config.json` (single source of truth)

All behaviour lives in one file at `$DATA_DIR/config.json` with **five**
nodes — `corrector`, `sensitive`, and the three LLM steps `review` / `audit` /
`final`. Each LLM step carries its own `protocol` / `base_url` / `model` /
`api_key`, so different steps can use different providers (e.g. the cheap local
Ollama for review & audit, and a stronger external model for the final repair).
**Environment variables are a default, not an override:** an `OLLAMA_*` / `FINAL_*` /
`LLM_*` (or `CORRECTOR_*` / `SENSITIVE_*`) env var only fills a field when `config.json`
leaves it empty; an explicit value in `config.json` always wins.

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
    "output_format": "逐条复核【待复核清单】并输出【漏报】清单（类型=错别字/敏感词/语法），使用固定标签。",
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

- `corrector.model` — MacBert model name (also overridable via `CORRECTOR_MODEL`).
- `sensitive.auto_discover` — when true, refresh targets the files found on disk.
- `sensitive.case_insensitive` — lower-case dictionary + text before matching.
- `review.*` — Step 3 复核: protocol, model, base URL, timeout, API key, prompts,
  sampling params. Always on.
- `audit.*` — Step 4 二次校验: same shape as `review`, gated by `audit.enabled`
  (default `false`). When enabled, the pipeline re-validates the review
  suggestions before the final text.
- `final.*` — Step 5 最终修复: same shape, gated by `final.enabled` (default
  `false`). When enabled, the pipeline produces the final repaired text
  (`final_suggestion`). **Set `final.protocol="openai"` + `final.base_url` /
  `final.model` / `final.api_key` to run Step 5 on an external model** while
  Steps 3/4 stay on the local Ollama.
- `*.protocol` — `ollama` (default, native `/api/generate`) or `openai`
  (any OpenAI-compatible `/v1/chat/completions`). See below.
- `*.max_retries` — retries on transient failures (connection error / timeout /
  HTTP 5xx / 429). 4xx client errors and body-level errors are not retried.
  `0` disables retries.

`{text}` in `review.user_template` is replaced with the input text at request time.

#### Local model vs. external LLM (`protocol`)

Each of the three LLM steps (`review` / `audit` / `final`) has its own
`protocol` field, so you decide per step where the model runs. By default all
three use the **local Ollama model** (Steps 3–5 cost nothing per call — a full
pipeline makes up to three LLM calls). The common pattern: keep Steps 3/4
(review / 二次校验) on the local Ollama to save cost, and point **Step 5 (final
repair) at a stronger external model** when you want higher-quality output.

**`protocol` = the wire format, and one value maps to exactly one API.** This is
the key rule that keeps the config unambiguous:

| `protocol` | Wire format | Request URL | Body shape |
| ---------- | ----------- | ----------- | ---------- |
| `ollama` *(default)* | Ollama native | `POST {base_url}/api/generate` | `prompt` + `system` + `options` + `think` |
| `openai` | OpenAI-compatible | `POST {base_url}/v1/chat/completions` | `messages[]` + `temperature` + `max_tokens` |

So `protocol` is **not** the backend identity — it tells the code which API
shape (and request body) to use. There is no case where "the same backend is
reached through two different interfaces": `ollama` always means
`/api/generate`, `openai` always means `/v1/chat/completions`. (Ollama happens to
expose *both* a native API and an OpenAI-compatible `/v1` API; calling Ollama's
`/v1` endpoint is simply using the `openai` protocol against Ollama — the wire
format is what `protocol` records, not "which binary serves it".)

**Accepted `protocol` values.** Every LLM step node reads its own `protocol`,
normalised case-insensitively; any unrecognised value silently falls back to
`ollama` (old configs keep working):

| Config value | Normalised to | Aliases also accepted |
| ------------ | ------------- | --------------------- |
| `ollama` *(default)* | `ollama` | *(anything else)* → falls back to `ollama` |
| `openai` | `openai` | `openai_compatible`, `openai-compatible`, `oai`, `v1` |

`openai` covers every OpenAI-compatible endpoint — OpenAI, DeepSeek,
Moonshot/Kimi, Qwen (DashScope compatible mode), Zhipu, SiliconFlow, Groq,
OpenRouter, self-hosted vLLM / LM Studio / SGLang / Xinference, and Ollama's
own `/v1` API.

**How to set `base_url` per protocol.** `base_url` is the *base* address; the
code appends the right path and accepts a few forms so you don't memorise the
suffix:

| `protocol` | What `base_url` should be | Examples you can write | Request URL actually sent |
| ---------- | ------------------------- | ---------------------- | -------------------------- |
| `ollama` | the Ollama origin (host:port) | `http://localhost:11434`<br>`http://host.docker.internal:11434` | `…/api/generate` |
| `openai` | bare host | `https://api.deepseek.com` | `…/v1/chat/completions` |
| `openai` | host already ending in `/v1` | `https://api.deepseek.com/v1` | `…/v1/chat/completions` |
| `openai` | full `/v1/chat/completions` path | `https://api.openai.com/v1/chat/completions` | used as-is |

> Rule of thumb: for `ollama` give just `host:port`; for `openai` give the API
> root (or any of the `/v1` / `/v1/chat/completions` forms). All OpenAI forms
> collapse to the same `/v1/chat/completions` call. Don't append `/api/generate`
> yourself — that suffix is Ollama-only and is added automatically.

```jsonc
// Steps 3/4 stay local (default; no per-call cost)
"review": { "protocol": "ollama", "model": "qwen3.5:9b",
            "base_url": "http://host.docker.internal:11434" },
"audit":  { "enabled": true, "protocol": "ollama", "model": "qwen3.5:9b",
            "base_url": "http://host.docker.internal:11434" },

// Step 5 -> external provider (higher final-repair quality)
"final":  { "enabled": true, "protocol": "openai", "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com", "api_key": "sk-..." }

// Aliases work too: "openai_compatible" / "oai" / "v1" are all treated as openai.
"final":  { "protocol": "openai_compatible", "base_url": "https://api.deepseek.com/v1" }

// Or flip the WHOLE service to one external provider by setting protocol=openai
// on review / audit / final together (config.json wins over the LLM_* env defaults).
```

What actually differs under the `openai` protocol: the request goes out as a
`messages` array (system + user) with `temperature` / `max_tokens`, and the
answer is read from `choices[0].message.content` (falling back to
`reasoning_content` for reasoning models). Ollama-only fields (`options`,
`think`, `num_ctx`, the raw `prompt`) are **not** sent, because
OpenAI-compatible servers reject unknown parameters with HTTP 400. Everything
above the transport — prompt assembly, verdict parsing, the retry/backoff
policy, `max_retries`, probing — is shared, so the behaviour of Steps 3–5 does
not change when you switch.

- `api_key` is sent as `Authorization: Bearer <key>` (a local Ollama ignores it).
- `num_ctx` is an Ollama-only knob; an external provider's context window is set
  server-side — pick a long-context model for long text.
- `max_tokens_param` — set it to `max_completion_tokens` for OpenAI's newest
  models.
- `extra_body` — a dict merged into the OpenAI request, for provider-specific
  knobs such as `{"top_p": 0.9}`.

#### Prompt & parameter tuning

Each LLM step (`review` / `audit` / `final`) carries its **own** `system_prompt`
plus its step-specific prompt, so a step can be routed to a different provider
(e.g. an external model for Step 5) without sharing another step's persona. Edit
the relevant node in `config.json`, then restart (or hot-reload) — no rebuild
needed.

**Prompts.** All prompt fields are plain strings. `system_prompt` lives on every
LLM step; `user_template` / `output_format` are `review`-only; `audit_prompt` /
`final_prompt` belong to their steps:

| Prompt field | Controls | Placeholders |
| ------------ | -------- | ------------ |
| `system_prompt` | Persona + rules **for this step** (present on `review` / `audit` / `final`). Each step gets its own. | — |
| `user_template` | `review` only — wraps the text; in pipeline mode the numbered tool-finding checklist is appended. | `{text}` |
| `output_format` | `review` only — the **output contract** the parser reads back. | — |
| `audit_prompt` | 二次校验 pass (only when `audit.enabled`). Re-confirms or overturns each suggestion. | `{issues}` `{original_text}` |
| `final_prompt` | Final repair pass (only when `final.enabled`); emits the corrected full text. | `{original_text}` `{issues}` |

**Output contract — do not break it.** The parser extracts results from the
prompt output by fixed tags. If you rewrite a prompt, keep these tokens:

| Token | Meaning | Parsed to `verdict` |
| ----- | ------- | ------------------- |
| `【确认修改】` `原文=` `建议=` | typo confirmed | `typo_confirmed` |
| `【非错误】` | typo false positive | `typo_rejected` |
| `【确认敏感】` `建议=` | sensitive word confirmed | `sensitive_confirmed` |
| `【非敏感】` | sensitive-word false positive | `sensitive_rejected` |
| `【漏报】` `类型=错别字` | missed typo | `typo_confirmed` |
| `【漏报】` `类型=敏感词` | missed sensitive word | `sensitive_confirmed` |
| `【漏报】` `类型=语法`/`语义` | missed grammar/semantic issue | `grammar_confirmed` |
| `【复核结论】` / `【漏报】` | section markers | — |

Values use the `key=「value」` form (e.g. `建议=「你好」`). Removing a tag,
renaming a section, or dropping the `「」` brackets makes that entry
unparseable — it then falls back to a heuristic guess (or is dropped).

**Parameters.**

| Parameter | Default | What to tune |
| --------- | ------- | ------------ |
| `protocol` | `ollama` | Wire format: `ollama` (native `/api/generate`) or `openai` (any OpenAI-compatible `/v1/chat/completions`). |
| `model` / `base_url` | `qwen3.5:9b` / `...` | Model + endpoint. Env (default fallback when config omits the field): review/audit use `OLLAMA_MODEL` / `OLLAMA_BASE_URL`; final uses `FINAL_MODEL` / `FINAL_BASE_URL`. |
| `api_key` | `""` | Bearer token for the endpoint; required by most external providers. Env (default fallback): review/audit use `OLLAMA_API_KEY`; final uses `FINAL_API_KEY`. |
| `temperature` | `0.2` | Keep low (0.1–0.3) for stable verdicts; higher → more run-to-run variance. |
| `num_ctx` | `16384` | **Context window** (Ollama only). Must cover input **plus** output. Raise this first for long text; too small truncates the final text. |
| `max_tokens` | `8192` | Max output tokens. Must exceed the repaired text length, else `final_suggestion` is cut off. |
| `timeout` | `300` | Per-request seconds. Long text on a slow local GPU → raise it. |
| `max_retries` | `2` | Retries on transient failures (connection / timeout / HTTP 5xx / 429); `0` disables. |
| `max_tokens_param` | `max_tokens` | OpenAI protocol only; set to `max_completion_tokens` for OpenAI's newest models. |
| `extra_body` | — | OpenAI protocol only; extra JSON merged into the request body (e.g. `{"top_p": 0.9}`). |
| `require_json` | `false` | Leave `false` for the tagged-text format above. |

> **Long-text rule of thumb:** set `num_ctx` ≥ input tokens + output tokens.
> Roughly 1–1.5 tokens per Chinese character; a 5000-字 text plus a 5000-字
> repair needs ≳ 12000 tokens, so the default `16384` covers it. If you see a
> truncated `final_suggestion`, raise `num_ctx` and `max_tokens` first.

### Environment variables / `.env`

A `.env` file in the working directory is parsed at startup (no third-party
dependency). `example.env` is the committed template.

| Variable                | Default                                                        | Description                                            |
| ----------------------- | -------------------------------------------------------------- | ------------------------------------------------------ |
| `DATA_DIR`              | `/data`                                                        | Directory for config + sensitive dictionaries.          |
| `CORRECTOR_MODEL`       | `shibing624/macbert4csc-base-chinese`                          | MacBert correction model name (default when config omits it). |
| `CORRECTOR_MODE`        | `model`                                                        | `model` (load MacBert) or `mock` (no ML deps, for local testing). |
| `OLLAMA_PROTOCOL`       | `ollama`                                                       | Default `protocol` for review / audit. |
| `OLLAMA_BASE_URL`       | `http://localhost:11434`                                       | Default LLM base URL for review / audit.    |
| `OLLAMA_MODEL`          | `qwen3.5:9b`                                                   | Default LLM model for review / audit.         |
| `OLLAMA_TIMEOUT`        | `300`                                                          | Default per-request timeout (seconds) for review / audit. |
| `OLLAMA_API_KEY`        | *(empty)*                                                      | Bearer token for review / audit. |
| `FINAL_PROTOCOL`        | —                                                              | Default `protocol` for final (Step 5 external model). |
| `FINAL_BASE_URL`        | —                                                              | Default LLM base URL for final.             |
| `FINAL_MODEL`           | —                                                              | Default LLM model for final.                  |
| `FINAL_TIMEOUT`         | —                                                              | Default per-request timeout (seconds) for final.      |
| `FINAL_API_KEY`         | —                                                              | Bearer token for final.            |
| `LLM_PROTOCOL`          | `ollama`                                                       | *(legacy fallback)* Default `protocol` for review / audit / final. |
| `LLM_BASE_URL`          | `http://localhost:11434`                                       | *(legacy fallback)* Default LLM base URL for all steps.    |
| `LLM_MODEL`             | `qwen3.5:9b`                                                   | *(legacy fallback)* Default LLM model for all steps.         |
| `LLM_TIMEOUT`           | `300`                                                          | *(legacy fallback)* Default per-request timeout for all steps. |
| `LLM_API_KEY`           | *(empty)*                                                      | *(legacy fallback)* Default Bearer token for all steps. |
| `SENSITIVE_REMOTE_BASE` | `https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/` | Source for `/api/sensitive/refresh`. |
| `CONFIG_PATH`           | *(resolved as `$DATA_DIR/config.json`)*                       | Override the config file path.                         |
| `API_KEYS_FILE`         | `*(resolved as `$DATA_DIR/.keys`)*`                              | API-key file (one key per line). Empty = open API.     |

> **Env vars are a default, not an override.** For every field, `config.json`
> wins: an env var is only applied when `config.json` leaves that field empty.
> Precedence (lowest → highest): `OLLAMA_*` (shared local model for review +
> audit) < `FINAL_*` (Step 5 external model) < `LLM_*` (legacy catch-all).
> To point one step (e.g. Step 5) at a different provider, set that step's node
> in `config.json` rather than adding more env vars.

### API-key authentication

API endpoints (`/api/*`) are protected by a simple key check. Put one key per
line in `$DATA_DIR/.keys`; `#` starts a comment and blank lines are ignored.
When the file is empty (the default), the API is open. Provide the key via
`Authorization: Bearer <key>` or the `X-API-Key: <key>` header.

```bash
# require a key
echo "my-secret-key" >> data/.keys
curl -H "Authorization: Bearer my-secret-key" http://localhost:8000/api/correct \
  -H 'Content-Type: application/json' -d '{"text":"我爱北京天安们"}'

# re-read the key file without restarting
curl -X POST http://localhost:8000/api/keys/reload
```

`/health` and `/` are always open.

---

## API reference

### `GET /health`
Returns service status, loaded model names, Ollama reachability and dictionary stats.

### `POST /api/correct`
```jsonc
// request
{ "text": "我爱北京天安们" }
// response
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
// request
{ "text": "今天去买了一个炸弹", "categories": ["violence"] }
// response
{
  "is_sensitive": true,
  "count": 1,
  "sensitive_words": [
    { "word": "炸弹", "category": "violence", "line": 1, "start": 7, "end": 9 }
  ],
  "categories_checked": ["violence"]
}
```
Omit `categories` to scan all loaded categories.

### `POST /api/sensitive/refresh`
```jsonc
// request
{ "remote": true, "force": false }
// response
{
  "updated": ["violence.txt", "politics.txt", "..."],
  "failed": [],
  "categories": ["violence", "politics", "..."],
  "total_words": 79282
}
```
- `remote=true` fetches from `SENSITIVE_REMOTE_BASE` (skips files already present
  unless `force=true`).
- `remote=false` simply reloads the dictionaries from disk.

### `POST /api/review`
The review step is a 复核 of the tool findings. You may pass `text` alone (the LLM
then scans the whole text for 漏报), or also pass `typos` / `sensitive_hits` (the LLM
then validates each finding, filtering 误报, and still scans for 漏报).

```jsonc
// request (standalone full-text review)
{ "text": "你号，这里有一个炸弹" }

// request (validate tool findings — typos first, then sensitive_hits)
{
  "text": "你号，这里有一个炸弹",
  "typos": [ { "line": 1, "start": 0, "end": 2, "original": "你号", "corrected": "你好" } ],
  "sensitive_hits": [ { "word": "炸弹", "category": "violence", "line": 1, "start": 7, "end": 9 } ]
}
// response
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
      "type": "grammar",          // 漏报 found by the LLM
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

> `suggestions` is the raw LLM text (for human inspection); `issues` is the
> structured, machine-readable result. Each issue carries a `verdict`:
> `typo_confirmed` / `typo_rejected` / `sensitive_confirmed` / `sensitive_rejected`
> / `grammar_confirmed`. When the model is unreachable, `reachable` is `false`,
> `issues` is `[]` and `error` describes the failure — the service never crashes.

### `POST /api/pipeline`
Runs correction and sensitive check **concurrently**, then passes both results to
the review step, which validates the findings. Optional audit and final text
generation steps are available.

#### Pipeline Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                      Input Text                             │
└─────────────────────┬───────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
          ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  Step 1: Typo   │     │  Step 2: Sensitive│
│  Correction     │     │  Word Detection  │
│  (MacBert)      │     │  (Aho-Corasick)  │
└────────┬────────┘     └────────┬────────┘
         │                       │
         │  Concurrent (ThreadPool) │
         └───────────┬───────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  Step 3: Review (LLM)                                       │
│  - Validate typos from Step 1                               │
│  - Validate sensitive words from Step 2                     │
│  - Output: issues list with suggestions                     │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ▼  enable_audit=true?
        ┌─────────────┴─────────────┐
        │ Yes                       │ No
        ▼                           │
┌─────────────────────────────┐     │
│  Step 4: Audit (LLM)        │     │
│  - Re-validate each issue's │     │
│    suggestion from Step 3   │     │
│  - Output: audit_suggestion │     │
└─────────────┬───────────────┘     │
              │                     │
              └──────────┬──────────┘
                         │
                         ▼  enable_final_suggestion=true?
           ┌─────────────┴─────────────┐
           │ Yes                       │ No
           ▼                           │
┌─────────────────────────────┐        │
│  Step 5: Final Text (LLM)   │        │
│  - Generate corrected text  │        │
│    based on confirmed issues│        │
│  - Output: final_suggestion │        │
└─────────────┬───────────────┘        │
              │                        │
              └──────────┬─────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      Output                                 │
│  - original: input text                                     │
│  - corrected_text: Step 1 corrected text                    │
│  - issues: validated issue list with suggestions            │
│  - final_suggestion: final corrected text (optional)        │
└─────────────────────────────────────────────────────────────┘
```

#### Key Design Points

- **Steps 1-2 run concurrently**: correction and sensitive scanning are independent,
  processed in parallel via thread pool for efficiency.
- **Step 3 — review（复核）**: combines the tool findings into a numbered
  【待复核清单】 and asks the LLM to (a) validate each finding, filtering
  **误报** (false positives, e.g. a name/term the corrector mis-flagged), and
  (b) actively hunt for **漏报** (missed typos / sensitive words / grammar-semantic
  errors), proposing a fix for every genuine problem. Each issue carries a
  machine-readable `verdict` (typo_confirmed / typo_rejected / sensitive_confirmed /
  sensitive_rejected / grammar_confirmed).
  - Sensitive words must be judged in context (e.g., "炸弹" may be normal in military news)
  - Typos need confirmation (e.g., "的地得" usage depends on context)
- **Step 4 — 二次校验 (optional, `enable_audit`)**: re-validates Step 3's suggestions
  for extra reliability; the audited verdict is folded back into each issue.
- **Step 5 — 最终修复 (复核开关, `enable_final_suggestion`)**: combines the review
  opinions with the original text and produces `final_suggestion` — the repaired text.
  It fixes confirmed errors **and** obvious grammar / semantic problems, but deliberately
  does **not** polish wording or style (不润色文笔). Only `verdict`-confirmed issues are
  applied, so false positives are dropped automatically.
- **Retry on flaky Ollama**: `_generate` retries transient failures (connection error,
  timeout, HTTP 5xx, 429) with exponential backoff (`review.max_retries`, default 2).
  4xx client errors and body-level errors are not retried.

#### Request Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `text` | string | (required) | Text to process |
| `enable_audit` | bool | null | Enable 二次校验 step (Step 4). null falls back to `audit.enabled`. |
| `enable_final_suggestion` | bool | null | Enable 复核开关 / final repair (Step 5). null falls back to `final.enabled`. |

#### Response Structure

```jsonc
{
  "original": "input text",
  "corrected_text": "Step 1 corrected text",
  "has_issues": true,
  "typos": [/* typo list */],
  "sensitive_words": [/* sensitive word list */],
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
      "type": "grammar",          // 漏报 found by review
      "original": "由于下雨了所以比赛取消",
      "corrected": "因为下雨，比赛取消了",
      "line": 1, "start": 0, "end": 0,
      "suggestion": "语法/语义修正：建议改为「因为下雨，比赛取消了」",
      "verdict": "grammar_confirmed"
    }
  ],
  "review_suggestions": "raw LLM review text (复核结论 + 漏报), for inspection",
  "audit": { /* present only when enable_audit=true */ },
  "final_suggestion": "final repaired text"  // only when enable_final_suggestion=true
}
```

---

## Running the tests

The suite uses `pytest`. The pure-Python parts (matcher, sensitive engine,
reviewer, pipeline orchestration) run **without** the ML stack or network.
The MacBert test is skipped automatically when `pycorrector` / the model is not
available.

```bash
pip install pytest pydantic
pytest -q
```

> Note: `pydantic` is required to import the schemas used by the pipeline
> endpoint tests. The MacBert corrector test additionally needs
> `pycorrector` + the downloaded model.

---

## Design notes

- **Minimal dependencies.** `sensitive.py` and `matcher.py` use only the
  standard library. The Ollama client (`reviewer.py`) uses `urllib`, not
  `requests`. The only third-party packages are `fastapi`, `uvicorn`,
  `pydantic` and `pycorrector`.
- **Offline-first correction.** The model is baked into the image and
  `HF_HUB_OFFLINE=1` / `TRANSFORMERS_OFFLINE=1` are set so startup never
  touches the network.
- **Lazy heavy import.** `pycorrector` is imported inside `TextCorrector.__init__`
  so the rest of the application (and the offline tests) can run without torch.
- **Concurrent first steps.** `TextPipeline` runs correction and the sensitive-word
  scan in parallel via `ThreadPoolExecutor`; the scan operates on the original text
  so it has no dependency on the (slower) correction step. Both results are then
  handed to the review step together.
- **Review 复核, not just validate.** Step 3 validates the tool findings (filtering
  误报 / false positives) **and** actively finds 漏报 (missed typos, sensitive words,
  and grammar/semantic errors), proposing a fix for each. Every issue carries a
  `verdict` so the pipeline knows what to apply. Sensitive words must be judged in
  context (e.g., "炸弹" may be normal in military news).
- **Optional 二次校验 (audit) step.** Whether the pipeline re-validates the review
  results is controlled by `enable_audit`. When off, Step 3's results are used
  directly; when on, it adds reliability at the cost of an extra LLM call.
- **Optional 最终修复 (final text).** The 复核开关 `enable_final_suggestion` controls
  whether the pipeline produces `final_suggestion`. When off, only `issues` are
  returned; when on, it repairs confirmed errors + grammar/semantic problems based on
  all confirmed issues, without polishing wording/style.
- **Graceful degradation.** If the correction model fails to load,
  `/api/correct` and `/api/pipeline` return HTTP 503 instead of crashing; the
  sensitive-word and review endpoints keep working.
