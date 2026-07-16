"""Config-driven travel answer tree with cheap parallel branches."""

from __future__ import annotations

import concurrent.futures
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..db import connect
from . import answerer, paw_experts, retrieval
from .source_freshness import (
    FreshnessClass,
    ask_freshness_decision,
    assess_freshness,
)


_DEFAULT_LABELS = {
    "itinerary": "your itinerary",
    "event": "event schedule and venue",
    "language": "language and culture",
    "local_info": "local transit, food, and safety",
}

_DEFAULT_KEYWORDS = {
    "itinerary": {
        "flight", "hotel", "reservation", "booking", "check-in", "checkout",
        "tomorrow", "my schedule", "confirmation", "itinerary",
    },
    "event": {
        "conference", "keynote", "workshop", "speaker", "session", "venue",
        "registration", "icml", "neurips", "acl", "cvpr", "schedule",
    },
    "language": {
        "mean", "say", "phrase", "translate", "language", "korean", "japanese",
        "etiquette", "custom", "polite",
    },
    "local_info": {
        "airport", "train", "subway", "metro", "bus", "taxi", "food", "restaurant",
        "emergency", "currency", "tip", "weather", "safety", "transit",
        "get there", "reach",
    },
}


def _load_config() -> dict:
    path = Path(__file__).with_name("travel_pipeline_config.json")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {
            "max_branches": 3,
            "relative_score_threshold": 0.5,
            "branches": {
                branch: {
                    "label": _DEFAULT_LABELS[branch],
                    "keywords": sorted(_DEFAULT_KEYWORDS[branch]),
                }
                for branch in _DEFAULT_LABELS
            },
        }


PIPELINE_CONFIG = _load_config()
BRANCH_LABELS = {
    branch: spec["label"]
    for branch, spec in PIPELINE_CONFIG["branches"].items()
}
BRANCH_KEYWORDS = {
    branch: set(spec["keywords"])
    for branch, spec in PIPELINE_CONFIG["branches"].items()
}


@dataclass
class RouteDecision:
    branches: list[str]
    scores: dict[str, float]
    source: str = "rules"


@dataclass
class BranchResult:
    branch: str
    label: str
    candidates: list[retrieval.Candidate]
    elapsed_ms: int = 0
    refresh_required: int = 0

    def summary(self) -> dict:
        return {
            "branch": self.branch,
            "label": self.label,
            "candidate_count": len(self.candidates),
            "source_ids": [item.source_id for item in self.candidates[:3]],
            "elapsed_ms": self.elapsed_ms,
            "refresh_required": self.refresh_required,
        }


def route_top_k(question: str, trip: dict, k: int | None = None) -> RouteDecision:
    k = k or int(PIPELINE_CONFIG.get("max_branches", 3))
    raw = paw_experts.run_global("travel_topk_router", question, max_tokens=20)
    if raw:
        labels = [
            item.strip().casefold().strip(".: ")
            for item in raw.replace("\n", ",").split(",")
        ]
        valid = [item for item in labels if item in BRANCH_LABELS]
        if valid:
            return RouteDecision(list(dict.fromkeys(valid))[:k], {}, source="paw")
    text = question.casefold()
    scores = {branch: 0.0 for branch in BRANCH_LABELS}
    for branch, keywords in BRANCH_KEYWORDS.items():
        scores[branch] = sum(1.0 for keyword in keywords if keyword in text)
    if any(token in text for token in ("my ", "i ", "we ", "our ")):
        scores["itinerary"] += 0.5
    event = str((trip.get("trip_brief") or {}).get("event", "")).casefold()
    if event and event in text:
        scores["event"] += 2.0
    if max(scores.values()) == 0:
        scores["local_info"] = 0.25
        scores["itinerary"] = 0.2
    ordered = sorted(scores, key=lambda name: scores[name], reverse=True)
    best = scores[ordered[0]]
    threshold = float(PIPELINE_CONFIG.get("relative_score_threshold", 0.5))
    selected = [
        branch
        for branch in ordered
        if scores[branch] > 0 and scores[branch] >= best * threshold
    ][:k]
    if not selected:
        selected = ordered[:1]
    return RouteDecision(selected, scores)


def _matches_branch(candidate: retrieval.Candidate, branch: str) -> bool:
    haystack = " ".join(
        [
            candidate.title,
            candidate.text[:500],
            str(candidate.meta.get("topic", "")),
            str(candidate.meta.get("source_type", "")),
        ]
    ).casefold()
    if branch == "itinerary" and candidate.meta.get("private"):
        return True
    return any(keyword in haystack for keyword in BRANCH_KEYWORDS[branch])


def _run_branch(pack_id: str, branch: str, question: str) -> BranchResult:
    import time

    started = time.perf_counter()
    conn = connect()
    try:
        candidates = retrieval.search(conn, pack_id, question, limit=10)
    finally:
        conn.close()
    matching = [item for item in candidates if _matches_branch(item, branch)]
    selected = []
    refresh_required = 0
    for item in matching:
        freshness_class = item.meta.get("freshness_class")
        observed = item.meta.get("retrieved_at") or item.as_of
        if freshness_class and observed:
            try:
                assessment = assess_freshness(
                    observed, FreshnessClass(freshness_class)
                )
                decision = ask_freshness_decision(
                    assessment,
                    consequence_flags=item.meta.get("consequence_flags", []),
                )
                if not decision.allow_offline:
                    refresh_required += 1
                    continue
            except (ValueError, TypeError):
                pass
        selected.append(item)
        if len(selected) >= 4:
            break
    return BranchResult(
        branch=branch,
        label=BRANCH_LABELS[branch],
        candidates=selected,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        refresh_required=refresh_required,
    )


def _dedupe(results: list[BranchResult]) -> list[retrieval.Candidate]:
    by_id: dict[str, retrieval.Candidate] = {}
    for result in results:
        for candidate in result.candidates:
            current = by_id.get(candidate.source_id)
            if current is None or candidate.score > current.score:
                by_id[candidate.source_id] = candidate
    return sorted(by_id.values(), key=lambda item: item.score, reverse=True)


def _merge_decision(results: list[BranchResult]) -> str:
    nonempty = [result for result in results if result.candidates]
    if not nonempty:
        return "none"
    if len(nonempty) == 1:
        return "main"
    main = nonempty[0]
    branch = nonempty[1]
    merge_input = (
        f"Question branches: {main.label} and {branch.label}. "
        f"Main evidence: {main.candidates[0].text[:240]}. "
        f"Branch evidence: {branch.candidates[0].text[:240]}."
    )
    raw = (
        paw_experts.run_global("travel_merge", merge_input, max_tokens=8)
        if os.environ.get("PREPARE_OFFLINE_USE_PAW_MERGE", "0") == "1"
        else None
    )
    if raw:
        decision = raw.strip().casefold().split()[0].strip(".:")
        if decision in {"main", "augment", "branch"}:
            return decision
    return "augment"


def stream_answer(
    pack_id: str, question: str, trip: dict
) -> Iterator[dict]:
    """Yield semantic progress and one progressively refined answer."""
    conn = connect()
    try:
        card = retrieval.match_answer_card(conn, pack_id, question)
        preliminary = None
        if card:
            preliminary = {
                "answer": card["answer"],
                "support": card["support"],
                "answer_mode": "answer_card",
                "sources": answerer._source_refs_for_ids(conn, pack_id, card["sources"]),
                "stale": not card["stable"],
            }
            yield {
                "type": "answer_update",
                "stage": "instant",
                "answer": preliminary["answer"],
                "result": preliminary,
                "sources": preliminary["sources"],
                "support": preliminary["support"],
                "stale": preliminary["stale"],
                "refined": False,
            }
    finally:
        conn.close()

    decision = route_top_k(question, trip)
    yield {
        "type": "route",
        "branches": [
            {"id": branch, "label": BRANCH_LABELS[branch]}
            for branch in decision.branches
        ],
    }
    for branch in decision.branches:
        yield {
            "type": "branch_started",
            "branch": branch,
            "label": BRANCH_LABELS[branch],
        }

    results: list[BranchResult] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(decision.branches))
    ) as executor:
        futures = {
            executor.submit(_run_branch, pack_id, branch, question): branch
            for branch in decision.branches
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            yield {"type": "branch_complete", **result.summary()}

    candidates = _dedupe(results)
    merge = _merge_decision(results)
    yield {
        "type": "synthesis",
        "message": f"Combining {len(candidates)} local source"
        + ("" if len(candidates) == 1 else "s"),
        "merge": merge,
    }
    final = answerer.answer_candidates(question, candidates)
    refined = bool(
        preliminary
        and final["answer"].strip() != preliminary["answer"].strip()
        and final["answer_mode"] != "abstained"
    )
    if preliminary and final["answer_mode"] == "abstained":
        final = preliminary
        refined = False
    yield {
        "type": "final" if final["answer_mode"] != "abstained" else "abstain",
        "answer": final["answer"],
        "result": final,
        "sources": final["sources"],
        "support": final["support"],
        "stale": final["stale"],
        "refined": refined,
        "merge": merge,
        "branches": [result.summary() for result in results],
        "requires_refresh": any(result.refresh_required for result in results),
    }
