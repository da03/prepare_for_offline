"""CLI for the factual country QA benchmark.

Examples:

    python -m eval.country_facts.runner validate
    python -m eval.country_facts.runner score --split dev \
        --answers answers.json --min-mean-score 0.8
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .score import load_dataset, score_dataset, validate_splits

BENCHMARK_DIR = Path(__file__).resolve().parent
DATASET_PATHS = {
    "dev": BENCHMARK_DIR / "datasets" / "dev.json",
    "test": BENCHMARK_DIR / "datasets" / "test.json",
}


def load_splits() -> dict[str, Mapping[str, Any]]:
    dev = load_dataset(DATASET_PATHS["dev"], expected_split="dev")
    test = load_dataset(DATASET_PATHS["test"], expected_split="test")
    validate_splits(dev, test)
    return {"dev": dev, "test": test}


def _load_answers(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, Mapping):
        return {str(key): answer for key, answer in value.items()}
    if isinstance(value, list):
        answers: dict[str, Any] = {}
        for row in value:
            if not isinstance(row, Mapping) or "id" not in row:
                raise ValueError("answer rows must contain an 'id' field")
            answers[str(row["id"])] = row.get("answer", "")
        return answers
    raise ValueError("answers JSON must be an object or list of rows")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("validate", help="Validate datasets and held-out isolation.")

    score_parser = commands.add_parser("score", help="Score an answers file.")
    score_parser.add_argument("--split", choices=("dev", "test"), required=True)
    score_parser.add_argument("--answers", type=Path, required=True)
    score_parser.add_argument("--system-name", default="unnamed")
    score_parser.add_argument("--output-json", type=Path)
    score_parser.add_argument("--min-mean-score", type=float)
    score_parser.add_argument("--min-pass-rate", type=float)
    score_parser.add_argument(
        "--max-prohibited-rate",
        type=float,
        help="Fail if the prohibited-claim rate exceeds this value.",
    )
    score_parser.add_argument(
        "--allow-severe",
        action="store_true",
        help="Do not fail on severe entity-type or prohibited violations.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        splits = load_splits()
        if args.command == "validate":
            print(
                json.dumps(
                    {
                        "valid": True,
                        "counts": {
                            name: len(dataset["cases"])
                            for name, dataset in splits.items()
                        },
                    },
                    indent=2,
                )
            )
            return 0

        report = score_dataset(
            splits[args.split],
            _load_answers(args.answers),
            system_name=args.system_name,
        )
        if args.output_json:
            args.output_json.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False))

        summary = report["summary"]
        failures: list[str] = []
        if (
            args.min_mean_score is not None
            and summary["mean_score"] < args.min_mean_score
        ):
            failures.append(
                f"mean score {summary['mean_score']:.6f} below {args.min_mean_score}"
            )
        if (
            args.min_pass_rate is not None
            and summary["pass_rate"] < args.min_pass_rate
        ):
            failures.append(
                f"pass rate {summary['pass_rate']:.6f} below {args.min_pass_rate}"
            )
        if (
            args.max_prohibited_rate is not None
            and summary["prohibited_claim_rate"] > args.max_prohibited_rate
        ):
            failures.append(
                f"prohibited-claim rate {summary['prohibited_claim_rate']:.6f} "
                f"exceeds {args.max_prohibited_rate}"
            )
        if not args.allow_severe and summary["severe_violation_count"]:
            failures.append(
                f"{summary['severe_violation_count']} severe violation(s) present"
            )
        if failures:
            for failure in failures:
                print(f"threshold failure: {failure}", file=sys.stderr)
            return 2
        return 0
    except (OSError, ValueError) as exc:
        print(f"country-facts: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
