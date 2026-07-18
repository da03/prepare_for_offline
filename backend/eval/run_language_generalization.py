"""Compare imperfect language programs by relative expected utility."""

from __future__ import annotations

import json
from pathlib import Path

import programasweights as paw

ROOT = Path(__file__).resolve().parent


def _function(program_id: str):
    return paw.function(program_id, offline=True)


def _coverage(answer: str, case: dict) -> float:
    normalized = answer.casefold()
    positive = any(
        value.casefold() in normalized for value in case["must_include_any"]
    )
    violations = sum(
        value.casefold() in normalized
        for value in case.get("must_not_include", [])
    )
    return max(0.0, float(positive) - 0.5 * violations)


def main() -> None:
    cases = json.loads((ROOT / "language_generalization.json").read_text())
    production = json.loads(
        (
            ROOT.parent / "app" / "services" / "neural_programs.json"
        ).read_text()
    )["programs"]
    candidates = json.loads(
        (ROOT / "leakage_free_programs.json").read_text()
    )["programs"]

    report = {}
    for stage in ("standard", "finetuned"):
        intent = _function(production["language_intent"][stage]["program_id"])
        rows = []
        for case in cases["intent"]:
            output = intent(case["question"], max_tokens=8).strip()
            rows.append({**case, "output": output, "correct": output == case["expected"]})
        report[f"intent_{stage}"] = {
            "accuracy": sum(row["correct"] for row in rows) / len(rows),
            "rows": rows,
        }

        heard = _function(production["heard_expression"][stage]["program_id"])
        heard_rows = []
        for case in cases["heard_expression"]:
            output = heard(case["input"], max_tokens=220)
            score = _coverage(output, case)
            heard_rows.append({**case, "output": output, "score": score})
        report[f"heard_expression_{stage}"] = {
            "mean_score": sum(row["score"] for row in heard_rows) / len(heard_rows),
            "rows": heard_rows,
        }

    translation_programs = {
        "broad_finetuned": production["broad"]["finetuned"]["program_id"],
        "translation_standard": production["translation"]["standard"]["program_id"],
        "translation_finetuned": production["translation"]["finetuned"]["program_id"],
        "translation_examples_standard": candidates["translation_examples"][
            "standard"
        ]["program_id"],
        "translation_examples_finetuned": candidates["translation_examples"][
            "finetuned"
        ]["program_id"],
    }
    for name, program_id in translation_programs.items():
        function = _function(program_id)
        rows = []
        for case in cases["translation"]:
            output = function(case["input"], max_tokens=220)
            score = _coverage(output, case)
            rows.append({**case, "output": output, "score": score})
        report[name] = {
            "program_id": program_id,
            "mean_score": sum(row["score"] for row in rows) / len(rows),
            "answer_rate": sum(bool(row["output"].strip()) for row in rows)
            / len(rows),
            "rows": rows,
        }
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
