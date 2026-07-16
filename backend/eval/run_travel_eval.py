"""Evaluate the two-page travel pipeline on deterministic local evidence."""

from __future__ import annotations

import json
import os
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.models import ContextCreate, ContextSourceCreate  # noqa: E402
from app.services import (  # noqa: E402
    contexts,
    conversations,
    followups,
    jobs,
    paw_experts,
    travel_pipeline,
    trip_parser,
)
from eval.travel_dataset import TRAVEL_EVAL  # noqa: E402


def _peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return (rss if sys.platform == "darwin" else rss * 1024) / 1e6


def main() -> None:
    home = tempfile.mkdtemp(prefix="pfo-travel-eval-")
    os.environ["PREPARE_OFFLINE_HOME"] = home
    get_settings.cache_clear()
    init_db()
    conn = connect()
    trip = contexts.create(
        conn,
        ContextCreate(
            name="ICML 2026 Seoul",
            context_type="conference",
            goal="Attend ICML in Seoul",
            languages=["en", "ko"],
            expected_needs=[item["question"] for item in TRAVEL_EVAL],
            storage_budget_mb=800,
        ),
    )
    conn.execute(
        "UPDATE contexts SET trip_brief=? WHERE context_id=?",
        (
            json.dumps(
                {
                    "event": "ICML 2026",
                    "destination": "Seoul",
                    "coverage": [
                        "event schedule and venue",
                        "itinerary",
                        "language",
                        "arrival and transit",
                    ],
                }
            ),
            trip["context_id"],
        ),
    )
    source_ids = []
    for index, item in enumerate(TRAVEL_EVAL):
        source = contexts.add_source(
            conn,
            trip["context_id"],
            ContextSourceCreate(
                title=f"Eval source {index + 1}",
                content=item["source"],
                metadata={"topic": item["topic"], "stable": True},
            ),
        )
        source_ids.append(source["source_id"])
    raw = {
        "context_id": trip["context_id"],
        "selected_source_ids": source_ids,
        "expected_questions": [item["question"] for item in TRAVEL_EVAL],
        "compile_expert": False,
        "cache_ui_router": False,
        "optimize": False,
        "discover": False,
    }
    job_id = jobs.create_job(trip["context_id"], raw)
    jobs._run(job_id, raw)
    trip = contexts.get(conn, trip["context_id"])

    # Release-global PAW programs are evaluated by default; set
    # PFO_EVAL_GLOBAL=0 to measure deterministic fallbacks.
    original = paw_experts.GLOBAL_PROGRAM_IDS.copy()
    use_global = os.environ.get("PFO_EVAL_GLOBAL", "1") != "0"
    if not use_global:
        for key in paw_experts.GLOBAL_PROGRAM_IDS:
            paw_experts.GLOBAL_PROGRAM_IDS[key] = None
    route_hits = 0
    top1_hits = 0
    unnecessary = []
    grounded = 0
    cited = 0
    first_latencies = []
    final_latencies = []
    refinements = 0
    for item in TRAVEL_EVAL:
        route = travel_pipeline.route_top_k(item["question"], trip)
        expected = item["branches"]
        route_hits += int(expected.issubset(set(route.branches)))
        top1_hits += int(route.branches[0] in expected)
        unnecessary.append(len(set(route.branches) - expected))
        started = time.perf_counter()
        events = []
        first_seen = None
        for event in travel_pipeline.stream_answer(
            trip["active_pack_id"], item["question"], trip
        ):
            events.append(event)
            if (
                first_seen is None
                and event["type"] in {"answer_update", "final", "abstain"}
            ):
                first_seen = time.perf_counter() - started
        first_latencies.append(first_seen or (time.perf_counter() - started))
        final = [
            event for event in events if event["type"] in {"final", "abstain"}
        ][-1]
        final_latencies.append(time.perf_counter() - started)
        grounded += int(item["keyword"].casefold() in final["result"]["answer"].casefold())
        cited += int(bool(final["result"]["sources"]))
        refinements += int(final.get("refined", False))
    paw_experts.GLOBAL_PROGRAM_IDS.update(original)

    parsed = trip_parser.parse("I'm going to ICML 2026 in Seoul")
    conversation = conversations.create(conn, trip["context_id"])
    conversations.add_message(
        conn,
        conversation["conversation_id"],
        role="user",
        content="How do I reach the ICML venue?",
    )
    followup = followups.rewrite(
        conn, conversation["conversation_id"], "What about Sunday?"
    )
    n = len(TRAVEL_EVAL)
    report = {
        "router_top1_recall": round(top1_hits / n, 3),
        "router_topk_recall": round(route_hits / n, 3),
        "mean_unnecessary_branches": round(statistics.mean(unnecessary), 3),
        "grounded_accuracy": round(grounded / n, 3),
        "citation_correctness": round(cited / n, 3),
        "refinement_rate": round(refinements / n, 3),
        "median_time_to_first_answer_ms": round(
            statistics.median(first_latencies) * 1000, 1
        ),
        "median_time_to_final_answer_ms": round(
            statistics.median(final_latencies) * 1000, 1
        ),
        "one_sentence_parse_complete": bool(
            parsed.event and parsed.destination and not parsed.blocking_question
        ),
        "bounded_followup_rewrite_accuracy": float(
            followup["used_context"]
            and "venue" in followup["query"].casefold()
            and "sunday" in followup["query"].casefold()
        ),
        "global_programs_pinned": all(paw_experts.GLOBAL_PROGRAM_IDS.values()),
        "peak_rss_mb": round(_peak_rss_mb(), 1),
        "global_programs_enabled": use_global,
    }
    path = Path(__file__).with_name("last_travel_report.json")
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"Report written to {path}")
    conn.close()


if __name__ == "__main__":
    main()
