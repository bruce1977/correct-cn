"""Long-text (3000-6000 字) multi-scenario stress test for correct-cn.

Drives the REAL review / pipeline code against a live Ollama instance. The
MacBert corrector and Aho-Corasick sensitive scan cannot run in this sandbox
(their native deps are absent), so we inject planted tool-findings via
lightweight fakes and let the LLM review (复核) + final-repair path do the work
-- which is exactly what we want to stress-test on long text.

Usage:
    python scripts/longtext_test.py
"""

import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from app.reviewer import TextReviewer
from app.pipeline import TextPipeline

TEST_DIR = os.path.join(REPO, "tests", "longtext")

# --- config: point at the reachable Ollama, raise ctx/tokens for long text ---
CFG_PATH = os.path.join(REPO, "data", "config.json")
with open(CFG_PATH, encoding="utf-8") as fh:
    cfg = json.load(fh)
review_cfg = cfg["review"]
review_cfg["base_url"] = "http://localhost:11434"
review_cfg["model"] = "qwen3.5:9b"
review_cfg["num_ctx"] = 16384          # long text needs a bigger window
review_cfg["max_tokens"] = 8192        # must fit a full ~4000-char re-write
review_cfg["timeout"] = 600
review_cfg["max_retries"] = 2


def locate(text: str, sub: str):
    """Return {line,start,end} for the first occurrence of ``sub`` in ``text``."""
    idx = text.find(sub)
    if idx < 0:
        return None
    line_start = text.rfind("\n", 0, idx)
    line_no = text[:idx].count("\n") + 1
    start = idx - (line_start + 1 if line_start >= 0 else 0)
    return {"line": line_no, "start": start, "end": start + len(sub)}


class FakeCorrector:
    """Stand-in for the MacBert corrector; emits the planted typos only."""
    def __init__(self, typos):
        self._typos = typos
    def correct(self, text):
        errors = []
        for t in self._typos:
            pos = locate(text, t["original"])
            if not pos:
                continue
            errors.append({
                "line": pos["line"], "start": pos["start"], "end": pos["end"],
                "original": t["original"], "corrected": t["corrected"],
            })
        return {"original": text, "corrected": text, "errors": errors}


class FakeSensitive:
    """Stand-in for the Aho-Corasick engine; emits the planted hits only."""
    def __init__(self, hits):
        self._hits = hits
    def check_text(self, text, categories=None):
        words = []
        for h in self._hits:
            pos = locate(text, h["word"])
            if not pos:
                continue
            words.append({
                "word": h["word"], "category": h["category"],
                "line": pos["line"], "start": pos["start"], "end": pos["end"],
            })
        return {
            "is_sensitive": bool(words),
            "count": len(words),
            "sensitive_words": words,
        }


def summarize_issues(issues):
    by_type = {}
    for it in issues:
        by_type.setdefault(it.type, {}).setdefault(it.verdict or "unknown", 0)
        by_type[it.type][it.verdict or "unknown"] += 1
    return by_type


def anchors_ok(original: str, final: str):
    """Check the final text keeps the opening and closing anchors (no drop)."""
    if not final:
        return False, "empty"
    head = original[:18].strip()
    tail = original[-18:].strip()
    problems = []
    if head and head not in final:
        problems.append("head-anchor-missing")
    if tail and tail not in final:
        problems.append("tail-anchor-missing")
    return (not problems), (",".join(problems) if problems else "ok")


def run_sample(key, meta, text):
    print(f"\n=== [{key}] {meta.get('title','')} ({len(text)} 字) ===")
    reviewer = TextReviewer(
        base_url=review_cfg["base_url"], model=review_cfg["model"],
        timeout=review_cfg["timeout"], config=review_cfg,
    )
    corrector = FakeCorrector(meta.get("typos", []))
    sensitive = FakeSensitive(meta.get("sensitive_hits", []))
    pipeline = TextPipeline(corrector, sensitive, reviewer, review_config=review_cfg)

    # 1) Full pipeline with final-repair switch on (real flow: tool -> review -> final)
    t0 = time.time()
    try:
        res = pipeline.run(text, enable_audit=False, enable_final_suggestion=True)
        pipe_err = None
    except Exception as exc:  # noqa: BLE001
        res = None
        pipe_err = f"{type(exc).__name__}: {exc}"
    dt_pipe = time.time() - t0

    # 2) Standalone review (no tool findings) -> pure 漏报 pass on long text
    t1 = time.time()
    try:
        standalone = reviewer.review(text)
        stand_err = None
    except Exception as exc:  # noqa: BLE001
        standalone = None
        stand_err = f"{type(exc).__name__}: {exc}"
    dt_stand = time.time() - t1

    rec = {
        "key": key,
        "title": meta.get("title", ""),
        "char_count": len(text),
        "pipeline_error": pipe_err,
        "standalone_error": stand_err,
        "pipeline_seconds": round(dt_pipe, 1),
        "standalone_seconds": round(dt_stand, 1),
    }

    if res:
        issues = res["issues"]
        rec["issues_total"] = len(issues)
        rec["issues_by_type"] = summarize_issues(issues)
        rec["grammar_missed_count"] = sum(1 for i in issues if i.type == "grammar")
        rec["has_issues"] = res["has_issues"]
        rec["review_reachable"] = (res.get("review_suggestions") is not None) or (pipe_err is None)

        # map planted typos -> verdicts
        typo_report = []
        for t in meta.get("typos", []):
            match = next((i for i in issues if i.type == "typo" and i.original == t["original"]), None)
            typo_report.append({
                "original": t["original"], "corrected": t["corrected"],
                "found": match is not None,
                "verdict": match.verdict if match else None,
                "note": t.get("note", ""),
            })
        rec["typo_report"] = typo_report

        sens_report = []
        for h in meta.get("sensitive_hits", []):
            match = next((i for i in issues if i.type == "sensitive" and i.word == h["word"]), None)
            sens_report.append({
                "word": h["word"], "expect": h.get("expect"),
                "found": match is not None,
                "verdict": match.verdict if match else None,
                "note": h.get("note", ""),
            })
        rec["sensitive_report"] = sens_report

        # final text quality
        final = res.get("final_suggestion") or ""
        rec["final_len"] = len(final)
        rec["final_ratio"] = round(len(final) / max(1, len(text)), 3)
        ok, anchor_note = anchors_ok(text, final)
        rec["final_anchors"] = anchor_note
        rec["expected_grammar"] = meta.get("expected_grammar", [])

    if standalone:
        sissues = standalone.get("issues", [])
        rec["standalone_reachable"] = standalone.get("reachable")
        rec["standalone_issues_total"] = len(sissues)
        rec["standalone_grammar_missed"] = sum(1 for i in sissues if i.type == "grammar")
        rec["standalone_by_type"] = summarize_issues(sissues)
        rec["standalone_error_detail"] = standalone.get("error")

    # console summary
    if res:
        print(f"  pipeline: {rec['issues_total']} issues, final_len={rec.get('final_len')} "
              f"ratio={rec.get('final_ratio')} anchors={rec.get('final_anchors')} ({dt_pipe:.1f}s)")
        for tr in rec.get("typo_report", []):
            print(f"    typo {tr['original']}->{tr['corrected']}: found={tr['found']} verdict={tr['verdict']}")
        for sr in rec.get("sensitive_report", []):
            print(f"    sensitive {sr['word']}: expect={sr['expect']} found={sr['found']} verdict={sr['verdict']}")
        print(f"    grammar(漏报) via pipeline={rec.get('grammar_missed_count')} | "
              f"standalone={rec.get('standalone_grammar_missed')}")
    if pipe_err:
        print(f"  PIPELINE ERROR: {pipe_err}")
    return rec


def main():
    with open(os.path.join(TEST_DIR, "samples_meta.json"), encoding="utf-8") as fh:
        meta_all = json.load(fh)
    results = []
    for key, meta in meta_all.items():
        txt_path = os.path.join(TEST_DIR, f"{key}.txt")
        with open(txt_path, encoding="utf-8") as fh:
            text = fh.read()
        results.append(run_sample(key, meta, text))

    out = os.path.join(TEST_DIR, "report.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    print(f"\nReport written to {out}")


if __name__ == "__main__":
    main()
