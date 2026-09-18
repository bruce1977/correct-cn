# correct-cn

A Docker-deployable FastAPI service for Chinese text quality control. It bundles
three capabilities behind a single, consistent API:

1. **Typo / error correction** — `MacBertCorrector` (`shibing624/macbert4csc-base-chinese`),
   returning the location (line number + character offset) of every correction.
2. **Sensitive-word filtering** — a fast Aho-Corasick matcher over a built-in
   dictionary (15 categories, ~79k words) with line/position reporting and a
   remote-refresh endpoint. The dictionary list is **auto-discovered** from the
   directory (nothing is hard-coded).
3. **Grammar / wording review** — delegates to a local LLM (Ollama, e.g.
   `qwen3.5:9b` / `qwen3.5:4b`) via a configurable prompt + output format.
4. **Full pipeline** — chains 1 → 2 → 3 and returns a consolidated set of
   modification suggestions, plus a `has_issues` flag and an optional second
   LLM "summary" call that consolidates everything into the final suggestion.

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
- Single configuration file: everything lives in one `config.json` with three
  nodes — `corrector`, `sensitive`, `review` — so no rebuild is needed to change
  behaviour.
- Auto-discovered dictionaries: the sensitive-word engine scans its directory
  and caches the discovered list at startup; refresh operates on that list.
- Configurable review step: edit the `review` node in `config.json` (system
  prompt, user template, output format, summary prompt, temperature, max tokens).
- Remote dictionary refresh: `/api/sensitive/refresh` can re-pull the dictionary
  files from a remote source (default: the konsheng/Sensitive-lexicon CDN).
- Optional API-key auth: API endpoints are gated by keys listed in
  `/data/keys.txt` (one per line). When the file is empty the API is open.

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
│   ├── reviewer.py        # Ollama client (urllib only) + summary call
│   ├── pipeline.py        # combined workflow
│   └── defaults/
│       ├── sensitive/     # 15 built-in dictionary .txt files
│       └── configs/
│           └── config.json   # single config (corrector / sensitive / review)
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
`/data/configs/config.json` from the built-in defaults. On subsequent starts your
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

All behaviour lives in one file at `$DATA_DIR/configs/config.json` with three
nodes. Every value can be overridden by an environment variable / `.env` entry.

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
    "system_prompt": "你是一名专业的中文审校助手……",
    "user_template": "请审校以下文本：\n\n{text}",
    "output_format": "请用中文回答，按以下结构输出……",
    "summary_prompt": "综合{review}给出最终修改建议……（占位符：{corrected_text} {typos} {sensitive} {review}）",
    "temperature": 0.3,
    "max_tokens": 2048,
    "require_json": false,
    "enable_summary": true
  }
}
```

- `corrector.model` — MacBert model name (also overridable via `CORRECTOR_MODEL`).
- `sensitive.auto_discover` — when true, refresh targets the files found on disk.
- `sensitive.case_insensitive` — lower-case dictionary + text before matching.
- `review.*` — Ollama model/base/timeout, prompts, sampling params.
- `review.enable_summary` — when true, the pipeline makes a **second** LLM call to
  consolidate the findings into the final suggestion; when false it reuses the
  review text directly.
- `review.summary_prompt` — template for that second call. Placeholders:
  `{corrected_text}`, `{typos}`, `{sensitive}`, `{review}`.

`{text}` in `user_template` is replaced with the input text at request time.

### Environment variables / `.env`

A `.env` file in the working directory is parsed at startup (no third-party
dependency). `example.env` is the committed template.

| Variable                | Default                                                        | Description                                            |
| ----------------------- | -------------------------------------------------------------- | ------------------------------------------------------ |
| `DATA_DIR`              | `/data`                                                        | Directory for config + sensitive dictionaries.          |
| `CORRECTOR_MODEL`       | `shibing624/macbert4csc-base-chinese`                          | MacBert correction model name (overrides config).      |
| `CORRECTOR_MODE`        | `model`                                                        | `model` (load MacBert) or `mock` (no ML deps, for local testing). |
| `OLLAMA_BASE_URL`       | `http://localhost:11434`                                       | Base URL of the Ollama API.                            |
| `OLLAMA_MODEL`          | `qwen3.5:9b`                                                   | Model used for the review step.                        |
| `OLLAMA_TIMEOUT`        | `60`                                                           | Request timeout (seconds) for Ollama.                  |
| `SENSITIVE_REMOTE_BASE` | `https://cdn.jsdelivr.net/gh/konsheng/Sensitive-lexicon@master/Vocabulary/` | Source for `/api/sensitive/refresh`. |
| `CONFIG_PATH`           | *(resolved as `$DATA_DIR/configs/config.json`)*                | Override the config file path.                         |
| `API_KEYS_FILE`         | `*(resolved as `$DATA_DIR/keys.txt`)*`                         | API-key file (one key per line). Empty = open API.     |

### API-key authentication

API endpoints (`/api/*`) are protected by a simple key check. Put one key per
line in `$DATA_DIR/keys.txt`; `#` starts a comment and blank lines are ignored.
When the file is empty (the default), the API is open. Provide the key via
`Authorization: Bearer <key>` or the `X-API-Key: <key>` header.

```bash
# require a key
echo "my-secret-key" >> data/keys.txt
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
```jsonc
// request
{ "text": "他跑的很快，因为我慢。" }
// response
{
  "model": "qwen3.5:9b",
  "reachable": true,
  "suggestions": "……（模型给出的审校意见）",
  "error": null
}
```

### `POST /api/pipeline`
Runs correction and sensitive check **concurrently**, then passes both results to
the review step, which may optionally make a second (summary) LLM call.

- The first two steps (correction + sensitive scan) run in parallel via a thread
  pool; the sensitive scan operates on the **original** text.
- `has_issues` is `true` when any typo or sensitive word was found.
- `enable_summary` (request parameter) decides whether a second LLM call
  consolidates everything into `final_suggestion`. `null` (default) uses the
  `config.json -> review.enable_summary` setting.
- When `enable_summary` is false (or the model is unreachable), `final_suggestion`
  reuses the review text and `summary` is `null`.

```jsonc
// request
{ "text": "你号，这里有一个炸弹", "enable_summary": true }
// response
{
  "original": "你号，这里有一个炸弹",
  "corrected_text": "你好，这里有一个炸弹",
  "has_issues": true,
  "typos": [ { "line": 1, "start": 0, "end": 2, "original": "你号", "corrected": "你好" } ],
  "sensitive_words": [ { "word": "炸弹", "category": "violence", "line": 1, "start": 7, "end": 9 } ],
  "review": { "model": "qwen3.5:9b", "reachable": true, "suggestions": "……", "error": null },
  "summary": { "model": "qwen3.5:9b", "reachable": true, "suggestions": "……", "error": null },
  "final_suggestion": "……"
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
- **Per-request summary toggle.** Whether the pipeline makes a second LLM call to
  produce the consolidated `final_suggestion` is chosen per request with
  `enable_summary` (falling back to `review.enable_summary` in config). When off,
  the review text is reused and no extra model call is made.
- **Graceful degradation.** If the correction model fails to load,
  `/api/correct` and `/api/pipeline` return HTTP 503 instead of crashing; the
  sensitive-word and review endpoints keep working.
