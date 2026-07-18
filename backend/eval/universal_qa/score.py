"""Deterministic scoring and validation for the backward-design QA benchmark.

The checked-in ``*.yaml`` files use JSON syntax, which is a strict subset of
YAML.  They therefore load with the standard library.  PyYAML is used only as
an optional fallback for contributors who prefer conventional YAML syntax.

Scoring is deliberately not delegated to an LLM.  Exact, normalized
claim/alias matches produce the authoritative deterministic score.  An
optional point grader may recover paraphrases, but its total positive and
negative influence is capped and both the deterministic and adjusted scores
are reported.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import re
import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

DATASET_SCHEMA_VERSION = "universal-qa-dataset/v1"
RESULT_SCHEMA_VERSION = "universal-qa-result/v1"
REPORT_SCHEMA_VERSION = "universal-qa-report/v1"
DEFAULT_GRADER_CREDIT_CAP = 0.20
ANCHOR_CASE_COUNT = 8
MIN_DEV_CASES = 30
MIN_TEST_CASES = 20

CATEGORIES = {
    "adversarial_staleness",
    "ambiguous_wording",
    "bounded_follow_up",
    "country_city_overview",
    "culture",
    "current_low_stakes",
    "everyday_practical",
    "language_phonetic",
    "multilingual",
    "science_common_knowledge",
    "travel",
    "world_history",
}
REQUIRED_DOMAINS = {
    "adversarial_staleness",
    "country_travel",
    "everyday_practical",
    "history",
    "language_phonetic",
    "science",
}

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)
_PLACEHOLDER_RE = re.compile(
    r"\b(?:todo|tbd|fixme|placeholder|lorem\s+ipsum|example\s+question)\b",
    flags=re.IGNORECASE,
)
_REFUSAL_ONLY_RE = re.compile(
    r"^(?:sorry[\s,.:;-]*)?(?:"
    r"i\s+(?:do\s+not|don['’]?t)\s+know|"
    r"i\s+(?:cannot|can['’]?t|am\s+unable\s+to)\s+answer(?:\s+that)?|"
    r"(?:i\s+am|i['’]?m)\s+not\s+able\s+to\s+help|"
    r"(?:there\s+is\s+)?(?:not\s+enough|insufficient)\s+(?:context|information)|"
    r"no\s+answer|unknown|unsupported|n/?a"
    r")(?:[\s.!?]*(?:because\s+.{0,120})?)?$",
    flags=re.IGNORECASE,
)
_REFUSAL_PREFIX_RE = re.compile(
    r"^(?:sorry[\s,.:;-]*)?(?:"
    r"i\s+(?:do\s+not|don['’]?t)\s+know|"
    r"i\s+(?:cannot|can['’]?t|am\s+unable\s+to)\s+answer|"
    r"as\s+an\s+ai\b|"
    r"(?:there\s+is\s+)?(?:not\s+enough|insufficient)\s+(?:context|information)"
    r")",
    flags=re.IGNORECASE,
)
_BEST_GUESS_RE = re.compile(
    r"\b(?:but|however|best\s+guess|likely|probably|i\s+think|could\s+be|"
    r"appears?\s+to\s+be)\b",
    flags=re.IGNORECASE,
)
_NEGATION_WORDS = {
    "false",
    "incorrect",
    "isnt",
    "isn't",
    "myth",
    "never",
    "no",
    "not",
    "untrue",
}
_LEAKAGE_STOPWORDS = {
    "a",
    "about",
    "an",
    "and",
    "are",
    "can",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "know",
    "me",
    "of",
    "on",
    "should",
    "tell",
    "that",
    "the",
    "this",
    "to",
    "was",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
}


class DatasetValidationError(ValueError):
    """Raised when a benchmark dataset or split boundary is invalid."""

    def __init__(self, errors: Sequence[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


class PointGrader(Protocol):
    """Optional semantic checker used only for capped secondary credit."""

    def check(
        self,
        *,
        question: str,
        answer: str,
        requirement: str,
        polarity: str,
    ) -> bool | None:
        """Return True/False, or None when the checker cannot decide."""


GraderCallable = Callable[..., bool | None]


class CitationChecker(Protocol):
    """Optional evidence checker for one candidate citation.

    The hook sees only the question, answer, submitted citation, and declared
    source metadata.  It does not see the system name or scores.
    """

    def check(
        self,
        *,
        question: str,
        answer: str,
        citation: Mapping[str, Any],
        source: Mapping[str, Any],
    ) -> bool | None:
        """Return whether the citation supports the answer, or None."""


CitationCallable = Callable[..., bool | None]


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value)).casefold()
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    chars = [ch if ch.isalnum() or ch == "_" else " " for ch in value]
    return _SPACE_RE.sub(" ", "".join(chars)).strip()


def _tokens(value: str) -> list[str]:
    return _WORD_RE.findall(_normalize(value))


def answer_presence_reason(answer: Any) -> str | None:
    """Return why an answer is absent, or None for a gradable attempt."""

    if answer is None or not isinstance(answer, str) or not answer.strip():
        return "missing"
    stripped = answer.strip()
    if _REFUSAL_ONLY_RE.fullmatch(stripped):
        return "explicit_refusal"
    tokens = _tokens(stripped)
    if not tokens:
        return "missing"
    if (
        _REFUSAL_PREFIX_RE.search(stripped)
        and len(tokens) <= 18
        and not _BEST_GUESS_RE.search(stripped)
    ):
        return "refusal_without_substantive_answer"
    return None


def answer_is_present(answer: Any) -> bool:
    """Return whether an answer contains a gradable attempt.

    A short refusal or blank is not an answer.  Uncertainty is allowed: a
    response such as "I'm not certain, but my best guess is ..." is gradable.
    """

    return answer_presence_reason(answer) is None


def _candidate_phrases(candidate: Any) -> tuple[str, ...]:
    if isinstance(candidate, str):
        return (candidate,)
    if not isinstance(candidate, Mapping):
        return ()
    if "all" in candidate and isinstance(candidate["all"], list):
        return tuple(str(v) for v in candidate["all"])
    if "any" in candidate and isinstance(candidate["any"], list):
        return tuple(str(v) for v in candidate["any"])
    return ()


def _phrase_occurrences(haystack: str, needle: str) -> Iterable[int]:
    padded_haystack = f" {haystack} "
    padded_needle = f" {needle} "
    start = 0
    while True:
        index = padded_haystack.find(padded_needle, start)
        if index < 0:
            return
        yield max(index - 1, 0)
        start = index + 1


def _occurrence_is_negated(answer: str, start: int) -> bool:
    prefix_tokens = answer[:start].split()
    window = prefix_tokens[-5:]
    return any(token in _NEGATION_WORDS for token in window)


def _string_match(answer: str, candidate: str, *, skip_negated: bool) -> bool:
    needle = _normalize(candidate)
    if not needle:
        return False
    for start in _phrase_occurrences(answer, needle):
        if not skip_negated or not _occurrence_is_negated(answer, start):
            return True
    return False


def _candidate_matches(answer: str, candidate: Any, *, skip_negated: bool) -> bool:
    if isinstance(candidate, str):
        return _string_match(answer, candidate, skip_negated=skip_negated)
    if not isinstance(candidate, Mapping):
        return False
    if "all" in candidate and isinstance(candidate["all"], list):
        return bool(candidate["all"]) and all(
            _string_match(answer, str(part), skip_negated=skip_negated)
            for part in candidate["all"]
        )
    if "any" in candidate and isinstance(candidate["any"], list):
        return any(
            _string_match(answer, str(part), skip_negated=skip_negated)
            for part in candidate["any"]
        )
    return False


def _expanded_aliases(item: Mapping[str, Any], point: Mapping[str, Any]) -> list[Any]:
    candidates: list[Any] = list(point.get("aliases") or [])
    candidates.append(str(point.get("claim", "")))
    claim = _normalize(str(point.get("claim", "")))
    alias_map = item.get("acceptable_aliases") or {}
    if isinstance(alias_map, Mapping):
        for canonical, aliases in alias_map.items():
            if _normalize(str(canonical)) in claim and isinstance(aliases, list):
                candidates.extend(aliases)
    return candidates


def _match_point(
    item: Mapping[str, Any],
    point: Mapping[str, Any],
    normalized_answer: str,
    *,
    skip_negated: bool,
) -> Any | None:
    for candidate in _expanded_aliases(item, point):
        if _candidate_matches(
            normalized_answer,
            candidate,
            skip_negated=skip_negated,
        ):
            return candidate
    return None


def _call_grader(
    grader: PointGrader | GraderCallable,
    *,
    question: str,
    answer: str,
    requirement: str,
    polarity: str,
) -> bool | None:
    check = getattr(grader, "check", grader)
    return check(
        question=question,
        answer=answer,
        requirement=requirement,
        polarity=polarity,
    )


def _normalize_citations(citations: Any) -> list[dict[str, Any]]:
    if citations is None:
        return []
    if not isinstance(citations, Sequence) or isinstance(
        citations, (str, bytes)
    ):
        raise ValueError("citations must be a list of source ids or objects")
    normalized: list[dict[str, Any]] = []
    for index, citation in enumerate(citations):
        if isinstance(citation, str):
            source_id = citation
            row: dict[str, Any] = {"source_id": source_id}
        elif isinstance(citation, Mapping):
            source_id = citation.get("source_id")
            if not isinstance(source_id, str) or not source_id.strip():
                raise ValueError(
                    f"citations[{index}].source_id must be a nonblank string"
                )
            row = {str(key): value for key, value in citation.items()}
        else:
            raise ValueError(
                f"citations[{index}] must be a source id or citation object"
            )
        if not source_id.strip():
            raise ValueError(f"citations[{index}] cannot be blank")
        row["source_id"] = source_id
        normalized.append(row)
    return normalized


def _call_citation_checker(
    checker: CitationChecker | CitationCallable,
    *,
    question: str,
    answer: str,
    citation: Mapping[str, Any],
    source: Mapping[str, Any],
) -> bool | None:
    check = getattr(checker, "check", checker)
    return check(
        question=question,
        answer=answer,
        citation=citation,
        source=source,
    )


def _score_citations(
    item: Mapping[str, Any],
    answer: str,
    citations: Any,
    *,
    sources: Mapping[str, Any] | None,
    checker: CitationChecker | CitationCallable | None,
) -> dict[str, Any]:
    normalized = _normalize_citations(citations)
    expected = set(str(ref) for ref in item.get("references", []))
    source_catalog = sources or {}
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    correct_source_ids: set[str] = set()
    for citation in normalized:
        source_id = citation["source_id"]
        declared_for_case = source_id in expected
        known_source = source_id in source_catalog
        hook_supported: bool | None = None
        if checker is not None and known_source:
            try:
                hook_supported = _call_citation_checker(
                    checker,
                    question=item["question"],
                    answer=answer,
                    citation=citation,
                    source=source_catalog[source_id],
                )
            except Exception as exc:  # pragma: no cover - defensive boundary
                errors.append(
                    f"{source_id}: {type(exc).__name__}: {exc}"
                )
        correct = declared_for_case and known_source and hook_supported is not False
        if correct:
            correct_source_ids.add(source_id)
        rows.append(
            {
                "source_id": source_id,
                "declared_for_case": declared_for_case,
                "known_source": known_source,
                "hook_supported": hook_supported,
                "correct": correct,
            }
        )
    provided_count = len(rows)
    correct_count = sum(bool(row["correct"]) for row in rows)
    return {
        "expected_source_ids": sorted(expected),
        "provided_count": provided_count,
        "correct_count": correct_count,
        # No citation is preferable to a fabricated citation. Grounding
        # coverage is measured separately by recall.
        "correctness": (
            _round(correct_count / provided_count) if provided_count else 1.0
        ),
        "recall": _round(len(correct_source_ids) / len(expected))
        if expected
        else 1.0,
        "hook_enabled": checker is not None,
        "details": rows,
        "errors": errors,
    }


def _empty_point_results(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rubric = item["rubric"]
    for tier in ("must_include", "should_include", "must_not_include"):
        for point in rubric[tier]:
            rows.append(
                {
                    "point_id": point["id"],
                    "tier": tier,
                    "claim": point["claim"],
                    "weight": float(
                        point.get("penalty", point.get("weight", 0.0))
                    ),
                    "hit": False,
                    "match_method": "not_evaluated",
                    "matched_alias": None,
                    "grader_hit": None,
                }
            )
    return rows


def _round(value: float) -> float:
    return round(float(value), 6)


def _confidence_result(
    confidence: float | int | None,
    *,
    target: float,
) -> dict[str, Any]:
    if confidence is None:
        return {
            "reported": None,
            "target": _round(target),
            "absolute_error": None,
            "brier_score": None,
        }
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number between 0 and 1")
    value = float(confidence)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("confidence must be a finite number between 0 and 1")
    return {
        "reported": _round(value),
        "target": _round(target),
        "absolute_error": _round(abs(value - target)),
        "brier_score": _round((value - target) ** 2),
    }


def score_answer(
    item: Mapping[str, Any],
    answer: str | None,
    *,
    grader: PointGrader | GraderCallable | None = None,
    grader_credit_cap: float = DEFAULT_GRADER_CREDIT_CAP,
    citations: Any = None,
    confidence: float | int | None = None,
    sources: Mapping[str, Any] | None = None,
    citation_checker: CitationChecker | CitationCallable | None = None,
) -> dict[str, Any]:
    """Score one answer against one weighted rubric deterministically.

    The optional grader may add or remove at most ``grader_credit_cap`` of the
    available positive weight.  Refusals always score zero and are never sent
    to the grader.
    """

    if not 0 <= grader_credit_cap <= 1:
        raise ValueError("grader_credit_cap must be between 0 and 1")

    answer_text = answer if isinstance(answer, str) else ""
    presence_reason = answer_presence_reason(answer_text)
    citation_result = _score_citations(
        item,
        answer_text,
        citations,
        sources=sources,
        checker=citation_checker,
    )
    rubric = item["rubric"]
    positive_points = list(rubric["must_include"]) + list(
        rubric["should_include"]
    )
    points_possible = sum(float(point["weight"]) for point in positive_points)
    must_possible = sum(
        float(point["weight"]) for point in rubric["must_include"]
    )
    should_possible = sum(
        float(point["weight"]) for point in rubric["should_include"]
    )

    if presence_reason is not None:
        return {
            "schema_version": RESULT_SCHEMA_VERSION,
            "question_id": item["id"],
            "category": item["category"],
            "domain": item.get("domain", "unspecified"),
            "answer_present": False,
            "refusal_detected": presence_reason != "missing",
            "answer_presence_reason": presence_reason,
            "status": "missing_or_refusal",
            "score": 0.0,
            "deterministic_score": 0.0,
            "points_possible": _round(points_possible),
            "points_earned": 0.0,
            "deterministic_points_earned": 0.0,
            "penalty_applied": 0.0,
            "grader_adjustment": 0.0,
            "must_include": {
                "earned": 0.0,
                "possible": _round(must_possible),
                "coverage": 0.0,
            },
            "should_include": {
                "earned": 0.0,
                "possible": _round(should_possible),
                "coverage": 0.0,
            },
            "must_not_include": {"violations": 0, "penalty": 0.0},
            "point_results": _empty_point_results(item),
            "grader_errors": [],
            "citations": citation_result,
            "confidence": _confidence_result(confidence, target=0.0),
        }

    normalized_answer = _normalize(answer_text)
    rows: list[dict[str, Any]] = []
    grader_errors: list[str] = []
    deterministic_positive = 0.0
    deterministic_penalty = 0.0
    semantic_positive = 0.0
    semantic_penalty = 0.0
    deterministic_must = 0.0
    deterministic_should = 0.0
    semantic_must = 0.0
    semantic_should = 0.0

    for tier in ("must_include", "should_include"):
        for point in rubric[tier]:
            weight = float(point["weight"])
            matched = _match_point(
                item,
                point,
                normalized_answer,
                skip_negated=True,
            )
            deterministic_hit = matched is not None
            grader_hit: bool | None = None
            if deterministic_hit:
                deterministic_positive += weight
                if tier == "must_include":
                    deterministic_must += weight
                else:
                    deterministic_should += weight
            elif grader is not None:
                try:
                    grader_hit = _call_grader(
                        grader,
                        question=item["question"],
                        answer=answer_text,
                        requirement=point["claim"],
                        polarity="include",
                    )
                except Exception as exc:  # pragma: no cover - defensive boundary
                    grader_errors.append(
                        f"{point['id']}: {type(exc).__name__}: {exc}"
                    )
                if grader_hit:
                    semantic_positive += weight
                    if tier == "must_include":
                        semantic_must += weight
                    else:
                        semantic_should += weight
            rows.append(
                {
                    "point_id": point["id"],
                    "tier": tier,
                    "claim": point["claim"],
                    "weight": weight,
                    "hit": deterministic_hit or bool(grader_hit),
                    "match_method": (
                        "alias"
                        if deterministic_hit
                        else "grader"
                        if grader_hit
                        else "none"
                    ),
                    "matched_alias": matched,
                    "grader_hit": grader_hit,
                }
            )

    violations = 0
    deterministic_violations = 0
    for point in rubric["must_not_include"]:
        penalty = float(point["penalty"])
        matched = _match_point(
            item,
            point,
            normalized_answer,
            skip_negated=True,
        )
        deterministic_hit = matched is not None
        grader_hit = None
        if deterministic_hit:
            deterministic_penalty += penalty
            deterministic_violations += 1
        elif grader is not None:
            try:
                grader_hit = _call_grader(
                    grader,
                    question=item["question"],
                    answer=answer_text,
                    requirement=point["claim"],
                    polarity="exclude",
                )
            except Exception as exc:  # pragma: no cover - defensive boundary
                grader_errors.append(
                    f"{point['id']}: {type(exc).__name__}: {exc}"
                )
            if grader_hit:
                semantic_penalty += penalty
        if deterministic_hit or grader_hit:
            violations += 1
        rows.append(
            {
                "point_id": point["id"],
                "tier": "must_not_include",
                "claim": point["claim"],
                "weight": penalty,
                "hit": deterministic_hit or bool(grader_hit),
                "match_method": (
                    "alias"
                    if deterministic_hit
                    else "grader"
                    if grader_hit
                    else "none"
                ),
                "matched_alias": matched,
                "grader_hit": grader_hit,
            }
        )

    grader_cap_points = points_possible * grader_credit_cap
    credited_semantic_positive = min(semantic_positive, grader_cap_points)
    credited_semantic_penalty = min(semantic_penalty, grader_cap_points)
    deterministic_raw = max(
        0.0,
        min(points_possible, deterministic_positive - deterministic_penalty),
    )
    adjusted_raw = max(
        0.0,
        min(
            points_possible,
            deterministic_positive
            + credited_semantic_positive
            - deterministic_penalty
            - credited_semantic_penalty,
        ),
    )

    # Allocate capped semantic credit to must points first for coverage
    # reporting; the overall score remains independent of this presentation.
    semantic_budget = credited_semantic_positive
    credited_semantic_must = min(semantic_must, semantic_budget)
    semantic_budget -= credited_semantic_must
    credited_semantic_should = min(semantic_should, semantic_budget)
    must_earned = deterministic_must + credited_semantic_must
    should_earned = deterministic_should + credited_semantic_should

    score = (
        _round(adjusted_raw / points_possible)
        if points_possible
        else 0.0
    )
    deterministic_score = (
        _round(deterministic_raw / points_possible)
        if points_possible
        else 0.0
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "question_id": item["id"],
        "category": item["category"],
        "domain": item.get("domain", "unspecified"),
        "answer_present": True,
        "refusal_detected": False,
        "answer_presence_reason": None,
        "status": "scored",
        "score": score,
        "deterministic_score": deterministic_score,
        "points_possible": _round(points_possible),
        "points_earned": _round(adjusted_raw),
        "deterministic_points_earned": _round(deterministic_raw),
        "penalty_applied": _round(
            deterministic_penalty + credited_semantic_penalty
        ),
        "grader_adjustment": _round(
            credited_semantic_positive - credited_semantic_penalty
        ),
        "must_include": {
            "earned": _round(must_earned),
            "possible": _round(must_possible),
            "coverage": _round(must_earned / must_possible)
            if must_possible
            else 1.0,
        },
        "should_include": {
            "earned": _round(should_earned),
            "possible": _round(should_possible),
            "coverage": _round(should_earned / should_possible)
            if should_possible
            else 1.0,
        },
        "must_not_include": {
            "violations": violations,
            "deterministic_violations": deterministic_violations,
            "penalty": _round(
                deterministic_penalty + credited_semantic_penalty
            ),
        },
        "point_results": rows,
        "grader_errors": grader_errors,
        "citations": citation_result,
        "confidence": _confidence_result(confidence, target=score),
    }


def _load_json_or_yaml(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as json_error:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DatasetValidationError(
                [
                    f"{path}: not JSON-compatible YAML ({json_error})",
                    "Install PyYAML only for conventional YAML syntax; "
                    "checked-in datasets require no YAML dependency.",
                ]
            ) from exc
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise DatasetValidationError([f"{path}: invalid YAML: {exc}"]) from exc


def _validate_alias(alias: Any, location: str, errors: list[str]) -> None:
    if isinstance(alias, str):
        if not alias.strip():
            errors.append(f"{location}: alias cannot be blank")
        return
    if not isinstance(alias, Mapping) or set(alias) not in ({"all"}, {"any"}):
        errors.append(
            f"{location}: alias must be a string or one-key all/any mapping"
        )
        return
    values = alias.get("all", alias.get("any"))
    if (
        not isinstance(values, list)
        or not values
        or not all(isinstance(value, str) and value.strip() for value in values)
    ):
        errors.append(f"{location}: grouped alias must contain nonblank strings")


def _placeholder_locations(value: Any, location: str = "dataset") -> Iterable[str]:
    if isinstance(value, str):
        if _PLACEHOLDER_RE.search(value):
            yield location
    elif isinstance(value, Mapping):
        for key, nested in value.items():
            yield from _placeholder_locations(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            yield from _placeholder_locations(nested, f"{location}[{index}]")


def collect_dataset_errors(dataset: Any) -> list[str]:
    """Collect all schema and product-invariant violations in one pass."""

    errors: list[str] = []
    if not isinstance(dataset, Mapping):
        return ["dataset root must be an object"]
    if dataset.get("schema_version") != DATASET_SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {DATASET_SCHEMA_VERSION!r}"
        )
    for field in ("name", "description"):
        if (
            not isinstance(dataset.get(field), str)
            or not dataset[field].strip()
        ):
            errors.append(f"{field}: nonblank string required")
    if (
        not isinstance(dataset.get("as_of"), str)
        or not _ISO_DATE_RE.fullmatch(dataset["as_of"])
    ):
        errors.append("as_of: ISO date required")
    errors.extend(
        f"{location}: placeholder text is forbidden"
        for location in _placeholder_locations(dataset)
    )
    split = dataset.get("split")
    if split not in {"anchors", "dev", "test"}:
        errors.append("split must be one of anchors, dev, test")
    sources = dataset.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        errors.append("sources must be a nonempty object")
        sources = {}
    else:
        for source_id, source in sources.items():
            location = f"sources.{source_id}"
            if not isinstance(source_id, str) or not _ID_RE.fullmatch(source_id):
                errors.append(f"{location}: invalid source id")
            if not isinstance(source, Mapping):
                errors.append(f"{location}: source must be an object")
                continue
            for field in ("title", "publisher", "url", "as_of"):
                if not isinstance(source.get(field), str) or not source[field].strip():
                    errors.append(f"{location}.{field}: nonblank string required")
            url = source.get("url", "")
            if isinstance(url, str) and not url.startswith("https://"):
                errors.append(f"{location}.url: HTTPS URL required")
            as_of = source.get("as_of", "")
            if isinstance(as_of, str) and not _ISO_DATE_RE.fullmatch(as_of):
                errors.append(f"{location}.as_of: ISO date required")

    questions = dataset.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("questions must be a nonempty list")
        return errors

    seen_ids: set[str] = set()
    seen_questions: dict[str, str] = {}
    for index, item in enumerate(questions):
        location = f"questions[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{location}: question must be an object")
            continue
        question_id = item.get("id")
        if not isinstance(question_id, str) or not _ID_RE.fullmatch(question_id):
            errors.append(f"{location}.id: invalid id")
            question_id = f"index-{index}"
        elif question_id in seen_ids:
            errors.append(f"{location}.id: duplicate id {question_id!r}")
        seen_ids.add(str(question_id))

        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{location}.question: nonblank string required")
        else:
            normalized_question = _normalize(question)
            if normalized_question in seen_questions:
                errors.append(
                    f"{location}.question: duplicates "
                    f"{seen_questions[normalized_question]!r}"
                )
            seen_questions[normalized_question] = str(question_id)

        if item.get("answerable") is False or item.get("expect_answerable") is False:
            errors.append(
                f"{location}: unanswerable items are forbidden; every "
                "understandable question expects a substantive answer"
            )
        if item.get("category") not in CATEGORIES:
            errors.append(
                f"{location}.category: must be one of {sorted(CATEGORIES)}"
            )
        if item.get("domain") not in REQUIRED_DOMAINS:
            errors.append(
                f"{location}.domain: must be one of {sorted(REQUIRED_DOMAINS)}"
            )
        if not isinstance(item.get("language"), str) or not item["language"].strip():
            errors.append(f"{location}.language: nonblank string required")

        references = item.get("references")
        case_reference_ids: set[str] = set()
        if not isinstance(references, list) or not references:
            errors.append(f"{location}.references: nonempty list required")
        else:
            for ref in references:
                if not isinstance(ref, str):
                    errors.append(
                        f"{location}.references: source ids must be strings"
                    )
                elif ref not in sources:
                    errors.append(
                        f"{location}.references: unknown source {ref!r}"
                    )
                else:
                    case_reference_ids.add(ref)

        answer_policy = item.get("answer_policy")
        if not isinstance(answer_policy, Mapping):
            errors.append(f"{location}.answer_policy: object required")
        else:
            if answer_policy.get("must_answer") is not True:
                errors.append(
                    f"{location}.answer_policy.must_answer: must be true"
                )
            if answer_policy.get("allow_refusal") is not False:
                errors.append(
                    f"{location}.answer_policy.allow_refusal: must be false"
                )
            if (
                not isinstance(answer_policy.get("rationale"), str)
                or not answer_policy["rationale"].strip()
            ):
                errors.append(
                    f"{location}.answer_policy.rationale: nonblank string required"
                )

        follow_ups = item.get("follow_ups")
        if not isinstance(follow_ups, list):
            errors.append(f"{location}.follow_ups: list required")
        else:
            follow_up_ids: set[str] = set()
            for follow_up_index, follow_up in enumerate(follow_ups):
                follow_location = (
                    f"{location}.follow_ups[{follow_up_index}]"
                )
                if not isinstance(follow_up, Mapping):
                    errors.append(f"{follow_location}: object required")
                    continue
                follow_id = follow_up.get("id")
                if (
                    not isinstance(follow_id, str)
                    or not _ID_RE.fullmatch(follow_id)
                ):
                    errors.append(f"{follow_location}.id: invalid id")
                elif follow_id in follow_up_ids:
                    errors.append(
                        f"{follow_location}.id: duplicate id {follow_id!r}"
                    )
                follow_up_ids.add(str(follow_id))
                for field in ("question", "purpose"):
                    if (
                        not isinstance(follow_up.get(field), str)
                        or not follow_up[field].strip()
                    ):
                        errors.append(
                            f"{follow_location}.{field}: "
                            "nonblank string required"
                        )

        freshness = item.get("freshness")
        if not isinstance(freshness, Mapping):
            errors.append(f"{location}.freshness: object required")
        else:
            if freshness.get("classification") not in {
                "stable",
                "slow_changing",
                "time_sensitive",
            }:
                errors.append(
                    f"{location}.freshness.classification: invalid value"
                )
            if not isinstance(freshness.get("as_of"), str) or not _ISO_DATE_RE.fullmatch(
                freshness["as_of"]
            ):
                errors.append(f"{location}.freshness.as_of: ISO date required")
            if (
                not isinstance(freshness.get("expectation"), str)
                or not freshness["expectation"].strip()
            ):
                errors.append(
                    f"{location}.freshness.expectation: nonblank string required"
                )
            if (
                freshness.get("classification") == "time_sensitive"
                and (
                    not isinstance(freshness.get("max_age_days"), int)
                    or freshness["max_age_days"] <= 0
                )
            ):
                errors.append(
                    f"{location}.freshness.max_age_days: positive integer required "
                    "for time-sensitive questions"
                )

        rubric = item.get("rubric")
        if not isinstance(rubric, Mapping):
            errors.append(f"{location}.rubric: object required")
            continue
        point_ids: set[str] = set()
        positive_weight = 0.0
        for tier in ("must_include", "should_include", "must_not_include"):
            points = rubric.get(tier)
            if not isinstance(points, list):
                errors.append(f"{location}.rubric.{tier}: list required")
                continue
            if not points:
                errors.append(
                    f"{location}.rubric.{tier}: cannot be empty"
                )
            for point_index, point in enumerate(points):
                point_location = (
                    f"{location}.rubric.{tier}[{point_index}]"
                )
                if not isinstance(point, Mapping):
                    errors.append(f"{point_location}: point must be an object")
                    continue
                point_id = point.get("id")
                if (
                    not isinstance(point_id, str)
                    or not _ID_RE.fullmatch(point_id)
                ):
                    errors.append(f"{point_location}.id: invalid id")
                elif point_id in point_ids:
                    errors.append(
                        f"{point_location}.id: duplicate point id {point_id!r}"
                    )
                elif isinstance(question_id, str) and not point_id.startswith(
                    f"{question_id}."
                ):
                    errors.append(
                        f"{point_location}.id: must start with "
                        f"{question_id!r} plus a dot"
                    )
                point_ids.add(str(point_id))
                if (
                    not isinstance(point.get("claim"), str)
                    or not point["claim"].strip()
                ):
                    errors.append(
                        f"{point_location}.claim: nonblank string required"
                    )
                weight_key = (
                    "penalty" if tier == "must_not_include" else "weight"
                )
                weight = point.get(weight_key)
                if (
                    isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or weight <= 0
                ):
                    errors.append(
                        f"{point_location}.{weight_key}: positive number required"
                    )
                elif tier != "must_not_include":
                    positive_weight += float(weight)
                elif float(weight) > 1:
                    errors.append(
                        f"{point_location}.penalty: cannot exceed 1"
                    )
                aliases = point.get("aliases")
                if not isinstance(aliases, list) or not aliases:
                    errors.append(
                        f"{point_location}.aliases: nonempty list required"
                    )
                else:
                    for alias_index, alias in enumerate(aliases):
                        _validate_alias(
                            alias,
                            f"{point_location}.aliases[{alias_index}]",
                            errors,
                        )
                if (
                    not isinstance(point.get("rationale"), str)
                    or not point["rationale"].strip()
                ):
                    errors.append(
                        f"{point_location}.rationale: nonblank string required"
                    )
                accepted_evidence = point.get("accepted_evidence")
                if (
                    not isinstance(accepted_evidence, list)
                    or not accepted_evidence
                    or not all(
                        isinstance(evidence, str) and evidence.strip()
                        for evidence in accepted_evidence
                    )
                ):
                    errors.append(
                        f"{point_location}.accepted_evidence: "
                        "nonempty string list required"
                    )
                point_references = point.get("references")
                if not isinstance(point_references, list) or not point_references:
                    errors.append(
                        f"{point_location}.references: nonempty list required"
                    )
                else:
                    for ref in point_references:
                        if ref not in case_reference_ids:
                            errors.append(
                                f"{point_location}.references: {ref!r} must "
                                "also appear in the case references"
                            )
        if not math.isclose(positive_weight, 1.0, abs_tol=1e-9):
            errors.append(
                f"{location}.rubric: positive weights must sum to 1.0, "
                f"found {positive_weight:.6g}"
            )

        if split == "anchors":
            aliases = item.get("acceptable_aliases")
            if not isinstance(aliases, Mapping) or not aliases:
                errors.append(
                    f"{location}.acceptable_aliases: nonempty object required"
                )
            else:
                for canonical, values in aliases.items():
                    if (
                        not isinstance(canonical, str)
                        or not canonical.strip()
                        or not isinstance(values, list)
                        or not values
                        or not all(
                            isinstance(value, str) and value.strip()
                            for value in values
                        )
                    ):
                        errors.append(
                            f"{location}.acceptable_aliases: each canonical "
                            "term needs nonblank aliases"
                        )
            style = item.get("style")
            if not isinstance(style, Mapping):
                errors.append(f"{location}.style: object required")
            else:
                for field in ("tone", "structure", "expectation"):
                    if (
                        not isinstance(style.get(field), str)
                        or not style[field].strip()
                    ):
                        errors.append(
                            f"{location}.style.{field}: nonblank string required"
                        )
                if (
                    not isinstance(style.get("max_words"), int)
                    or style["max_words"] <= 0
                ):
                    errors.append(
                        f"{location}.style.max_words: positive integer required"
                    )
            if (
                not isinstance(item.get("exemplar"), str)
                or not item["exemplar"].strip()
            ):
                errors.append(f"{location}.exemplar: nonblank string required")
    return errors


def validate_dataset(dataset: Any) -> Mapping[str, Any]:
    errors = collect_dataset_errors(dataset)
    if errors:
        raise DatasetValidationError(errors)
    return dataset


def load_dataset(
    path: str | Path,
    *,
    expected_split: str | None = None,
) -> Mapping[str, Any]:
    dataset = validate_dataset(_load_json_or_yaml(Path(path)))
    if expected_split is not None and dataset["split"] != expected_split:
        raise DatasetValidationError(
            [
                f"{path}: expected split {expected_split!r}, "
                f"found {dataset['split']!r}"
            ]
        )
    return dataset


def _content_tokens(value: str) -> set[str]:
    return {
        token
        for token in _tokens(value)
        if token not in _LEAKAGE_STOPWORDS and len(token) > 1
    }


def _rubric_phrases(item: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for tier in ("must_include", "should_include", "must_not_include"):
        for point in item["rubric"][tier]:
            yield point["id"], point["claim"]


def find_split_leakage(
    train_datasets: Sequence[Mapping[str, Any]],
    test_dataset: Mapping[str, Any],
    *,
    spec_texts: Sequence[tuple[str, str]] = (),
) -> list[dict[str, str]]:
    """Return conservative train/test and checker-spec leakage findings."""

    findings: list[dict[str, str]] = []
    train_items = [
        item
        for dataset in train_datasets
        for item in dataset["questions"]
    ]
    test_items = list(test_dataset["questions"])

    for test_item in test_items:
        test_question = _normalize(test_item["question"])
        test_tokens = _content_tokens(test_item["question"])
        for train_item in train_items:
            train_question = _normalize(train_item["question"])
            if test_question == train_question:
                findings.append(
                    {
                        "type": "exact_question",
                        "train_id": train_item["id"],
                        "test_id": test_item["id"],
                        "detail": test_item["question"],
                    }
                )
                continue
            train_tokens = _content_tokens(train_item["question"])
            union = train_tokens | test_tokens
            intersection = train_tokens & test_tokens
            jaccard = len(intersection) / len(union) if union else 0.0
            sequence_ratio = difflib.SequenceMatcher(
                None,
                train_question,
                test_question,
            ).ratio()
            if len(intersection) >= 4 and (
                jaccard >= 0.82 or sequence_ratio >= 0.90
            ):
                findings.append(
                    {
                        "type": "near_duplicate_question",
                        "train_id": train_item["id"],
                        "test_id": test_item["id"],
                        "detail": (
                            f"token_jaccard={jaccard:.3f}, "
                            f"sequence_ratio={sequence_ratio:.3f}"
                        ),
                    }
                )

        test_phrases = list(_rubric_phrases(test_item))
        for train_item in train_items:
            train_phrases = list(_rubric_phrases(train_item))
            for test_point_id, test_phrase in test_phrases:
                normalized_test_phrase = _normalize(test_phrase)
                if len(normalized_test_phrase) < 24:
                    continue
                for train_point_id, train_phrase in train_phrases:
                    if normalized_test_phrase == _normalize(train_phrase):
                        findings.append(
                            {
                                "type": "exact_claim",
                                "train_id": train_item["id"],
                                "test_id": test_item["id"],
                                "detail": (
                                    f"{train_point_id} == {test_point_id}: "
                                    f"{test_phrase}"
                                ),
                            }
                        )

        spec_needles = [
            ("question", test_item["question"]),
            *[
                (f"claim:{point_id}", phrase)
                for point_id, phrase in test_phrases
            ],
        ]
        for spec_name, spec_text in spec_texts:
            normalized_spec = _normalize(spec_text)
            for label, needle in spec_needles:
                normalized_needle = _normalize(needle)
                if (
                    len(normalized_needle) >= 24
                    and normalized_needle in normalized_spec
                ):
                    findings.append(
                        {
                            "type": "spec_leakage",
                            "train_id": spec_name,
                            "test_id": test_item["id"],
                            "detail": f"{label}: {needle}",
                        }
                    )
    return findings


def validate_benchmark(
    anchors: Mapping[str, Any],
    dev: Mapping[str, Any],
    test: Mapping[str, Any],
    *,
    spec_paths: Sequence[str | Path] = (),
) -> None:
    """Validate all splits, global IDs, and held-out leakage heuristics."""

    validate_dataset(anchors)
    validate_dataset(dev)
    validate_dataset(test)
    expected = (("anchors", anchors), ("dev", dev), ("test", test))
    errors: list[str] = []
    count_requirements = {
        "anchors": (ANCHOR_CASE_COUNT, ANCHOR_CASE_COUNT),
        "dev": (MIN_DEV_CASES, None),
        "test": (MIN_TEST_CASES, None),
    }
    for split, dataset in expected:
        if dataset["split"] != split:
            errors.append(
                f"expected {split!r} split, found {dataset['split']!r}"
            )
        minimum, exact = count_requirements[split]
        count = len(dataset["questions"])
        if exact is not None and count != exact:
            errors.append(
                f"{split} must contain exactly {exact} cases, found {count}"
            )
        elif exact is None and count < minimum:
            errors.append(
                f"{split} must contain at least {minimum} cases, found {count}"
            )
        present_domains = {
            item["domain"] for item in dataset["questions"]
        }
        missing_domains = sorted(REQUIRED_DOMAINS - present_domains)
        if missing_domains:
            errors.append(
                f"{split} is missing required domains: "
                f"{', '.join(missing_domains)}"
            )
    seen: dict[str, str] = {}
    seen_questions: dict[str, tuple[str, str]] = {}
    for split, dataset in expected:
        for item in dataset["questions"]:
            if item["id"] in seen:
                errors.append(
                    f"question id {item['id']!r} occurs in both "
                    f"{seen[item['id']]} and {split}"
                )
            seen[item["id"]] = split
            normalized_question = _normalize(item["question"])
            if normalized_question in seen_questions:
                other_split, other_id = seen_questions[normalized_question]
                errors.append(
                    f"question {item['id']!r} in {split} duplicates "
                    f"{other_id!r} in {other_split}"
                )
            seen_questions[normalized_question] = (split, item["id"])

    spec_texts = [
        (str(path), Path(path).read_text(encoding="utf-8"))
        for path in spec_paths
    ]
    leakage = find_split_leakage(
        [anchors, dev],
        test,
        spec_texts=spec_texts,
    )
    errors.extend(
        f"leakage[{finding['type']}]: {finding['train_id']} -> "
        f"{finding['test_id']} ({finding['detail']})"
        for finding in leakage
    )
    if errors:
        raise DatasetValidationError(errors)


def _aggregate_results(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(results)
    if not count:
        return {
            "question_count": 0,
            "answered_count": 0,
            "unanswered_count": 0,
            "refusal_count": 0,
            "missing_count": 0,
            "answer_rate": 0.0,
            "refusal_rate": 0.0,
            "mean_score": 0.0,
            "median_score": 0.0,
            "deterministic_mean_score": 0.0,
            "must_include_coverage": 0.0,
            "should_include_coverage": 0.0,
            "must_not_violation_rate": 0.0,
            "citation_correctness": 0.0,
            "citation_recall": 0.0,
            "citation_count": 0,
            "confidence_count": 0,
            "confidence_coverage": 0.0,
            "mean_confidence": None,
            "brier_score": None,
            "expected_calibration_error": None,
        }
    answered = sum(bool(result["answer_present"]) for result in results)
    refusals = sum(bool(result["refusal_detected"]) for result in results)
    missing = sum(
        result["answer_presence_reason"] == "missing" for result in results
    )
    must_earned = sum(result["must_include"]["earned"] for result in results)
    must_possible = sum(
        result["must_include"]["possible"] for result in results
    )
    should_earned = sum(
        result["should_include"]["earned"] for result in results
    )
    should_possible = sum(
        result["should_include"]["possible"] for result in results
    )
    violations = sum(
        result["must_not_include"]["violations"] for result in results
    )
    citation_count = sum(
        result["citations"]["provided_count"] for result in results
    )
    correct_citations = sum(
        result["citations"]["correct_count"] for result in results
    )
    expected_citation_count = sum(
        len(result["citations"]["expected_source_ids"]) for result in results
    )
    recalled_citations = sum(
        len(
            {
                detail["source_id"]
                for detail in result["citations"]["details"]
                if detail["correct"]
            }
        )
        for result in results
    )
    confidence_rows = [
        result["confidence"]
        for result in results
        if result["confidence"]["reported"] is not None
    ]
    expected_calibration_error: float | None = None
    if confidence_rows:
        weighted_error = 0.0
        for lower_index in range(10):
            lower = lower_index / 10
            upper = (lower_index + 1) / 10
            rows = [
                row
                for row in confidence_rows
                if lower <= row["reported"] < upper
                or (lower_index == 9 and row["reported"] == 1.0)
            ]
            if rows:
                confidence_mean = statistics.fmean(
                    row["reported"] for row in rows
                )
                target_mean = statistics.fmean(row["target"] for row in rows)
                weighted_error += (
                    len(rows)
                    / len(confidence_rows)
                    * abs(confidence_mean - target_mean)
                )
        expected_calibration_error = _round(weighted_error)
    return {
        "question_count": count,
        "answered_count": answered,
        "unanswered_count": count - answered,
        "refusal_count": refusals,
        "missing_count": missing,
        "answer_rate": _round(answered / count),
        "refusal_rate": _round(refusals / count),
        "mean_score": _round(
            statistics.fmean(result["score"] for result in results)
        ),
        "median_score": _round(
            statistics.median(result["score"] for result in results)
        ),
        "deterministic_mean_score": _round(
            statistics.fmean(
                result["deterministic_score"] for result in results
            )
        ),
        "must_include_coverage": _round(must_earned / must_possible)
        if must_possible
        else 1.0,
        "should_include_coverage": _round(should_earned / should_possible)
        if should_possible
        else 1.0,
        "must_not_violation_rate": _round(violations / count),
        "citation_correctness": _round(correct_citations / citation_count)
        if citation_count
        else 1.0,
        "citation_recall": _round(
            recalled_citations / expected_citation_count
        )
        if expected_citation_count
        else 1.0,
        "citation_count": citation_count,
        "confidence_count": len(confidence_rows),
        "confidence_coverage": _round(len(confidence_rows) / count),
        "mean_confidence": _round(
            statistics.fmean(row["reported"] for row in confidence_rows)
        )
        if confidence_rows
        else None,
        "brier_score": _round(
            statistics.fmean(row["brier_score"] for row in confidence_rows)
        )
        if confidence_rows
        else None,
        "expected_calibration_error": expected_calibration_error,
    }


def score_dataset(
    dataset: Mapping[str, Any],
    answers: Mapping[str, Any],
    *,
    grader: PointGrader | GraderCallable | None = None,
    grader_credit_cap: float = DEFAULT_GRADER_CREDIT_CAP,
    citation_checker: CitationChecker | CitationCallable | None = None,
    system_name: str = "unnamed",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Score a complete split and return a stable report schema."""

    validate_dataset(dataset)
    results: list[dict[str, Any]] = []
    for item in dataset["questions"]:
        candidate = answers.get(item["id"], "")
        if isinstance(candidate, Mapping):
            answer = candidate.get("answer", "")
            citations = candidate.get("citations", [])
            confidence = candidate.get("confidence")
        else:
            answer = candidate
            citations = []
            confidence = None
        if answer is not None and not isinstance(answer, str):
            raise ValueError(
                f"answer for {item['id']!r} must be a string or null"
            )
        results.append(
            score_answer(
                item,
                answer,
                grader=grader,
                grader_credit_cap=grader_credit_cap,
                citations=citations,
                confidence=confidence,
                sources=dataset["sources"],
                citation_checker=citation_checker,
            )
        )
    category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    domain_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        category_rows[result["category"]].append(result)
        domain_rows[result["domain"]].append(result)
    categories = {
        category: _aggregate_results(rows)
        for category, rows in sorted(category_rows.items())
    }
    domains = {
        domain: _aggregate_results(rows)
        for domain, rows in sorted(domain_rows.items())
    }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": generated_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "system_name": system_name,
        "dataset": {
            "schema_version": dataset["schema_version"],
            "split": dataset["split"],
            "question_count": len(dataset["questions"]),
        },
        "scoring_policy": {
            "answer_presence_gate": True,
            "deterministic_alias_matching": True,
            "must_not_penalties": True,
            "grader_hook_enabled": grader is not None,
            "grader_blinded_to_system_identity": grader is not None,
            "grader_credit_cap": grader_credit_cap,
            "citation_hook_enabled": citation_checker is not None,
            "confidence_calibration_enabled": True,
        },
        "summary": _aggregate_results(results),
        "categories": categories,
        "domains": domains,
        "results": results,
    }


def _load_answers(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, Mapping):
        return {str(key): answer for key, answer in value.items()}
    if isinstance(value, list):
        answers: dict[str, Any] = {}
        for row in value:
            if not isinstance(row, Mapping) or "id" not in row or "answer" not in row:
                raise ValueError(
                    "answer rows must contain string 'id' and 'answer' fields"
                )
            candidate = {
                key: row[key]
                for key in ("answer", "citations", "confidence")
                if key in row
            }
            answers[str(row["id"])] = candidate
        return answers
    raise ValueError("answers JSON must be an object or list of rows")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("answers", type=Path)
    parser.add_argument("--system-name", default="unnamed")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    dataset = load_dataset(args.dataset)
    report = score_dataset(
        dataset,
        _load_answers(args.answers),
        system_name=args.system_name,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
