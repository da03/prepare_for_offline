"""Compile, pin, and run PAW experts.

PAW is used for fuzzy BEHAVIOR only (per the plan's division of labor):
- heard_expression_resolver: turn approximate phonetic input into canonical
  search candidates.
Compilation requires the network and runs during Prepare-for-Offline. At run
time experts are loaded fully offline via the single-expert LRU loader.

Only behavioral specs are sent to the compiler; personal content never is.
"""

from __future__ import annotations

import sqlite3

import programasweights as paw

from ..config import get_settings
from .expert_loader import get_loader

# Global reusable / domain experts. These are behavioral and reusable across
# trips, so they are compiled once and pinned by immutable program id.
#
# NOTE on memory: each loaded expert is its own llama.cpp instance (~1 GB RSS
# measured). The router/sufficiency gates are therefore OPT-IN
# (PREPARE_OFFLINE_USE_PAW_GATES=1); by default the pipeline uses fast,
# deterministic gates so memory stays bounded to the base interpreter.
EXPERT_SPECS: dict[str, str] = {
    "heard_expression_resolver": (
        "You help an English-speaking traveler in Korea who typed a Korean "
        "word or phrase the way it SOUNDED to them, often misspelled. Output "
        "a comma-separated list of up to 5 likely canonical search terms: "
        "romanizations and, if you know it, Hangul. Output ONLY the list.\n\n"
        "Input: simida\n"
        "Output: seumnida, seubnida, -습니다, -ㅂ니다, imnida\n\n"
        "Input: kamsahamnida\n"
        "Output: gamsahamnida, 감사합니다, thank you\n\n"
        "Input: anyonghaseyo\n"
        "Output: annyeonghaseyo, 안녕하세요, hello\n\n"
        "Input: bibimbap\n"
        "Output: bibimbap, 비빔밥"
    ),
    "router": (
        "Classify a traveler's question into exactly one topic. Return ONLY "
        "one of: language, food, transport, etiquette, money, emergency, "
        "other.\n\n"
        "Input: what does simida mean\nOutput: language\n\n"
        "Input: is bibimbap spicy\nOutput: food\n\n"
        "Input: how do I use the subway\nOutput: transport\n\n"
        "Input: do I tip the waiter\nOutput: money\n\n"
        "Input: what number for an ambulance\nOutput: emergency"
    ),
    "sufficiency_classifier": (
        "Given a QUESTION and SOURCES, decide if the sources contain enough "
        "to answer. Return ONLY SUFFICIENT or INSUFFICIENT.\n\n"
        "Input: QUESTION: what is bossam\nSOURCES: bossam is boiled pork belly "
        "wrapped in vegetables\nOutput: SUFFICIENT\n\n"
        "Input: QUESTION: what is the capital of France\nSOURCES: kimchi is "
        "fermented cabbage\nOutput: INSUFFICIENT"
    ),
}

# Which roles are safety-critical behavioral experts worth pinning.
GLOBAL_ROLES = ["router", "sufficiency_classifier"]
DOMAIN_ROLES = ["heard_expression_resolver"]


def compile_expert(
    conn: sqlite3.Connection,
    pack_id: str,
    role: str,
    *,
    finalize: bool = False,
) -> dict:
    """Compile an expert online and store its immutable program id in the pack."""
    spec = EXPERT_SPECS[role]
    settings = get_settings()
    compiler = settings.compiler_final if finalize else settings.compiler_fast
    program = paw.compile(spec, compiler=compiler)
    program_id = getattr(program, "id", None) or getattr(program, "program_id", "")
    conn.execute(
        "INSERT INTO experts (pack_id, role, program_id, compiler, spec) "
        "VALUES (?,?,?,?,?) "
        "ON CONFLICT(pack_id, role) DO UPDATE SET "
        "program_id=excluded.program_id, compiler=excluded.compiler, spec=excluded.spec",
        (pack_id, role, program_id, compiler, spec),
    )
    conn.commit()
    return {"role": role, "program_id": program_id, "compiler": compiler}


def get_expert_program_id(
    conn: sqlite3.Connection, pack_id: str, role: str
) -> str | None:
    row = conn.execute(
        "SELECT program_id FROM experts WHERE pack_id=? AND role=?",
        (pack_id, role),
    ).fetchone()
    if row and row["program_id"]:
        return row["program_id"]
    return None


def run_expert(program_id: str, input_text: str, *, max_tokens: int = 96) -> str:
    fn = get_loader().get(program_id, offline=True)
    return fn(input_text, max_tokens=max_tokens)


def resolve_query_candidates(
    conn: sqlite3.Connection, pack_id: str, query: str
) -> list[str]:
    """Use the heard-expression resolver (if compiled) to propose search terms."""
    program_id = get_expert_program_id(conn, pack_id, "heard_expression_resolver")
    if not program_id:
        return []
    try:
        raw = run_expert(program_id, query)
    except Exception:
        return []
    terms = [t.strip() for t in raw.replace("\n", ",").split(",")]
    return [t for t in terms if t and t.lower() != query.lower()][:5]


def run_router(conn: sqlite3.Connection, pack_id: str, query: str) -> str | None:
    """Return the compiled router's topic label, or None if unavailable."""
    program_id = get_expert_program_id(conn, pack_id, "router")
    if not program_id:
        return None
    try:
        raw = run_expert(program_id, query, max_tokens=8)
    except Exception:
        return None
    label = raw.strip().split()[0].lower().strip(".:,") if raw.strip() else ""
    valid = {"language", "food", "transport", "etiquette", "money", "emergency", "other"}
    return label if label in valid else None


def run_sufficiency(
    conn: sqlite3.Connection, pack_id: str, question: str, sources_text: str
) -> bool | None:
    """Return True/False from the compiled sufficiency gate, or None if absent."""
    program_id = get_expert_program_id(conn, pack_id, "sufficiency_classifier")
    if not program_id:
        return None
    try:
        raw = run_expert(program_id, f"QUESTION: {question}\nSOURCES: {sources_text}", max_tokens=8)
    except Exception:
        return None
    up = raw.strip().upper()
    if up.startswith("SUFFICIENT"):
        return True
    if up.startswith("INSUFFICIENT"):
        return False
    return None
