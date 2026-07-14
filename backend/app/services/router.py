"""Query routing.

Phase 1 has a single domain (Korean language/culture) so routing is a light
heuristic. This is a seam: Phase 2 replaces it with a compiled PAW `router`
that classifies the question into a capability + corpus without changing the
call site.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Route:
    capability: str
    use_resolver: bool


def route(question: str) -> Route:
    # Everything currently flows through the evidence pipeline; the phonetic
    # resolver is always worth trying for short, non-sentence inputs.
    q = question.strip()
    short = len(q.split()) <= 4
    return Route(capability="korean_language", use_resolver=short or "mean" in q.lower())
