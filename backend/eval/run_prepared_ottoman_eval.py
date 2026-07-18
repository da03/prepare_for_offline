"""Compare broad and prepared Ottoman PAW programs on hand-written cases."""

from __future__ import annotations

import json
import re
from pathlib import Path

import programasweights as paw

ROOT = Path(__file__).resolve().parent
PROGRAMS = {
    "broad_standard": "9cb56a510a84f1164b17",
    "broad_finetuned": "9baea697447389a8b073",
    "prepared_standard": "ecd9e816e54f743eff58",
    "prepared_finetuned": "ea3d26855f3e799a68cd",
}


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold())


def _score(answer: str, case: dict) -> float:
    normalized = _normalize(answer)
    positive = sum(
        any(alias.casefold() in normalized for alias in group)
        for group in case["must_include"]
    ) / len(case["must_include"])
    violations = sum(
        phrase.casefold() in normalized for phrase in case["must_not_include"]
    )
    return max(0.0, positive - 0.5 * violations)


def main() -> None:
    cases = json.loads((ROOT / "prepared_ottoman.json").read_text())
    report = {}
    answer_maps = {}
    for name, program_id in PROGRAMS.items():
        function = paw.function(program_id, offline=True)
        rows = []
        for case in cases:
            answer = function(case["question"], max_tokens=320)
            rows.append(
                {
                    "question": case["question"],
                    "answer": answer,
                    "score": round(_score(answer, case), 3),
                }
            )
        answer_maps[name] = {row["question"]: row["answer"] for row in rows}
        report[name] = {
            "program_id": program_id,
            "mean_score": round(
                sum(row["score"] for row in rows) / len(rows), 3
            ),
            "answer_rate": sum(bool(row["answer"].strip()) for row in rows)
            / len(rows),
            "rows": rows,
        }
        closer = getattr(function, "close", None)
        if callable(closer):
            closer()
    manifest = json.loads(
        (
            ROOT.parent / "app" / "services" / "neural_programs.json"
        ).read_text()
    )
    stages = manifest["programs"]["aggregator"]
    aggregator_id = (stages.get("finetuned") or stages["standard"])["program_id"]
    aggregator = paw.function(aggregator_id, offline=True)
    aggregate_rows = []
    for case in cases:
        question = case["question"]
        answer = aggregator(
            f"QUESTION: {question}\n"
            f"CANDIDATE broad: {answer_maps['broad_finetuned'][question]}\n"
            f"CANDIDATE prepared_topic: "
            f"{answer_maps['prepared_finetuned'][question]}",
            max_tokens=320,
        )
        aggregate_rows.append(
            {
                "question": question,
                "answer": answer,
                "score": round(_score(answer, case), 3),
            }
        )
    report["broad_plus_prepared_finetuned"] = {
        "program_id": aggregator_id,
        "mean_score": round(
            sum(row["score"] for row in aggregate_rows) / len(aggregate_rows),
            3,
        ),
        "answer_rate": sum(
            bool(row["answer"].strip()) for row in aggregate_rows
        )
        / len(aggregate_rows),
        "rows": aggregate_rows,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
