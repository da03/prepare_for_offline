"""Evaluate exact-label top-k routing on explicitly multi-domain questions."""

from __future__ import annotations

import json
from pathlib import Path

import programasweights as paw

from app.services import neural_specs

ROOT = Path(__file__).resolve().parent


def main() -> None:
    manifest = json.loads(
        (ROOT.parent / "app" / "services" / "neural_programs.json").read_text()
    )
    stages = manifest["programs"]["router"]
    program_id = (stages.get("finetuned") or stages["standard"])["program_id"]
    function = paw.function(program_id, offline=True)
    cases = json.loads((ROOT / "neural_routes.json").read_text())
    exact = 0
    recall = 0
    outputs = []
    allowed = set(neural_specs.BUILTIN_TOPICS)
    for case in cases:
        raw = function(
            f"QUESTION: {case['question']}",
            max_tokens=48,
        )
        labels = [
            value.strip().casefold()
            for value in str(raw).replace("\n", ",").split(",")
            if value.strip().casefold() in allowed
        ][:3]
        expected = case["expected"]
        exact += int(labels == expected)
        recall += len(set(labels) & set(expected)) / len(expected)
        outputs.append(
            {
                **case,
                "actual": labels,
                "raw": str(raw),
                "exact": labels == expected,
            }
        )
    report = {
        "program_id": program_id,
        "cases": len(cases),
        "exact_match": round(exact / len(cases), 3),
        "mean_label_recall": round(recall / len(cases), 3),
        "outputs": outputs,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
