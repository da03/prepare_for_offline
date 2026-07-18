"""Compile and evaluate release-global travel programs.

Run online before a release, then copy the printed immutable IDs into
GLOBAL_PROGRAM_IDS in app/services/paw_experts.py.
"""

from __future__ import annotations

import json

import programasweights as paw

from app.services.paw_experts import EXPERT_SPECS, GLOBAL_TRAVEL_ROLES, evaluate_program


def main() -> None:
    output = {}
    for role in GLOBAL_TRAVEL_ROLES:
        print(f"Compiling {role} with paw-ft-bs48...", flush=True)
        program = paw.compile(
            EXPERT_SPECS[role],
            compiler="paw-ft-bs48",
            public=True,
        )
        program_id = getattr(program, "id", None) or getattr(program, "program_id", "")
        score, metrics = evaluate_program(role, program_id)
        output[role] = {"program_id": program_id, "score": score, "metrics": metrics}
        print(f"{role}: {program_id} score={score:.0%}", flush=True)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
