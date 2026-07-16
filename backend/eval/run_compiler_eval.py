"""Evaluate shipped global programs and the real fast→finetuned trip promotion."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.paw_experts import (
    GLOBAL_PROGRAM_IDS,
    evaluate_program,
)


HEARD_EXPRESSION_FAST = "68941e424b44f2225061"
HEARD_EXPRESSION_FINETUNED = "2d105eae118b710e0155"


def main() -> None:
    globals_report = {}
    for role, program_id in GLOBAL_PROGRAM_IDS.items():
        score, metrics = evaluate_program(role, program_id)
        globals_report[role] = {
            "program_id": program_id,
            "compiler": "paw-ft-bs48",
            "score": score,
            "metrics": metrics,
        }
    fast_score, fast_metrics = evaluate_program(
        "heard_expression_resolver", HEARD_EXPRESSION_FAST
    )
    final_score, final_metrics = evaluate_program(
        "heard_expression_resolver", HEARD_EXPRESSION_FINETUNED
    )
    report = {
        "global_programs": globals_report,
        "trip_specific_promotion": {
            "role": "heard_expression_resolver",
            "fast_program_id": HEARD_EXPRESSION_FAST,
            "finetuned_program_id": HEARD_EXPRESSION_FINETUNED,
            "fast_score": fast_score,
            "finetuned_score": final_score,
            "lift": final_score - fast_score,
            "promotion_allowed": final_score >= fast_score,
            "fast_metrics": fast_metrics,
            "finetuned_metrics": final_metrics,
        },
    }
    path = Path(__file__).with_name("last_compiler_report.json")
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report written to {path}")


if __name__ == "__main__":
    main()
