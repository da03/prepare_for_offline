"""Plan a versioned offline pack from an editable context."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import likely_questions, seed, templates

# Rough per-expert on-disk cost (Qwen3-0.6B LoRA adapter ~22 MB).
EXPERT_BYTES = 22 * 1024 * 1024
# Base interpreter (shared on disk across experts).
BASE_MODEL_BYTES = 600 * 1024 * 1024
# Compile time per expert (fast compiler ~8s; finetuned ~180s).
COMPILE_S_FAST = 8
COMPILE_S_FINAL = 180

# Priority for the optional Korea example template.
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
    context_id: str
    name: str
    context_type: str
    goal: str
    languages: list[str]
    interests: list[str]
    storage_budget_mb: int
    include_base_model: bool
    selected_capabilities: list[str] = field(default_factory=list)
    selected_topics: list[str] = field(default_factory=list)
    expert_specs: list[str] = field(default_factory=list)
    expected_questions: list[str] = field(default_factory=list)
    dropped_topics: list[str] = field(default_factory=list)
    selected_source_ids: list[str] = field(default_factory=list)
    source_bytes: int = 0
    template_id: str | None = None
    privacy_disclosures: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    search_enabled: bool = True
    suggested_queries: list[str] = field(default_factory=list)
    source_publishers: list[str] = field(default_factory=list)
    freshness_summary: dict[str, int] = field(default_factory=dict)
    compiler_plan: dict = field(default_factory=dict)
    fits_budget: bool = True
    warnings: list[str] = field(default_factory=list)
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


def plan_context(
    context: dict,
    sources: list[dict],
    *,
    selected_source_ids: list[str] | None = None,
    selected_capabilities: list[str] | None = None,
    selected_topics: list[str] | None = None,
    expected_questions: list[str] | None = None,
    finalize: bool | None = None,
    allow_online_synth: bool | None = None,
) -> PackPlan:
    """Build an editable preview for an arbitrary user context."""
    enabled_sources = [source for source in sources if source.get("enabled", True)]
    if selected_source_ids is not None:
        selected = set(selected_source_ids)
        enabled_sources = [s for s in enabled_sources if s["source_id"] in selected]

    template = templates.get(context.get("template_id"))
    topics = (
        list(selected_topics)
        if selected_topics is not None
        else (
            list(template.get("topics", []))
            if template
            else list(dict.fromkeys(context.get("interests", [])))
        )
    )
    capabilities: list[str] = list(selected_capabilities or [])
    experts: list[str] = []
    if selected_capabilities is None:
        for interest in context.get("interests", []):
            capability = INTEREST_TO_CAPABILITY.get(str(interest).strip().lower())
            if capability and capability not in capabilities:
                capabilities.append(capability)
    if template and template["template_id"] == "korea":
        for capability in capabilities:
            for expert in seed.CAPABILITIES.get(capability, {}).get("experts", []):
                if expert not in experts:
                    experts.append(expert)
    elif "ko" in {str(language).lower() for language in context.get("languages", [])}:
        experts.append("heard_expression_resolver")

    source_bytes = sum(
        len(source.get("content", "").encode("utf-8"))
        + len(source.get("title", "").encode("utf-8"))
        for source in enabled_sources
    )
    budget_bytes = int(context.get("storage_budget_mb", 1200)) * 1024 * 1024
    include_base = True
    dropped: list[str] = []

    def estimate(sel_topics: list[str], with_base: bool) -> int:
        template_bytes = sum(_topic_bytes(topic) for topic in sel_topics) if template else 0
        total = template_bytes + source_bytes * 3 + len(experts) * EXPERT_BYTES
        if with_base:
            total += BASE_MODEL_BYTES
        return total

    selected_topics = sorted(
        topics,
        key=lambda topic: TOPIC_PRIORITY.index(topic)
        if topic in TOPIC_PRIORITY
        else len(TOPIC_PRIORITY),
    )
    while estimate(selected_topics, include_base) > budget_bytes and selected_topics:
        dropped.append(selected_topics.pop())
    if estimate(selected_topics, include_base) > budget_bytes:
        include_base = False

    allow_online = (
        context.get("privacy_mode") == "allow_online_planning"
        if allow_online_synth is None
        else allow_online_synth
    )
    questions = expected_questions or likely_questions.generate_for_context(
        context, enabled_sources, selected_topics, allow_online=allow_online
    )
    final = (
        context.get("preparation_quality") == "final"
        if finalize is None
        else finalize
    )
    disclosures = [
        "Sources and personal context remain on this Mac.",
        "Only reusable behavioral PAW specifications are sent to the compiler.",
    ]
    if allow_online:
        disclosures.append("Online planning is enabled for non-document context metadata.")
    final_estimate = estimate(selected_topics, include_base)
    warnings: list[str] = []
    if final_estimate > budget_bytes:
        warnings.append(
            "Selected sources exceed the storage budget. Remove sources or increase the budget."
        )
    if not include_base:
        warnings.append(
            "The base model does not fit this budget; only deterministic answers will be available."
        )
    brief = context.get("trip_brief") or {}
    publishers = [
        str(source.get("publisher") or source.get("title", "")).strip()
        for source in enabled_sources
        if source.get("publisher") or source.get("title")
    ]
    freshness_summary: dict[str, int] = {}
    for source in enabled_sources:
        key = str(source.get("freshness_class") or "unknown")
        freshness_summary[key] = freshness_summary.get(key, 0) + 1

    return PackPlan(
        context_id=context["context_id"],
        name=context["name"],
        context_type=context.get("context_type", "custom"),
        goal=context.get("goal", ""),
        languages=context.get("languages", []),
        interests=context.get("interests", []),
        storage_budget_mb=int(context.get("storage_budget_mb", 1200)),
        include_base_model=include_base,
        selected_capabilities=capabilities,
        selected_topics=selected_topics,
        expert_specs=experts,
        expected_questions=questions,
        dropped_topics=dropped,
        selected_source_ids=[source["source_id"] for source in enabled_sources],
        source_bytes=source_bytes,
        template_id=context.get("template_id"),
        privacy_disclosures=disclosures,
        coverage=list(brief.get("coverage") or selected_topics),
        search_enabled=bool(context.get("search_enabled", True)),
        suggested_queries=list(brief.get("suggested_queries") or []),
        source_publishers=list(dict.fromkeys(publishers))[:12],
        freshness_summary=freshness_summary,
        compiler_plan={
            "global_programs": "finetuned_pinned",
            "trip_specific": "fast_then_background_finetune" if experts else "not_needed",
            "ready_after": "fast",
        },
        fits_budget=final_estimate <= budget_bytes,
        warnings=warnings,
        storage_estimate_bytes=final_estimate,
        preparation_time_estimate_s=len(experts)
        * (COMPILE_S_FINAL if final else COMPILE_S_FAST),
    )


def plan(
    destination: str = "South Korea",
    interests: list[str] | None = None,
    storage_budget_mb: int = 1200,
    *,
    finalize: bool = False,
    allow_online_synth: bool = False,
) -> PackPlan:
    """Compatibility wrapper for the Korea example and existing eval harness."""
    example = {
        "context_id": "example-korea",
        "name": destination,
        "context_type": "trip",
        "goal": "Travel confidently offline.",
        "languages": ["en", "ko"],
        "interests": interests
        or ["language", "food", "transport", "etiquette", "money", "safety"],
        "expected_needs": [],
        "storage_budget_mb": storage_budget_mb,
        "privacy_mode": "allow_online_planning" if allow_online_synth else "local_only",
        "preparation_quality": "final" if finalize else "fast",
        "template_id": "korea",
    }
    return plan_context(
        example,
        [],
        finalize=finalize,
        allow_online_synth=allow_online_synth,
    )
