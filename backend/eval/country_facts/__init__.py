"""Deterministic factual country QA benchmark.

This suite exists because the neural report explicitly does not gate releases on
factual accuracy. It scores atomic-claim coverage, prohibited claims, and
entity-type errors (for example, calling a city district a peer city) so the
observed "major cities of South Korea" failure becomes a hard regression.
"""

from .score import (
    DATASET_SCHEMA_VERSION,
    CountryFactsValidationError,
    aggregate_results,
    load_dataset,
    score_answer,
    score_dataset,
    validate_dataset,
    validate_splits,
)

__all__ = [
    "DATASET_SCHEMA_VERSION",
    "CountryFactsValidationError",
    "aggregate_results",
    "load_dataset",
    "score_answer",
    "score_dataset",
    "validate_dataset",
    "validate_splits",
]
