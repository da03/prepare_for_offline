"""Score the offline factual-pack layer against the country-facts benchmark.

This is deterministic and offline: it answers each case from the curated factual
packs (no model) and scores atomic-claim coverage, prohibited claims, and severe
violations. Countries without a pack are intentionally unanswered, which honestly
shows that factual grounding must be built per country rather than assumed.

    python -m eval.run_country_facts_eval --split dev
    python -m eval.run_country_facts_eval --split test --min-pass-rate 0.0
"""

from __future__ import annotations

import argparse
import json
import sys

from app.services import factual_packs
from eval.country_facts.runner import load_splits
from eval.country_facts.score import score_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--min-pass-rate", type=float)
    parser.add_argument("--require-zero-severe", action="store_true")
    args = parser.parse_args()

    dataset = load_splits()[args.split]
    answers = {}
    for case in dataset["cases"]:
        found = factual_packs.lookup(case["question"])
        answers[case["id"]] = found["answer"] if found else ""
    report = score_dataset(dataset, answers, system_name="factual-packs")
    summary = report["summary"]
    print(json.dumps(report, indent=2, ensure_ascii=False))

    failures = []
    if args.min_pass_rate is not None and summary["pass_rate"] < args.min_pass_rate:
        failures.append(
            f"pass rate {summary['pass_rate']:.6f} below {args.min_pass_rate}"
        )
    if args.require_zero_severe and summary["severe_violation_count"]:
        failures.append(
            f"{summary['severe_violation_count']} severe violation(s) present"
        )
    for failure in failures:
        print(f"threshold failure: {failure}", file=sys.stderr)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
