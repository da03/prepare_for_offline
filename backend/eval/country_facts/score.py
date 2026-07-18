"""Deterministic scoring for the factual country QA benchmark.

Scoring is intentionally not delegated to a same-base model. Each case carries an
atomic fact ledger:

- ``must_include``: alias groups; a group is satisfied when any alias appears.
- ``should_include``: optional supporting alias groups.
- ``must_not_include``: prohibited phrases (false claims).
- ``entity_type_errors``: a mention plus "wrong type" cue terms that must not
  occur near it (for example, "Gangnam" near "city"/"cities"). This catches the
  district-as-peer-city and airport-as-city confusions that plain substring
  rubrics miss.

A case fails hard when it has any severe violation (prohibited claim or
entity-type error on a ``high`` severity case), regardless of coverage.
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

DATASET_SCHEMA_VERSION = "country-facts-dataset/v1"
RESULT_SCHEMA_VERSION = "country-facts-result/v1"

FAMILIES = {
    "capital_vs_seat",
    "major_cities",
    "entity_hierarchy",
    "borders_location",
    "language_currency_government",
    "transport_norms",
    "landmarks_culture",
    "time_sensitive",
}
FRESHNESS = {"stable", "slow_changing", "time_sensitive"}
SEVERITIES = {"low", "medium", "high"}
ENTITY_PROXIMITY_TOKENS = 12

_SPACE_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class CountryFactsValidationError(ValueError):
    """Raised when a benchmark dataset or split boundary is invalid."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    chars = [ch if ch.isalnum() else " " for ch in value]
    return _SPACE_RE.sub(" ", "".join(chars)).strip()


def _tokens(value: str) -> list[str]:
    return _normalize(value).split()


def _contains(haystack_norm: str, needle: str) -> bool:
    needle_norm = _normalize(needle)
    if not needle_norm:
        return False
    return f" {needle_norm} " in f" {haystack_norm} "


def _group_satisfied(answer_norm: str, group: Sequence[str]) -> bool:
    return any(_contains(answer_norm, alias) for alias in group)


def _entity_type_violation(
    answer_tokens: list[str],
    mention: str,
    cue_terms: Sequence[str],
    *,
    window: int = ENTITY_PROXIMITY_TOKENS,
) -> bool:
    mention_tokens = _tokens(mention)
    cue_norms = [_normalize(cue) for cue in cue_terms]
    if not mention_tokens:
        return False
    span = len(mention_tokens)
    for index in range(len(answer_tokens) - span + 1):
        if answer_tokens[index : index + span] != mention_tokens:
            continue
        lo = max(0, index - window)
        hi = min(len(answer_tokens), index + span + window)
        near = answer_tokens[lo:hi]
        near_text = " ".join(near)
        for cue in cue_norms:
            if not cue:
                continue
            if f" {cue} " in f" {near_text} ":
                return True
    return False


def score_answer(case: Mapping[str, Any], answer: str | None) -> dict[str, Any]:
    """Score one answer against one atomic fact ledger."""

    text = answer if isinstance(answer, str) else ""
    answer_norm = _normalize(text)
    answer_tokens = answer_norm.split()
    answered = bool(answer_norm)

    must_groups = list(case.get("must_include", []))
    should_groups = list(case.get("should_include", []))
    satisfied_must = [
        group for group in must_groups if _group_satisfied(answer_norm, group)
    ]
    satisfied_should = [
        group for group in should_groups if _group_satisfied(answer_norm, group)
    ]

    prohibited_hits = [
        phrase
        for phrase in case.get("must_not_include", [])
        if _contains(answer_norm, phrase)
    ]
    entity_errors = [
        {"mention": entry["mention"], "cue_terms": list(entry["cue_terms"])}
        for entry in case.get("entity_type_errors", [])
        if _entity_type_violation(
            answer_tokens, entry["mention"], entry["cue_terms"]
        )
    ]

    must_total = len(must_groups)
    should_total = len(should_groups)
    include_fraction = (
        (0.85 * (len(satisfied_must) / must_total) if must_total else 0.85)
        + (0.15 * (len(satisfied_should) / should_total) if should_total else 0.15)
    )
    violations = len(prohibited_hits) + len(entity_errors)
    raw_score = max(0.0, min(1.0, include_fraction - 0.5 * violations))

    severity = case.get("severity", "high")
    severe_violation = bool(prohibited_hits or entity_errors) and severity == "high"
    passed = answered and not severe_violation and raw_score >= float(
        case.get("pass_threshold", 0.8)
    )

    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "id": case["id"],
        "country": case["country"],
        "family": case["family"],
        "freshness": case.get("freshness", "stable"),
        "severity": severity,
        "answered": answered,
        "score": round(raw_score, 6),
        "must_total": must_total,
        "must_satisfied": len(satisfied_must),
        "should_total": should_total,
        "should_satisfied": len(satisfied_should),
        "missing_must": [group for group in must_groups if group not in satisfied_must],
        "prohibited_hits": prohibited_hits,
        "entity_type_errors": entity_errors,
        "severe_violation": severe_violation,
        "passed": passed,
    }


def aggregate_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not results:
        return {
            "cases": 0,
            "answer_rate": 0.0,
            "mean_score": 0.0,
            "pass_rate": 0.0,
            "prohibited_claim_rate": 0.0,
            "entity_type_error_rate": 0.0,
            "severe_violation_count": 0,
            "must_include_coverage": 0.0,
            "by_family": {},
            "by_country": {},
        }
    count = len(results)
    must_total = sum(row["must_total"] for row in results)
    must_satisfied = sum(row["must_satisfied"] for row in results)

    def _bucket(key: str) -> dict[str, Any]:
        buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in results:
            buckets[row[key]].append(row)
        return {
            name: {
                "cases": len(rows),
                "mean_score": round(
                    statistics.fmean(r["score"] for r in rows), 6
                ),
                "pass_rate": round(
                    sum(bool(r["passed"]) for r in rows) / len(rows), 6
                ),
            }
            for name, rows in sorted(buckets.items())
        }

    return {
        "cases": count,
        "answer_rate": round(sum(bool(r["answered"]) for r in results) / count, 6),
        "mean_score": round(statistics.fmean(r["score"] for r in results), 6),
        "pass_rate": round(sum(bool(r["passed"]) for r in results) / count, 6),
        "prohibited_claim_rate": round(
            sum(bool(r["prohibited_hits"]) for r in results) / count, 6
        ),
        "entity_type_error_rate": round(
            sum(bool(r["entity_type_errors"]) for r in results) / count, 6
        ),
        "severe_violation_count": sum(bool(r["severe_violation"]) for r in results),
        "must_include_coverage": round(must_satisfied / must_total, 6)
        if must_total
        else 1.0,
        "by_family": _bucket("family"),
        "by_country": _bucket("country"),
    }


def score_dataset(
    dataset: Mapping[str, Any],
    answers: Mapping[str, Any],
    *,
    system_name: str = "unnamed",
) -> dict[str, Any]:
    validate_dataset(dataset)
    results = []
    for case in dataset["cases"]:
        candidate = answers.get(case["id"], "")
        answer = (
            candidate.get("answer", "")
            if isinstance(candidate, Mapping)
            else candidate
        )
        if answer is not None and not isinstance(answer, str):
            raise CountryFactsValidationError(
                [f"answer for {case['id']!r} must be a string or null"]
            )
        results.append(score_answer(case, answer))
    return {
        "schema_version": "country-facts-report/v1",
        "system_name": system_name,
        "split": dataset["split"],
        "summary": aggregate_results(results),
        "results": results,
    }


def collect_dataset_errors(dataset: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(dataset, Mapping):
        return ["dataset root must be an object"]
    if dataset.get("schema_version") != DATASET_SCHEMA_VERSION:
        errors.append(f"schema_version must be {DATASET_SCHEMA_VERSION!r}")
    if dataset.get("split") not in {"dev", "test"}:
        errors.append("split must be 'dev' or 'test'")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a nonempty list")
        return errors

    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        location = f"cases[{index}]"
        if not isinstance(case, Mapping):
            errors.append(f"{location}: must be an object")
            continue
        case_id = case.get("id")
        if not isinstance(case_id, str) or not _ID_RE.fullmatch(case_id):
            errors.append(f"{location}.id: invalid id")
        elif case_id in seen_ids:
            errors.append(f"{location}.id: duplicate {case_id!r}")
        else:
            seen_ids.add(case_id)
        for field in ("country", "question"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                errors.append(f"{location}.{field}: nonblank string required")
        if case.get("family") not in FAMILIES:
            errors.append(f"{location}.family: must be one of {sorted(FAMILIES)}")
        if case.get("freshness", "stable") not in FRESHNESS:
            errors.append(f"{location}.freshness: invalid value")
        if case.get("severity", "high") not in SEVERITIES:
            errors.append(f"{location}.severity: invalid value")
        must = case.get("must_include")
        if not isinstance(must, list) or not must:
            errors.append(f"{location}.must_include: nonempty list of alias groups")
        else:
            for group_index, group in enumerate(must):
                if not isinstance(group, list) or not group or not all(
                    isinstance(alias, str) and alias.strip() for alias in group
                ):
                    errors.append(
                        f"{location}.must_include[{group_index}]: "
                        "nonempty list of nonblank strings"
                    )
        for entry_index, entry in enumerate(case.get("entity_type_errors", [])):
            entry_loc = f"{location}.entity_type_errors[{entry_index}]"
            if not isinstance(entry, Mapping):
                errors.append(f"{entry_loc}: must be an object")
                continue
            if not isinstance(entry.get("mention"), str) or not entry["mention"].strip():
                errors.append(f"{entry_loc}.mention: nonblank string required")
            cues = entry.get("cue_terms")
            if not isinstance(cues, list) or not cues or not all(
                isinstance(cue, str) and cue.strip() for cue in cues
            ):
                errors.append(f"{entry_loc}.cue_terms: nonempty list of strings")
        if not isinstance(case.get("sources"), list) or not case["sources"]:
            errors.append(f"{location}.sources: nonempty list required")
    return errors


def validate_dataset(dataset: Any) -> Mapping[str, Any]:
    errors = collect_dataset_errors(dataset)
    if errors:
        raise CountryFactsValidationError(errors)
    return dataset


def load_dataset(path: str | Path, *, expected_split: str | None = None) -> Mapping[str, Any]:
    dataset = validate_dataset(json.loads(Path(path).read_text(encoding="utf-8")))
    if expected_split is not None and dataset["split"] != expected_split:
        raise CountryFactsValidationError(
            [f"{path}: expected split {expected_split!r}, found {dataset['split']!r}"]
        )
    return dataset


def validate_splits(dev: Mapping[str, Any], test: Mapping[str, Any]) -> None:
    """Held-out isolation: distinct questions and disjoint test countries.

    Test countries must not appear in dev so that fixing a dev country (Korea,
    Singapore) cannot pass the held-out split by memorization.
    """

    validate_dataset(dev)
    validate_dataset(test)
    errors: list[str] = []
    dev_questions = {_normalize(case["question"]) for case in dev["cases"]}
    for case in test["cases"]:
        if _normalize(case["question"]) in dev_questions:
            errors.append(f"test question duplicates dev: {case['id']}")
    dev_countries = {_normalize(case["country"]) for case in dev["cases"]}
    for case in test["cases"]:
        if _normalize(case["country"]) in dev_countries:
            errors.append(
                f"test country {case['country']!r} also appears in dev "
                f"({case['id']}); hold out analogous countries"
            )
    if errors:
        raise CountryFactsValidationError(errors)
