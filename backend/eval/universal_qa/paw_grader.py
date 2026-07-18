"""Optional adapter for a compiled ``paw_helper.grader`` rubric checker.

Importing this module does not require paw-helper.  The dependency is resolved
only when the adapter is constructed, so deterministic benchmark scoring stays
dependency-free.
"""

from __future__ import annotations

from typing import Any


class BlindedPawGrader:
    """Expose PAW's point checker without model or score metadata.

    Only the candidate text, question, and one rubric requirement are passed
    across the optional dependency boundary.  The checker never receives the
    system name, split aggregate, deterministic hits, or expected score.
    """

    def __init__(self, checker_function: Any):
        try:
            from paw_helper import grader
        except ImportError as exc:  # pragma: no cover - optional environment
            raise RuntimeError(
                "paw-helper is not installed; deterministic scoring remains "
                "available without it"
            ) from exc
        self._grader = grader
        self._checker_function = checker_function

    @classmethod
    def from_program_id(cls, program_id: str) -> "BlindedPawGrader":
        """Load a ProgramAsWeights checker only when explicitly requested."""

        try:
            import programasweights as paw
        except ImportError as exc:  # pragma: no cover - optional environment
            raise RuntimeError(
                "programasweights is required to load a compiled checker"
            ) from exc
        return cls(paw.function(program_id))

    def check(
        self,
        *,
        question: str,
        answer: str,
        requirement: str,
        polarity: str,
    ) -> bool:
        if polarity == "exclude":
            requirement = (
                "The answer asserts the following prohibited or false claim "
                f"as true: {requirement}"
            )
        elif polarity != "include":
            raise ValueError(f"unsupported polarity: {polarity!r}")
        return self._grader.check_point(
            self._checker_function,
            question,
            answer,
            requirement,
        )


# Backward-compatible name for early benchmark consumers.
PawHelperGrader = BlindedPawGrader

__all__ = ["BlindedPawGrader", "PawHelperGrader"]
