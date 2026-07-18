"""Compile leakage-free PAW candidates without modifying production selection."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.services.neural_specs import spec_sha256
from eval.leakage_free_specs import (
    BROAD_BEHAVIOR_SPEC,
    BROAD_PROSE_SPEC,
    SUBJECT_PROSE_SPECS,
    TRANSLATION_EXAMPLES_SPEC,
)
from scripts.compile_neural_programs import compile_program

OUTPUT = Path(__file__).with_name("leakage_free_programs.json")


def specifications() -> dict[str, str]:
    return {
        "broad_prose": BROAD_PROSE_SPEC,
        "broad_behavior": BROAD_BEHAVIOR_SPEC,
        "translation_examples": TRANSLATION_EXAMPLES_SPEC,
        **{
            f"subject:{name}": spec
            for name, spec in SUBJECT_PROSE_SPECS.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compiler",
        default="paw-4b-qwen3-0.6b",
        choices=("paw-4b-qwen3-0.6b", "paw-ft-bs48"),
    )
    parser.add_argument("--role", action="append")
    args = parser.parse_args()
    specs = specifications()
    roles = args.role or list(specs)
    document = (
        json.loads(OUTPUT.read_text())
        if OUTPUT.exists()
        else {"schema_version": 1, "programs": {}}
    )
    stage = "finetuned" if args.compiler == "paw-ft-bs48" else "standard"
    for role in roles:
        if role not in specs:
            raise SystemExit(f"Unknown role: {role}")
        started = time.perf_counter()
        program_id = compile_program(specs[role], args.compiler)
        document["programs"].setdefault(role, {})[stage] = {
            "program_id": program_id,
            "compiler": args.compiler,
            "spec_sha256": spec_sha256(specs[role]),
            "compile_seconds": round(time.perf_counter() - started, 3),
        }
        OUTPUT.write_text(json.dumps(document, indent=2) + "\n")
        print(f"{role}={program_id}")


if __name__ == "__main__":
    main()
