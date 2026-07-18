"""Compile the frozen PAW Offline global program set."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
import programasweights as paw
from programasweights.client import PAWClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.neural_specs import (
    AGGREGATOR_SPEC,
    BROAD_QA_SPEC,
    CRITIC_SPEC,
    HEARD_EXPRESSION_SPEC,
    LANGUAGE_INTENT_SPEC,
    PREPARED_MATCHER_SPEC,
    REVISION_SPEC,
    SUBJECT_SPECS,
    TOPK_ROUTER_SPEC,
    TRANSLATION_SPEC,
    spec_sha256,
)

OUTPUT = Path(__file__).resolve().parents[1] / "app" / "services" / "neural_programs.json"


def specifications() -> dict[str, str]:
    return {
        "broad": BROAD_QA_SPEC,
        "router": TOPK_ROUTER_SPEC,
        "aggregator": AGGREGATOR_SPEC,
        "critic": CRITIC_SPEC,
        "revision": REVISION_SPEC,
        "prepared_matcher": PREPARED_MATCHER_SPEC,
        "language_intent": LANGUAGE_INTENT_SPEC,
        "heard_expression": HEARD_EXPRESSION_SPEC,
        "translation": TRANSLATION_SPEC,
        **{f"subject:{name}": spec for name, spec in SUBJECT_SPECS.items()},
    }


def compile_program(spec: str, compiler: str) -> str:
    if compiler != "paw-ft-bs48":
        program = paw.compile(spec, compiler=compiler)
        program_id = getattr(program, "id", None) or getattr(
            program, "program_id", None
        )
        if not program_id:
            raise RuntimeError("Compiler returned no program ID")
        return program_id
    client = PAWClient()
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.post(
                f"{client._api_url}/api/v1/compile",
                json={"spec": spec, "public": True, "compiler": compiler},
                headers=client._headers(),
                timeout=600.0,
            )
            if response.status_code in {502, 504} and attempt < 2:
                time.sleep(5)
                continue
            response.raise_for_status()
            program_id = response.json().get("program_id")
            if not program_id:
                raise RuntimeError("Finetuned compiler returned no program ID")
            return program_id
        except httpx.ReadTimeout as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(5)
                continue
            raise
    raise RuntimeError("Finetuned compiler retries exhausted") from last_error


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compiler",
        default="paw-4b-qwen3-0.6b",
        choices=("paw-4b-qwen3-0.6b", "paw-ft-bs48"),
    )
    parser.add_argument("--role", action="append")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    specs = specifications()
    roles = args.role or list(specs)
    unknown = sorted(set(roles) - set(specs))
    if unknown:
        raise SystemExit(f"Unknown roles: {', '.join(unknown)}")
    existing = (
        json.loads(args.output.read_text())
        if args.output.exists()
        else {"schema_version": 1, "programs": {}}
    )
    stage = "finetuned" if args.compiler == "paw-ft-bs48" else "standard"
    for role in roles:
        spec = specs[role]
        started = time.perf_counter()
        program_id = compile_program(spec, args.compiler)
        existing["programs"].setdefault(role, {})[stage] = {
            "program_id": program_id,
            "compiler": args.compiler,
            "spec_sha256": spec_sha256(spec),
            "compile_seconds": round(time.perf_counter() - started, 3),
        }
        args.output.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False) + "\n"
        )
        print(f"{role}={program_id}")


if __name__ == "__main__":
    main()
