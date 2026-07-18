"""CLI harness for the backward-design Universal QA benchmark.

Examples:

    python -m eval.universal_qa.runner validate
    python -m eval.universal_qa.runner score \
        --split dev --answers answers.json \
        --output-json report.json --output-markdown report.md

Answer files may map ids directly to strings or to objects containing
``answer``, ``citations``, and ``confidence``:

    {
      "dev-blue-sky": {
        "answer": "Short wavelengths are Rayleigh-scattered ...",
        "citations": [{"source_id": "nasa-blue-sky"}],
        "confidence": 0.91
      }
    }
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .paw_grader import BlindedPawGrader
from .score import (
    ANCHOR_CASE_COUNT,
    DEFAULT_GRADER_CREDIT_CAP,
    MIN_DEV_CASES,
    MIN_TEST_CASES,
    _load_answers,
    load_dataset,
    score_dataset,
    validate_benchmark,
)

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_DATASET_PATHS = {
    "anchors": BENCHMARK_DIR / "datasets" / "anchors.yaml",
    "dev": BENCHMARK_DIR / "datasets" / "dev.yaml",
    "test": BENCHMARK_DIR / "datasets" / "test.yaml",
}
DEFAULT_SPEC_PATHS = [BENCHMARK_DIR / "specs" / "rubric_checker.txt"]


def load_benchmark(
    *,
    anchors_path: str | Path = DEFAULT_DATASET_PATHS["anchors"],
    dev_path: str | Path = DEFAULT_DATASET_PATHS["dev"],
    test_path: str | Path = DEFAULT_DATASET_PATHS["test"],
    spec_paths: Sequence[str | Path] = DEFAULT_SPEC_PATHS,
) -> dict[str, Mapping[str, Any]]:
    """Load and validate all splits, count floors, and held-out isolation."""

    datasets = {
        "anchors": load_dataset(anchors_path, expected_split="anchors"),
        "dev": load_dataset(dev_path, expected_split="dev"),
        "test": load_dataset(test_path, expected_split="test"),
    }
    validate_benchmark(
        datasets["anchors"],
        datasets["dev"],
        datasets["test"],
        spec_paths=spec_paths,
    )
    return datasets


def render_markdown_report(report: Mapping[str, Any]) -> str:
    """Render a stable human-readable companion to the JSON report."""

    summary = report["summary"]
    dataset = report["dataset"]
    lines = [
        "# Universal QA benchmark report",
        "",
        f"- System: `{report['system_name']}`",
        f"- Split: `{dataset['split']}`",
        f"- Cases: {dataset['question_count']}",
        f"- Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Mean score | {summary['mean_score']:.3f} |",
        f"| Deterministic mean | {summary['deterministic_mean_score']:.3f} |",
        f"| Answer rate | {summary['answer_rate']:.3f} |",
        f"| Refusal rate | {summary['refusal_rate']:.3f} |",
        f"| Must-include coverage | {summary['must_include_coverage']:.3f} |",
        f"| Should-include coverage | {summary['should_include_coverage']:.3f} |",
        f"| Prohibited-claim rate | {summary['must_not_violation_rate']:.3f} |",
        f"| Citation correctness | {summary['citation_correctness']:.3f} |",
        f"| Citation recall | {summary['citation_recall']:.3f} |",
        f"| Confidence coverage | {summary['confidence_coverage']:.3f} |",
    ]
    if summary["brier_score"] is not None:
        lines.extend(
            [
                f"| Brier score | {summary['brier_score']:.3f} |",
                "| Expected calibration error | "
                f"{summary['expected_calibration_error']:.3f} |",
            ]
        )

    lines.extend(
        [
            "",
            "## Domains",
            "",
            "| Domain | Cases | Answer rate | Mean score |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for domain, aggregate in report["domains"].items():
        lines.append(
            f"| {domain} | {aggregate['question_count']} | "
            f"{aggregate['answer_rate']:.3f} | {aggregate['mean_score']:.3f} |"
        )

    lines.extend(["", "## Cases", ""])
    for result in report["results"]:
        confidence = result["confidence"]["reported"]
        confidence_text = (
            f"{confidence:.3f}" if confidence is not None else "not reported"
        )
        lines.extend(
            [
                f"### {result['question_id']}",
                "",
                f"- Domain: `{result['domain']}`",
                f"- Status: `{result['status']}`",
                f"- Score: {result['score']:.3f} "
                f"(deterministic {result['deterministic_score']:.3f})",
                f"- Confidence: {confidence_text}",
                "- Citations: "
                f"{result['citations']['correct_count']}/"
                f"{result['citations']['provided_count']} correct",
                "",
                "| Rubric point | Tier | Hit | Method |",
                "| --- | --- | :---: | --- |",
            ]
        )
        for point in result["point_results"]:
            lines.append(
                f"| `{point['point_id']}` | {point['tier']} | "
                f"{'yes' if point['hit'] else 'no'} | "
                f"{point['match_method']} |"
            )
        if result["grader_errors"] or result["citations"]["errors"]:
            lines.extend(["", "Errors:"])
            lines.extend(
                f"- {error}"
                for error in (
                    list(result["grader_errors"])
                    + list(result["citations"]["errors"])
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_hook(spec: str) -> Any:
    if ":" not in spec:
        raise ValueError("hook must use MODULE:ATTRIBUTE syntax")
    module_name, attribute_name = spec.split(":", 1)
    if not module_name or not attribute_name:
        raise ValueError("hook must use MODULE:ATTRIBUTE syntax")
    value = getattr(importlib.import_module(module_name), attribute_name)
    if isinstance(value, type):
        return value()
    return value


def _add_dataset_path_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--anchors",
        type=Path,
        default=DEFAULT_DATASET_PATHS["anchors"],
    )
    parser.add_argument(
        "--dev",
        type=Path,
        default=DEFAULT_DATASET_PATHS["dev"],
    )
    parser.add_argument(
        "--test",
        type=Path,
        default=DEFAULT_DATASET_PATHS["test"],
    )
    parser.add_argument(
        "--spec",
        type=Path,
        action="append",
        dest="specs",
        help="Checker specification to scan for held-out leakage; repeatable.",
    )


def _load_from_args(args: argparse.Namespace) -> dict[str, Mapping[str, Any]]:
    return load_benchmark(
        anchors_path=args.anchors,
        dev_path=args.dev,
        test_path=args.test,
        spec_paths=args.specs or DEFAULT_SPEC_PATHS,
    )


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _validation_payload(
    datasets: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "valid": True,
        "thresholds": {
            "anchors_exact": ANCHOR_CASE_COUNT,
            "dev_minimum": MIN_DEV_CASES,
            "test_minimum": MIN_TEST_CASES,
        },
        "counts": {
            split: len(dataset["questions"])
            for split, dataset in datasets.items()
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser(
        "validate",
        help="Validate datasets, count thresholds, and held-out isolation.",
    )
    _add_dataset_path_arguments(validate_parser)
    validate_parser.add_argument("--output-json", type=Path)

    score_parser = commands.add_parser(
        "score",
        help="Score answer JSON and emit JSON and/or Markdown.",
    )
    _add_dataset_path_arguments(score_parser)
    score_parser.add_argument("--split", choices=("anchors", "dev", "test"), required=True)
    score_parser.add_argument("--answers", type=Path, required=True)
    score_parser.add_argument("--system-name", default="unnamed")
    score_parser.add_argument("--output-json", type=Path)
    score_parser.add_argument("--output-markdown", type=Path)
    score_parser.add_argument("--paw-program-id")
    score_parser.add_argument(
        "--citation-checker",
        metavar="MODULE:ATTRIBUTE",
        help="Optional blinded citation-support hook.",
    )
    score_parser.add_argument(
        "--grader-credit-cap",
        type=float,
        default=DEFAULT_GRADER_CREDIT_CAP,
    )
    score_parser.add_argument("--min-score", type=float)
    score_parser.add_argument("--min-answer-rate", type=float)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        datasets = _load_from_args(args)
        if args.command == "validate":
            payload = _validation_payload(datasets)
            if args.output_json:
                _write_json(args.output_json, payload)
            else:
                print(json.dumps(payload, indent=2))
            return 0

        grader = (
            BlindedPawGrader.from_program_id(args.paw_program_id)
            if args.paw_program_id
            else None
        )
        citation_checker = (
            _load_hook(args.citation_checker)
            if args.citation_checker
            else None
        )
        report = score_dataset(
            datasets[args.split],
            _load_answers(args.answers),
            grader=grader,
            grader_credit_cap=args.grader_credit_cap,
            citation_checker=citation_checker,
            system_name=args.system_name,
        )
        if args.output_json:
            _write_json(args.output_json, report)
        if args.output_markdown:
            args.output_markdown.write_text(
                render_markdown_report(report),
                encoding="utf-8",
            )
        if not args.output_json and not args.output_markdown:
            print(json.dumps(report, indent=2, ensure_ascii=False))

        failures: list[str] = []
        if (
            args.min_score is not None
            and report["summary"]["mean_score"] < args.min_score
        ):
            failures.append(
                f"mean score {report['summary']['mean_score']:.6f} "
                f"is below {args.min_score:.6f}"
            )
        if (
            args.min_answer_rate is not None
            and report["summary"]["answer_rate"] < args.min_answer_rate
        ):
            failures.append(
                f"answer rate {report['summary']['answer_rate']:.6f} "
                f"is below {args.min_answer_rate:.6f}"
            )
        if failures:
            for failure in failures:
                print(f"threshold failure: {failure}", file=sys.stderr)
            return 2
        return 0
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"universal-qa: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
