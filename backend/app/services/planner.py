"""PackPlanner: turn a destination + interests + storage budget into a PackPlan.

This is the "contextual compilation" step: it decides which capabilities,
corpus topics, and experts to include, and estimates storage and preparation
time BEFORE anything is built. The storage budget genuinely drives selection -
lower-priority topics are dropped until the estimate fits.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import likely_questions, seed

# Rough per-expert on-disk cost (Qwen3-0.6B LoRA adapter ~22 MB).
EXPERT_BYTES = 22 * 1024 * 1024
# Base interpreter (shared on disk across experts).
BASE_MODEL_BYTES = 600 * 1024 * 1024
# Compile time per expert (fast compiler ~8s; finetuned ~180s).
COMPILE_S_FAST = 8
COMPILE_S_FINAL = 180

# Priority when trimming to fit the budget (most critical first).
TOPIC_PRIORITY = ["language", "emergency", "food", "transport", "etiquette", "money", "itinerary"]

# Interest keyword -> capability.
INTEREST_TO_CAPABILITY = {
    "language": "heard_expression",
    "culture": "etiquette",
    "etiquette": "etiquette",
    "food": "menu_help",
    "menus": "menu_help",
    "transport": "getting_around",
    "transportation": "getting_around",
    "money": "money",
    "safety": "safety",
    "emergency": "safety",
}


@dataclass
class PackPlan:
    destination: str
    interests: list[str]
    storage_budget_mb: int
    include_base_model: bool
    selected_capabilities: list[str] = field(default_factory=list)
    selected_topics: list[str] = field(default_factory=list)
    expert_specs: list[str] = field(default_factory=list)
    expected_questions: list[str] = field(default_factory=list)
    dropped_topics: list[str] = field(default_factory=list)
    storage_estimate_bytes: int = 0
    preparation_time_estimate_s: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _topic_bytes(topic: str) -> int:
    total = 0
    for d in seed.DOCUMENTS:
        if d["topic"] == topic:
            total += len(d["text"]) + len(d["title"])
    for c in seed.ANSWER_CARDS:
        if c["topic"] == topic:
            total += len(c["answer"]) + len(c["question"])
    # Indexes (FTS + trigrams) roughly triple the raw text footprint.
    return total * 3


def plan(
    destination: str = "South Korea",
    interests: list[str] | None = None,
    storage_budget_mb: int = 1200,
    *,
    finalize: bool = False,
    allow_online_synth: bool = False,
) -> PackPlan:
    interests = interests or ["language", "food", "transport", "etiquette", "money", "safety"]

    # Map interests -> capabilities -> topics + experts.
    capabilities: list[str] = []
    for it in interests:
        cap = INTEREST_TO_CAPABILITY.get(it.strip().lower())
        if cap and cap not in capabilities:
            capabilities.append(cap)

    topics: list[str] = []
    experts: list[str] = []
    for cap in capabilities:
        meta = seed.CAPABILITIES.get(cap, {})
        for t in meta.get("topics", []):
            if t not in topics:
                topics.append(t)
        for e in meta.get("experts", []):
            if e not in experts:
                experts.append(e)
    # Always include language + emergency as safety-critical defaults.
    for t in ("language", "emergency"):
        if t not in topics:
            topics.append(t)

    budget_bytes = storage_budget_mb * 1024 * 1024
    include_base = True
    dropped: list[str] = []

    def estimate(sel_topics: list[str], with_base: bool) -> int:
        total = sum(_topic_bytes(t) for t in sel_topics)
        total += len(experts) * EXPERT_BYTES
        if with_base:
            total += BASE_MODEL_BYTES
        return total

    # Trim lowest-priority topics until we fit (base model is essential; only
    # drop it as a last resort).
    ordered = sorted(topics, key=lambda t: TOPIC_PRIORITY.index(t) if t in TOPIC_PRIORITY else 99)
    selected = list(ordered)
    while estimate(selected, include_base) > budget_bytes and len(selected) > 1:
        victim = selected.pop()  # lowest priority is last
        dropped.append(victim)
    if estimate(selected, include_base) > budget_bytes:
        include_base = False  # last resort: rely on deterministic + cards only

    expected = likely_questions.generate(selected, allow_online=allow_online_synth)

    return PackPlan(
        destination=destination,
        interests=interests,
        storage_budget_mb=storage_budget_mb,
        include_base_model=include_base,
        selected_capabilities=capabilities,
        selected_topics=selected,
        expert_specs=experts,
        expected_questions=expected,
        dropped_topics=dropped,
        storage_estimate_bytes=estimate(selected, include_base),
        preparation_time_estimate_s=len(experts) * (COMPILE_S_FINAL if finalize else COMPILE_S_FAST),
    )
