"""Evidence-support status from observable signals (NOT a model self-report).

We never ask the 0.6B model for a confidence number. Support is derived from
retrieval strength and whether the answer came from a precomputed card, so it
can later be calibrated against real correctness.
"""

from __future__ import annotations

from .retrieval import Candidate


def support_for_card() -> str:
    return "high"


def support_for_generated(best: Candidate | None) -> str:
    if best is None:
        return "low"
    if best.score >= 0.7:
        return "high"
    if best.score >= 0.5 and best.fts_hit:
        return "medium"
    if best.score >= 0.45:
        return "medium"
    return "low"


def should_queue(support: str, abstained: bool) -> bool:
    return abstained or support == "low"
