"""Evaluation harness comparing answering strategies on the Korea pack.

Modes:
  - base_only : Qwen3-0.6B answers from the question alone (no retrieval).
  - rag_base  : retrieval + Qwen3-0.6B over the retrieved sources (always model).
  - pack      : the full pipeline (cards + deterministic facts + answerer +
                abstention) - what the product ships.
  - baseline  : optional stronger generic local model via EVAL_BASELINE_GGUF,
                answering from the question alone (product baseline, NOT a PAW
                interpreter).

Metrics: answerable coverage, grounded accuracy, citation correctness,
abstention precision (on unanswerable) and over-abstention (on answerable),
phonetic accuracy, latency (cold/warm), and peak RSS.

Run:  PREPARE_OFFLINE_HOME=/tmp/pfo_eval python -m eval.run_eval
"""

from __future__ import annotations

import json
import os
import resource
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import connect, init_db  # noqa: E402
from app.services import packs, retrieval  # noqa: E402
from app.services.answerer import _build_prompt, _relevant, UNSUPPORTED  # noqa: E402
from app.services.interpreter import get_interpreter  # noqa: E402
from eval.dataset import EVAL  # noqa: E402


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (rss if sys.platform == "darwin" else rss * 1024) / 1e6


def _kw_hit(answer: str, keywords: list[str]) -> bool:
    a = answer.lower()
    return all(k.lower() in a for k in keywords)


def mode_pack(conn, pack_id, q):
    from app.services import answerer

    r = answerer.answer_question(conn, pack_id, q)
    abstained = r["answer_mode"] == "abstained"
    return r["answer"], [s["source_id"] for s in r["sources"]], abstained


def mode_rag_base(conn, pack_id, q):
    cands = retrieval.search(conn, pack_id, q, limit=3)
    used = _relevant(cands)
    if not used:
        return "", [], True
    interp = get_interpreter()
    raw = interp.answer(_build_prompt(q, used)) if interp.is_available() else UNSUPPORTED
    abstained = (not raw) or raw.strip().upper().startswith(UNSUPPORTED)
    return raw, [c.source_id for c in used], abstained


def mode_base_only(conn, pack_id, q, interp=None):
    interp = interp or get_interpreter()
    if not interp.is_available():
        return "", [], True
    prompt = f"QUESTION:\n{q}\n\nAnswer in one or two sentences. If you do not know, reply {UNSUPPORTED}."
    raw = interp.answer(prompt)
    abstained = (not raw) or raw.strip().upper().startswith(UNSUPPORTED)
    return raw, [], abstained


def evaluate(mode_fn, conn, pack_id, label):
    ans = [e for e in EVAL if e["answerable"]]
    unans = [e for e in EVAL if not e["answerable"]]
    grounded = cited = answered = 0
    phon_total = phon_ok = 0
    over_abstain = 0
    correct_abstain = 0
    latencies = []

    for e in EVAL:
        t0 = time.perf_counter()
        answer, sources, abstained = mode_fn(conn, pack_id, e["q"])
        latencies.append(time.perf_counter() - t0)
        if e["answerable"]:
            if abstained:
                over_abstain += 1
            else:
                answered += 1
                if _kw_hit(answer, e["keywords"]):
                    grounded += 1
                if e["gold_source"] and e["gold_source"] in sources:
                    cited += 1
            if e["phonetic"]:
                phon_total += 1
                if not abstained and _kw_hit(answer, e["keywords"]):
                    phon_ok += 1
        else:
            if abstained:
                correct_abstain += 1

    n_ans = len(ans)
    return {
        "mode": label,
        "answerable_coverage": round(answered / n_ans, 3),
        "grounded_accuracy": round(grounded / n_ans, 3),
        "citation_correctness": round(cited / n_ans, 3),
        "over_abstention": round(over_abstain / n_ans, 3),
        "abstention_precision": round(correct_abstain / len(unans), 3) if unans else None,
        "phonetic_accuracy": round(phon_ok / phon_total, 3) if phon_total else None,
        "latency_cold_s": round(latencies[0], 3),
        "latency_warm_median_s": round(statistics.median(latencies[1:]), 3),
    }


def main() -> None:
    init_db()
    conn = connect()
    pack_id = packs.build_korea_pack(conn)

    results = []
    results.append(evaluate(mode_pack, conn, pack_id, "pack"))
    results.append(evaluate(mode_rag_base, conn, pack_id, "rag_base"))
    results.append(evaluate(mode_base_only, conn, pack_id, "base_only"))

    baseline_gguf = os.environ.get("EVAL_BASELINE_GGUF")
    if baseline_gguf:
        from llama_cpp import Llama

        class _Baseline:
            def __init__(self, path):
                self._llm = Llama(model_path=path, n_ctx=2048,
                                  n_gpu_layers=int(os.environ.get("PAW_GPU_LAYERS", "-1")),
                                  verbose=False)

            def is_available(self):
                return True

            def answer(self, body, max_tokens=320):
                out = self._llm.create_completion(prompt=body, max_tokens=max_tokens,
                                                  temperature=0.0)
                return out["choices"][0]["text"].strip()

        bl = _Baseline(baseline_gguf)
        results.append(evaluate(lambda c, p, q: mode_base_only(c, p, q, interp=bl),
                                conn, pack_id, "baseline_generic"))

    peak = _peak_rss_mb()
    report = {"peak_rss_mb": round(peak, 1), "results": results}

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_report.json")
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nPeak RSS: {peak:.0f} MB\n")
    cols = ["mode", "answerable_coverage", "grounded_accuracy", "citation_correctness",
            "over_abstention", "abstention_precision", "phonetic_accuracy",
            "latency_cold_s", "latency_warm_median_s"]
    print(f"{'metric':24}" + "".join(f"{r['mode']:>16}" for r in results))
    for c in cols[1:]:
        print(f"{c:24}" + "".join(f"{str(r[c]):>16}" for r in results))
    print(f"\nReport written to {out_path}")
    conn.close()


if __name__ == "__main__":
    main()
