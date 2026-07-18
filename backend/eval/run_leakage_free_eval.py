"""Score leakage-free broad PAW candidates on development and held-out sets."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import programasweights as paw

from eval.leakage_free_specs import BROAD_BEHAVIOR_SPEC, BROAD_PROSE_SPEC
from eval.universal_qa.runner import load_benchmark
from eval.universal_qa.score import score_dataset

ROOT = Path(__file__).resolve().parent


def main() -> None:
    programs = json.loads(
        (ROOT / "leakage_free_programs.json").read_text()
    )["programs"]
    benchmark = load_benchmark()
    specs = {
        "broad_prose": BROAD_PROSE_SPEC,
        "broad_behavior": BROAD_BEHAVIOR_SPEC,
    }
    report = {}
    for role, spec in specs.items():
        leaked = [
            item["id"]
            for split in ("dev", "test")
            for item in benchmark[split]["questions"]
            if item["question"] in spec
        ]
        if leaked:
            raise RuntimeError(f"{role} leaks benchmark questions: {leaked}")
        for stage, metadata in programs[role].items():
            function = paw.function(metadata["program_id"], offline=True)
            for split in ("dev", "test"):
                answers = {}
                latencies = []
                for item in benchmark[split]["questions"]:
                    started = time.perf_counter()
                    answer = function(item["question"], max_tokens=320)
                    latencies.append((time.perf_counter() - started) * 1000)
                    answers[item["id"]] = {
                        "answer": answer,
                        "confidence": 0.65,
                    }
                scored = score_dataset(
                    benchmark[split],
                    answers,
                    system_name=f"{role}_{stage}",
                )
                report[f"{role}_{stage}_{split}"] = {
                    **scored["summary"],
                    "median_warm_ms": round(statistics.median(latencies), 2),
                }
            closer = getattr(function, "close", None)
            if callable(closer):
                closer()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
