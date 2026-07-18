"""Evaluate neural-only PAW Offline graph variants on frozen QA rubrics."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

from app.config import get_settings
from app.db import connect, init_db
from app.services import neural_answer_graph, program_registry, program_runtime
from eval.universal_qa.runner import load_benchmark
from eval.universal_qa.score import score_dataset


def _setup() -> None:
    os.environ["PREPARE_OFFLINE_HOME"] = tempfile.mkdtemp(
        prefix="paw-offline-eval-"
    )
    get_settings.cache_clear()
    init_db()
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
    finally:
        conn.close()


def _answer(
    conn, question: str, mode: str, broad_stage: str
) -> tuple[str, dict]:
    if mode == "broad":
        broad = program_registry.active(conn, "broad")
        program_id = broad["program_id"]
        if broad_stage != "active":
            manifest = json.loads(
                program_registry.PROGRAM_MANIFEST.read_text()
            )
            program_id = manifest["programs"]["broad"][broad_stage]["program_id"]
        result = program_runtime.run(program_id, question)
        return result.output, {
            "elapsed_ms": result.elapsed_ms,
            "peak_rss_mb": result.peak_rss_mb,
            "labels": [],
        }
    original_cap = neural_answer_graph.MAX_SPECIALISTS
    neural_answer_graph.MAX_SPECIALISTS = 1 if mode == "top1" else 3
    if mode == "topk_critic":
        os.environ["PFO_USE_CRITIC"] = "1"
    else:
        os.environ.pop("PFO_USE_CRITIC", None)
    started = time.perf_counter()
    final = None
    try:
        for event in neural_answer_graph.answer_events(conn, question):
            if event["type"] == "final":
                final = event
    finally:
        neural_answer_graph.MAX_SPECIALISTS = original_cap
    if final is None:
        raise RuntimeError("Neural graph produced no final answer")
    trace = final["trace"]
    peaks = [
        item.get("peak_rss_mb", 0)
        for item in trace["programs"]
    ]
    for key in ("router", "aggregator", "critic", "revision"):
        item = trace.get(key)
        if item:
            peaks.append(item.get("peak_rss_mb", 0))
    return final["answer"], {
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "peak_rss_mb": max(peaks, default=0),
        "labels": final.get("program_labels", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("broad", "top1", "topk", "topk_critic"),
        required=True,
    )
    parser.add_argument(
        "--split", choices=("anchors", "dev", "test"), default="anchors"
    )
    parser.add_argument(
        "--broad-stage",
        choices=("active", "standard", "finetuned"),
        default="active",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    _setup()
    dataset = load_benchmark()[args.split]
    conn = connect()
    answers = {}
    operational = []
    try:
        for item in dataset["questions"]:
            answer, metrics = _answer(
                conn,
                item["question"],
                args.mode,
                args.broad_stage,
            )
            answers[item["id"]] = {
                "answer": answer,
                "citations": [],
                "confidence": 0.65,
            }
            operational.append({"id": item["id"], **metrics})
    finally:
        conn.close()
    report = score_dataset(
        dataset,
        answers,
        system_name=f"paw_{args.mode}",
    )
    latencies = [row["elapsed_ms"] for row in operational]
    summary = {
        **report["summary"],
        "median_final_answer_ms": round(statistics.median(latencies), 2),
        "p95_final_answer_ms": sorted(latencies)[
            max(0, int(len(latencies) * 0.95) - 1)
        ],
        "max_worker_rss_mb": max(
            (row["peak_rss_mb"] for row in operational), default=0
        ),
    }
    payload = {
        "mode": args.mode,
        "split": args.split,
        "summary": summary,
        "operational": operational,
        "answers": answers,
        "report": report,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
