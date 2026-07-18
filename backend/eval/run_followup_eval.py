"""Compare bounded follow-up strategies on unseen question-answer pairs."""

from __future__ import annotations

import json
import re
import statistics
import time
from pathlib import Path

import programasweights as paw

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "followup_cases.json"
PROGRAMS_PATH = ROOT / "followup_programs.json"
REPORT_PATH = ROOT / "followup_report.json"
PRODUCTION_MANIFEST = (
    ROOT.parent / "app" / "services" / "neural_programs.json"
)
LEGACY_FOLLOWUP_PROGRAM_ID = "b2648cc9e8f9dfd1ad41"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def _coverage(
    value: str,
    include_groups: list[list[str]],
    forbidden: list[str],
) -> float:
    normalized = _normalize(value)
    positive = sum(
        any(_normalize(alias) in normalized for alias in group)
        for group in include_groups
    ) / len(include_groups)
    violations = sum(_normalize(phrase) in normalized for phrase in forbidden)
    return max(0.0, positive - 0.5 * violations)


def _close(function) -> None:
    closer = getattr(function, "close", None) or getattr(function, "free", None)
    if callable(closer):
        closer()


def _answer_aware_input(case: dict) -> str:
    return (
        f"PREVIOUS_QUESTION: {case['previous_question']}\n"
        f"PREVIOUS_ANSWER: {case['previous_answer']}\n"
        f"FOLLOW_UP: {case['follow_up']}"
    )


def _structured_context(case: dict) -> str:
    return (
        "Use the immediate context below to answer only the follow-up.\n"
        f"PREVIOUS_QUESTION: {case['previous_question']}\n"
        f"PREVIOUS_ANSWER: {case['previous_answer']}\n"
        f"FOLLOW_UP: {case['follow_up']}"
    )


def _summarize(rows: list[dict], split: str) -> dict:
    selected = [row for row in rows if row["split"] == split]
    rewrite_scores = [
        row["rewrite_score"]
        for row in selected
        if row["rewrite_score"] is not None
    ]
    rewrite_latencies = [
        row["rewrite_ms"]
        for row in selected
        if row["rewrite_ms"] is not None
    ]
    answer_latencies = [row["answer_ms"] for row in selected]
    return {
        "cases": len(selected),
        "rewrite_score": (
            round(statistics.fmean(rewrite_scores), 3)
            if rewrite_scores
            else None
        ),
        "answer_score": round(
            statistics.fmean(row["answer_score"] for row in selected), 3
        ),
        "median_warm_rewrite_ms": (
            round(statistics.median(rewrite_latencies), 1)
            if rewrite_latencies
            else 0.0
        ),
        "median_warm_answer_ms": round(
            statistics.median(answer_latencies), 1
        ),
    }


def main() -> None:
    cases = json.loads(CASES_PATH.read_text())
    production = json.loads(PRODUCTION_MANIFEST.read_text())["programs"]
    candidates: dict[str, dict] = {
        "followup_only": {"kind": "direct"},
        "structured_context": {"kind": "structured"},
        "legacy_question_only_finetuned": {
            "kind": "legacy",
            "program_id": LEGACY_FOLLOWUP_PROGRAM_ID,
        },
    }
    if PROGRAMS_PATH.exists():
        programs = json.loads(PROGRAMS_PATH.read_text())["programs"]
        for role, stages in programs.items():
            for stage, metadata in stages.items():
                candidates[f"{role}_{stage}"] = {
                    "kind": "answer_aware",
                    "program_id": metadata["program_id"],
                }

    queries: dict[str, dict[str, dict]] = {
        name: {} for name in candidates
    }
    for case in cases:
        queries["followup_only"][case["id"]] = {
            "query": case["follow_up"],
            "rewrite_ms": None,
            "rewrite_score": None,
        }
        queries["structured_context"][case["id"]] = {
            "query": _structured_context(case),
            "rewrite_ms": None,
            "rewrite_score": None,
        }

    for name, candidate in candidates.items():
        if candidate["kind"] not in {"legacy", "answer_aware"}:
            continue
        function = paw.function(candidate["program_id"], offline=True)
        try:
            for case in cases:
                prompt = (
                    f"PREVIOUS: {case['previous_question']}\n"
                    f"FOLLOW-UP: {case['follow_up']}"
                    if candidate["kind"] == "legacy"
                    else _answer_aware_input(case)
                )
                started = time.perf_counter()
                rewritten = str(
                    function(prompt, max_tokens=112, temperature=0.0)
                ).strip()
                elapsed_ms = (time.perf_counter() - started) * 1000
                queries[name][case["id"]] = {
                    "query": rewritten,
                    "rewrite_ms": round(elapsed_ms, 1),
                    "rewrite_score": round(
                        _coverage(
                            rewritten,
                            case["rewrite_must_include"],
                            case["rewrite_must_not_include"],
                        ),
                        3,
                    ),
                }
        finally:
            _close(function)

    broad_id = production["broad"]["finetuned"]["program_id"]
    broad = paw.function(broad_id, offline=True)
    reports: dict[str, dict] = {}
    try:
        for name, candidate in candidates.items():
            rows = []
            for case in cases:
                rewrite = queries[name][case["id"]]
                started = time.perf_counter()
                answer = str(
                    broad(
                        rewrite["query"],
                        max_tokens=240,
                        temperature=0.0,
                    )
                ).strip()
                answer_ms = (time.perf_counter() - started) * 1000
                rows.append(
                    {
                        "id": case["id"],
                        "split": case["split"],
                        "follow_up": case["follow_up"],
                        "query": rewrite["query"],
                        "rewrite_score": rewrite["rewrite_score"],
                        "rewrite_ms": rewrite["rewrite_ms"],
                        "answer": answer,
                        "answer_score": round(
                            _coverage(
                                answer,
                                case["answer_must_include"],
                                case["answer_must_not_include"],
                            ),
                            3,
                        ),
                        "answer_ms": round(answer_ms, 1),
                    }
                )
            reports[name] = {
                **candidate,
                "dev": _summarize(rows, "dev"),
                "test": _summarize(rows, "test"),
                "rows": rows,
            }
    finally:
        _close(broad)

    paw_candidates = {
        name: value
        for name, value in reports.items()
        if value["kind"] in {"legacy", "answer_aware"}
    }
    best_paw = max(
        paw_candidates,
        key=lambda name: (
            paw_candidates[name]["dev"]["answer_score"],
            paw_candidates[name]["dev"]["rewrite_score"] or 0.0,
            -paw_candidates[name]["dev"]["median_warm_rewrite_ms"],
        ),
    )
    baseline = reports["structured_context"]
    best = reports[best_paw]
    meaningful_dev_lift = (
        best["dev"]["answer_score"] - baseline["dev"]["answer_score"]
        >= 0.05
    )
    rewrite_fidelity_gate = (
        (best["dev"]["rewrite_score"] or 0.0) >= 0.9
    )
    selected = (
        best_paw
        if meaningful_dev_lift and rewrite_fidelity_gate
        else "structured_context"
    )
    report = {
        "schema_version": 1,
        "cases": len(cases),
        "selection_policy": (
            "Prefer zero-rewrite structured context unless the best PAW "
            "candidate gains at least 0.05 development answer score and "
            "reaches 0.90 development rewrite fidelity; report held-out test "
            "only after selection"
        ),
        "broad_program_id": broad_id,
        "best_paw_by_dev": best_paw,
        "meaningful_dev_lift": meaningful_dev_lift,
        "rewrite_fidelity_gate": rewrite_fidelity_gate,
        "selected_for_product": selected,
        "selected_test_score": reports[selected]["test"]["answer_score"],
        "candidates": reports,
    }
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    )
    summary = {
        name: {"dev": value["dev"], "test": value["test"]}
        for name, value in reports.items()
    }
    print(
        json.dumps(
            {
                "best_paw_by_dev": best_paw,
                "selected_for_product": selected,
                "candidates": summary,
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
