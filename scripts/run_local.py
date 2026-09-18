"""Local development launcher for correct-cn (no Docker required).

This script is the easiest way to test the API on your own machine:

  * In ``mock`` mode (default) only ``fastapi``, ``uvicorn`` and ``pydantic``
    are needed -- the correction step runs a dependency-free stand-in so the
    whole service (sensitive check, review, and the full pipeline) is usable
    without installing torch / transformers / pycorrector.

  * In ``model`` mode the real MacBert corrector is used. The model is already
    baked into ``models/huggingface`` in this repo, so we point ``HF_HOME`` there
    and enable offline loading -- no download required. You still need to install
    the ML stack once (``pip install pycorrector safetensors`` plus torch).

Usage:
    python scripts/run_local.py                 # mock mode, all endpoints
    python scripts/run_local.py --mode model    # real MacBert corrector
    python scripts/run_local.py --port 9000 --reload
"""

import argparse
import os
import sys
from pathlib import Path

# The project root is the parent directory of this script (correct-cn/).
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run correct-cn locally.")
    parser.add_argument(
        "--mode",
        choices=["mock", "model"],
        default="mock",
        help="mock = no ML deps (default); model = real MacBert corrector.",
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Where config + dictionaries live (default: <repo>/data).",
    )
    parser.add_argument(
        "--ollama-url",
        default=None,
        help="Ollama base URL for the review step (default http://localhost:11434).",
    )
    parser.add_argument(
        "--ollama-api-key",
        default=None,
        help="API key for authenticated Ollama endpoints.",
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload.")
    args = parser.parse_args()

    # --- environment wiring (only fill in what is missing) ---------------- #
    os.environ.setdefault("CORRECTOR_MODE", args.mode)
    os.environ.setdefault("DATA_DIR", args.data_dir or os.path.join(ROOT, "data"))
    if args.ollama_url:
        os.environ["OLLAMA_BASE_URL"] = args.ollama_url
    if args.ollama_api_key:
        os.environ["OLLAMA_API_KEY"] = args.ollama_api_key

    if args.mode == "model":
        # Reuse the model already baked into the repo -- no re-download.
        hf_home = os.environ.get("HF_HOME") or os.path.join(
            ROOT, "models", "huggingface"
        )
        os.environ.setdefault("HF_HOME", hf_home)
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        import uvicorn  # noqa: WPS433 - runtime import keeps --help fast
    except ImportError:
        print("ERROR: uvicorn is not installed.")
        print("  Minimal deps:  pip install fastapi uvicorn pydantic")
        if args.mode == "model":
            print("  ML stack too:   pip install pycorrector safetensors torch")
        return 1

    print(
        "Starting correct-cn locally\n"
        f"  mode      = {os.environ['CORRECTOR_MODE']}\n"
        f"  data_dir  = {os.environ['DATA_DIR']}\n"
        f"  ollama    = {os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')}\n"
        f"  api_key   = {'set' if os.environ.get('OLLAMA_API_KEY') else 'not set'}\n"
        f"  url       = http://{args.host}:{args.port}/health"
    )
    if args.mode == "model":
        print(f"  hf_home   = {os.environ.get('HF_HOME')}")

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
