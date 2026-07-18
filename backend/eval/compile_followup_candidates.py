"""Compile answer-aware follow-up candidates without changing production."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.services.neural_specs import spec_sha256
from eval.followup_specs import specifications
from scripts.compile_neural_programs import compile_program

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "followup_cases.json"
OUTPUT = ROOT / "followup_programs.json"


def _assert_no_case_leakage(specs: dict[str, str]) -> None:
    cases = json.loads(CASES.read_text())
    for name, spec in specs.items():
        leaked = [
            case["id"]
            for case in cases
            if case["previous_question"] in spec or case["follow_up"] in spec
        ]
        if leaked:
            raise RuntimeError(f"{name} leaks benchmark cases: {leaked}")


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
    _assert_no_case_leakage(specs)
    roles = args.role or list(specs)
    unknown = sorted(set(roles) - set(specs))
    if unknown:
        raise SystemExit(f"Unknown roles: {', '.join(unknown)}")

    document = (
        json.loads(OUTPUT.read_text())
        if OUTPUT.exists()
        else {"schema_version": 1, "programs": {}}
    )
    stage = "finetuned" if args.compiler == "paw-ft-bs48" else "standard"
    for role in roles:
        started = time.perf_counter()
        program_id = compile_program(specs[role], args.compiler)
        document["programs"].setdefault(role, {})[stage] = {
            "program_id": program_id,
            "compiler": args.compiler,
            "spec_sha256": spec_sha256(specs[role]),
            "compile_seconds": round(time.perf_counter() - started, 3),
        }
        OUTPUT.write_text(json.dumps(document, indent=2) + "\n")
        print(f"{role}:{stage}={program_id}", flush=True)


if __name__ == "__main__":
    main()
