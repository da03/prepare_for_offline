"""Compile, pin, and run PAW experts.

PAW is used for fuzzy BEHAVIOR only (per the plan's division of labor):
- heard_expression_resolver: turn approximate phonetic input into canonical
  search candidates.
Compilation requires the network and runs during Prepare-for-Offline. At run
time experts are loaded fully offline via the single-expert LRU loader.

Only behavioral specs are sent to the compiler; personal content never is.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

import programasweights as paw

from ..config import get_settings
from .expert_loader import get_loader

# Set after compiling the release router. The settings table can override this
# with a newer immutable program ID without changing code.
DEFAULT_UI_ROUTER_PROGRAM_ID: str | None = "644ed37c4fc184726df6"
GLOBAL_PROGRAM_IDS: dict[str, str | None] = {
    "travel_topk_router": "1784f8b3105dcb9429f8",
    "travel_merge": "8d24bf8f33a81085e47a",
    "followup_rewriter": "5781306754b42304594a",
}

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
    "ui_action_router": (
        "Classify what the user wants the app to do. Return ONLY one label "
        "from: show_history, new_conversation, switch_context, create_context, "
        "prepare_context, add_source, show_context_status, show_settings, "
        "show_unresolved, show_storage, delete_context, answer_question.\n\n"
        "Input: what were my old conversations\nOutput: show_history\n\n"
        "Input: take me back to my previous chats\nOutput: show_history\n\n"
        "Input: start a fresh chat\nOutput: new_conversation\n\n"
        "Input: use my machine learning course\nOutput: switch_context\n\n"
        "Input: set me up for a conference next week\nOutput: create_context\n\n"
        "Input: make this available offline\nOutput: prepare_context\n\n"
        "Input: include this PDF in my materials\nOutput: add_source\n\n"
        "Input: is my offline context ready\nOutput: show_context_status\n\n"
        "Input: change my privacy preferences\nOutput: show_settings\n\n"
        "Input: which questions still need checking\nOutput: show_unresolved\n\n"
        "Input: how much disk space is this using\nOutput: show_storage\n\n"
        "Input: remove my old conference context\nOutput: delete_context\n\n"
        "Input: what does this Korean phrase mean\nOutput: answer_question"
    ),
    "travel_topk_router": (
        "Route a travel question to one or more relevant branches. Return ONLY "
        "a comma-separated list chosen from: itinerary, event, language, local_info. "
        "Use at most 3 labels.\n\n"
        "Input: What time is my hotel check-in?\nOutput: itinerary\n\n"
        "Input: Where is the ICML keynote and how do I get there?\n"
        "Output: event, local_info\n\n"
        "Input: What does annyeonghaseyo mean?\nOutput: language\n\n"
        "Input: Can I use the subway after the workshop?\n"
        "Output: local_info, event"
    ),
    "travel_merge": (
        "Choose how to combine a main travel answer with another grounded branch. "
        "Return ONLY MAIN, AUGMENT, or BRANCH. MAIN means keep the main answer; "
        "AUGMENT means add non-conflicting useful details; BRANCH means the branch "
        "better answers the question.\n\n"
        "Input: Question: Where is my keynote? Main: The keynote is at 9 AM. "
        "Branch: It is in Hall A.\nOutput: AUGMENT\n\n"
        "Input: Question: What time is check-in? Main: Check-in is 3 PM. "
        "Branch: Seoul taxis accept cards.\nOutput: MAIN\n\n"
        "Input: Question: Which hall is the workshop? Main: I do not know. "
        "Branch: The workshop is in Hall B.\nOutput: BRANCH"
    ),
    "followup_rewriter": (
        "Rewrite a travel follow-up as a standalone question. Return ONLY the "
        "rewritten question.\n\n"
        "Input: Previous: How do I reach the venue? Follow-up: What about Sunday?\n"
        "Output: How do I reach the venue on Sunday?\n\n"
        "Input: Previous: Is bossam spicy? Follow-up: Is it child-friendly?\n"
        "Output: Is bossam child-friendly?"
    ),
}

# Which roles are safety-critical behavioral experts worth pinning.
GLOBAL_ROLES = ["router", "sufficiency_classifier"]
DOMAIN_ROLES = ["heard_expression_resolver"]
GLOBAL_TRAVEL_ROLES = ["travel_topk_router", "travel_merge", "followup_rewriter"]
UI_ACTIONS = {
    "show_history",
    "new_conversation",
    "switch_context",
    "create_context",
    "prepare_context",
    "add_source",
    "show_context_status",
    "show_settings",
    "show_unresolved",
    "show_storage",
    "delete_context",
    "answer_question",
}
EXPERT_TESTS: dict[str, list[tuple[str, str]]] = {
    "heard_expression_resolver": [
        ("simida", "seumnida"),
        ("kamsahamnida", "gamsahamnida"),
    ],
    "travel_topk_router": [
        ("What time is my hotel check-in?", "itinerary"),
        ("Where is the keynote?", "event"),
        ("What does this Korean phrase mean?", "language"),
        ("How do I use the subway?", "local_info"),
    ],
    "travel_merge": [
        (
            "Question: Where is my keynote? Main: It is at 9 AM. "
            "Branch: It is in Hall A.",
            "augment",
        ),
        (
            "Question: What time is check-in? Main: 3 PM. "
            "Branch: Taxis accept cards.",
            "main",
        ),
    ],
    "followup_rewriter": [
        (
            "Previous: How do I reach the venue?\nFollow-up: What about Sunday?",
            "sunday",
        ),
        (
            "Previous: Is bossam spicy?\nFollow-up: Is it child-friendly?",
            "bossam",
        ),
    ],
}


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


def compile_ui_router(conn: sqlite3.Connection) -> str:
    """Compile the global UI router once and persist its immutable program ID."""
    settings = get_settings()
    program = paw.compile(
        EXPERT_SPECS["ui_action_router"], compiler=settings.compiler_fast
    )
    program_id = getattr(program, "id", None) or getattr(program, "program_id", "")
    if not program_id:
        raise RuntimeError("PAW compiler returned no program ID")
    conn.execute(
        """
        INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,
            updated_at=excluded.updated_at
        """,
        (
            "ui_router_program_id",
            json.dumps(program_id),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return program_id


def get_ui_router_program_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings WHERE key='ui_router_program_id'"
    ).fetchone()
    if row:
        try:
            value = json.loads(row["value"])
            if value:
                return str(value)
        except json.JSONDecodeError:
            pass
    return DEFAULT_UI_ROUTER_PROGRAM_ID


def run_ui_router(conn: sqlite3.Connection, text: str) -> str | None:
    """Lazy-run the global UI action router. Returns None when not cached."""
    program_id = get_ui_router_program_id(conn)
    if not program_id:
        return None
    try:
        raw = run_expert(program_id, text, max_tokens=12)
    except Exception:
        return None
    label = raw.strip().split()[0].lower().strip(".:,") if raw.strip() else ""
    return label if label in UI_ACTIONS else None


def ensure_ui_router_cached() -> None:
    """Download/cache the pinned router without loading another interpreter."""
    if not DEFAULT_UI_ROUTER_PROGRAM_ID:
        return
    from programasweights.client import PAWClient

    PAWClient().download_paw(DEFAULT_UI_ROUTER_PROGRAM_ID)


def run_global(role: str, text: str, *, max_tokens: int = 32) -> str | None:
    program_id = GLOBAL_PROGRAM_IDS.get(role)
    if not program_id:
        return None
    try:
        return run_expert(program_id, text, max_tokens=max_tokens)
    except Exception:
        return None


def evaluate_program(role: str, program_id: str) -> tuple[float, dict]:
    tests = EXPERT_TESTS.get(role, [])
    if not tests:
        return 1.0, {"tests": 0, "passed": 0}
    function = get_loader().get(program_id, offline=False)
    passed = 0
    outputs = []
    for input_text, expected in tests:
        try:
            output = function(input_text, max_tokens=32).strip().casefold()
        except Exception as exc:
            output = f"error:{exc}"
        ok = expected.casefold() in output
        passed += int(ok)
        outputs.append({"input": input_text, "expected": expected, "output": output, "ok": ok})
    return passed / len(tests), {
        "tests": len(tests),
        "passed": passed,
        "outputs": outputs,
    }


def compile_expert_version(
    conn: sqlite3.Connection,
    *,
    context_id: str | None,
    pack_id: str | None,
    role: str,
    compiler: str,
    stage: str,
) -> dict:
    program = paw.compile(EXPERT_SPECS[role], compiler=compiler)
    program_id = getattr(program, "id", None) or getattr(program, "program_id", "")
    if not program_id:
        raise RuntimeError(f"Compiler returned no program ID for {role}")
    score, metrics = evaluate_program(role, program_id)
    version_id = f"pv-{uuid.uuid4().hex[:14]}"
    conn.execute(
        """
        INSERT INTO program_versions (
            version_id, context_id, pack_id, role, program_id, compiler, stage,
            score, metrics, is_active, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,0,?)
        """,
        (
            version_id,
            context_id,
            pack_id,
            role,
            program_id,
            compiler,
            stage,
            score,
            json.dumps(metrics, ensure_ascii=False),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return {
        "version_id": version_id,
        "role": role,
        "program_id": program_id,
        "compiler": compiler,
        "stage": stage,
        "score": score,
        "metrics": metrics,
    }


def activate_version(conn: sqlite3.Connection, version: dict, spec: str | None = None) -> None:
    pack_id = version.get("pack_id")
    if not pack_id:
        row = conn.execute(
            "SELECT pack_id FROM program_versions WHERE version_id=?",
            (version["version_id"],),
        ).fetchone()
        pack_id = row["pack_id"] if row else None
    if not pack_id:
        return
    conn.execute(
        "UPDATE program_versions SET is_active=0 WHERE pack_id=? AND role=?",
        (pack_id, version["role"]),
    )
    conn.execute(
        "UPDATE program_versions SET is_active=1 WHERE version_id=?",
        (version["version_id"],),
    )
    conn.execute(
        """
        INSERT INTO experts (pack_id, role, program_id, compiler, spec)
        VALUES (?,?,?,?,?)
        ON CONFLICT(pack_id, role) DO UPDATE SET
            program_id=excluded.program_id, compiler=excluded.compiler,
            spec=excluded.spec
        """,
        (
            pack_id,
            version["role"],
            version["program_id"],
            version["compiler"],
            spec or EXPERT_SPECS[version["role"]],
        ),
    )
    conn.commit()


def ensure_global_programs_cached() -> None:
    from programasweights.client import PAWClient

    client = PAWClient()
    for program_id in GLOBAL_PROGRAM_IDS.values():
        if program_id:
            client.download_paw(program_id)
