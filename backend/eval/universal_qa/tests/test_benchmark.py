from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from eval.universal_qa.paw_grader import BlindedPawGrader
from eval.universal_qa.runner import (
    DEFAULT_DATASET_PATHS,
    load_benchmark,
    main as runner_main,
    render_markdown_report,
)
from eval.universal_qa.score import (
    ANCHOR_CASE_COUNT,
    MIN_DEV_CASES,
    MIN_TEST_CASES,
    REQUIRED_DOMAINS,
    answer_is_present,
    answer_presence_reason,
    collect_dataset_errors,
    score_answer,
    score_dataset,
    validate_benchmark,
)


@pytest.fixture(scope="module")
def benchmark() -> dict[str, Any]:
    return load_benchmark()


def _case(dataset: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(item for item in dataset["questions"] if item["id"] == case_id)


def test_split_counts_domains_and_isolation(benchmark: dict[str, Any]) -> None:
    anchors = benchmark["anchors"]
    dev = benchmark["dev"]
    test = benchmark["test"]

    assert len(anchors["questions"]) == ANCHOR_CASE_COUNT == 8
    assert len(dev["questions"]) >= MIN_DEV_CASES == 30
    assert len(test["questions"]) >= MIN_TEST_CASES == 20
    for dataset in benchmark.values():
        assert {item["domain"] for item in dataset["questions"]} == REQUIRED_DOMAINS
        assert collect_dataset_errors(dataset) == []

    validate_benchmark(anchors, dev, test)
    all_cases = [
        item
        for dataset in benchmark.values()
        for item in dataset["questions"]
    ]
    assert len({item["id"] for item in all_cases}) == len(all_cases)
    assert len({item["question"].casefold() for item in all_cases}) == len(all_cases)


def test_every_case_is_backward_designed(benchmark: dict[str, Any]) -> None:
    for dataset in benchmark.values():
        for item in dataset["questions"]:
            assert item["answer_policy"]["must_answer"] is True
            assert item["answer_policy"]["allow_refusal"] is False
            assert item["references"]
            assert isinstance(item["follow_ups"], list)
            positive_weight = 0.0
            point_ids: set[str] = set()
            for tier in ("must_include", "should_include", "must_not_include"):
                assert item["rubric"][tier]
                for point in item["rubric"][tier]:
                    assert point["id"].startswith(f"{item['id']}.")
                    assert point["id"] not in point_ids
                    point_ids.add(point["id"])
                    assert point["rationale"].strip()
                    assert point["accepted_evidence"]
                    assert set(point["references"]) <= set(item["references"])
                    if tier == "must_not_include":
                        assert 0 < point["penalty"] <= 1
                    else:
                        assert point["weight"] > 0
                        positive_weight += point["weight"]
            assert positive_weight == pytest.approx(1.0)


def test_simida_anchor_encodes_required_corrections(
    benchmark: dict[str, Any],
) -> None:
    item = _case(benchmark["anchors"], "anchor-simida")
    positive_text = json.dumps(
        item["rubric"]["must_include"] + item["rubric"]["should_include"],
        ensure_ascii=False,
    )
    forbidden_text = json.dumps(
        item["rubric"]["must_not_include"],
        ensure_ascii=False,
    )

    assert "습니다" in positive_text
    assert "ㅂ니다" in positive_text
    assert "romanization" in positive_text.casefold()
    assert "formal" in positive_text.casefold()
    assert "polite" in positive_text.casefold()
    assert "standalone" in forbidden_text.casefold()
    assert "thank you" in forbidden_text.casefold()


def test_singapore_anchor_forbids_landmark_land_conflation(
    benchmark: dict[str, Any],
) -> None:
    item = _case(benchmark["anchors"], "anchor-singapore-marina-bay")
    positive_text = json.dumps(
        item["rubric"]["must_include"] + item["rubric"]["should_include"]
    ).casefold()
    forbidden_text = json.dumps(
        item["rubric"]["must_not_include"]
    ).casefold()

    assert "land reclamation" in positive_text
    assert "marina bay sands" in positive_text
    assert "three towers" in positive_text
    assert "skypark" in positive_text
    assert "not a literal boat" in positive_text
    assert "marina bay sands is reclaimed land" in forbidden_text


def test_deterministic_scoring_hits_and_penalizes(
    benchmark: dict[str, Any],
) -> None:
    item = _case(benchmark["anchors"], "anchor-simida")
    good = (
        "Simida is an approximate phonetic spelling of the formal sentence "
        "ending 습니다, romanized seumnida. 습니다 and ㅂ니다 are variants. "
        "It marks a formal polite statement and is not a standalone word; it "
        "attaches to a verb stem. 감사합니다 is the whole expression."
    )
    good_result = score_answer(item, good)
    assert good_result["deterministic_score"] == pytest.approx(1.0)
    assert good_result["must_not_include"]["violations"] == 0

    bad_result = score_answer(item, "Simida means thank you; it is a word for thank you.")
    assert bad_result["deterministic_score"] == 0
    assert bad_result["must_not_include"]["violations"] >= 1


@pytest.mark.parametrize(
    ("answer", "reason"),
    [
        ("", "missing"),
        ("I don't know.", "explicit_refusal"),
        ("As an AI, I cannot answer", "refusal_without_substantive_answer"),
    ],
)
def test_refusal_detection(answer: str, reason: str) -> None:
    assert answer_presence_reason(answer) == reason
    assert answer_is_present(answer) is False


def test_uncertain_best_guess_is_still_an_answer() -> None:
    answer = "I'm not certain, but my best guess is Tbilisi."
    assert answer_presence_reason(answer) is None
    assert answer_is_present(answer) is True


def test_semantic_grader_credit_is_capped(benchmark: dict[str, Any]) -> None:
    item = _case(benchmark["anchors"], "anchor-earth-seasons")

    def grader(**kwargs: Any) -> bool:
        return kwargs["polarity"] == "include"

    result = score_answer(
        item,
        "There is a physical reason for seasons.",
        grader=grader,
        grader_credit_cap=0.2,
    )
    assert result["deterministic_score"] == 0
    assert result["score"] == pytest.approx(0.2)
    assert result["grader_adjustment"] == pytest.approx(0.2)


def test_blinded_paw_adapter_passes_only_point_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def check_point(*args: Any) -> bool:
        calls.append(args)
        return True

    monkeypatch.setitem(
        sys.modules,
        "paw_helper",
        SimpleNamespace(grader=SimpleNamespace(check_point=check_point)),
    )
    checker_function = object()
    grader = BlindedPawGrader(checker_function)
    assert (
        grader.check(
            question="Question",
            answer="Candidate",
            requirement="One requirement",
            polarity="include",
        )
        is True
    )
    assert calls == [
        (checker_function, "Question", "Candidate", "One requirement")
    ]


def test_report_tracks_citations_refusals_and_calibration(
    benchmark: dict[str, Any],
) -> None:
    item = _case(benchmark["anchors"], "anchor-earth-seasons")
    answers = {
        item["id"]: {
            "answer": item["exemplar"],
            "citations": [{"source_id": "nasa-seasons"}],
            "confidence": 0.9,
        },
        "anchor-versailles-war": {
            "answer": "I don't know.",
            "confidence": 0.2,
        },
    }

    class CitationHook:
        def check(self, **kwargs: Any) -> bool:
            return kwargs["citation"]["source_id"] == "nasa-seasons"

    report = score_dataset(
        benchmark["anchors"],
        answers,
        citation_checker=CitationHook(),
        system_name="test-system",
        generated_at="2026-07-17T00:00:00Z",
    )
    summary = report["summary"]
    earth_result = next(
        row for row in report["results"] if row["question_id"] == item["id"]
    )
    versailles_result = next(
        row
        for row in report["results"]
        if row["question_id"] == "anchor-versailles-war"
    )

    assert summary["answered_count"] == 1
    assert summary["refusal_count"] == 1
    assert summary["missing_count"] == 6
    assert summary["answer_rate"] == pytest.approx(1 / 8)
    assert summary["confidence_count"] == 2
    assert earth_result["citations"]["correctness"] == 1
    assert earth_result["confidence"]["reported"] == 0.9
    assert versailles_result["refusal_detected"] is True
    markdown = render_markdown_report(report)
    assert "# Universal QA benchmark report" in markdown
    assert "| Answer rate | 0.125 |" in markdown
    assert "### anchor-earth-seasons" in markdown


def test_bad_citation_hook_and_confidence_validation(
    benchmark: dict[str, Any],
) -> None:
    item = _case(benchmark["anchors"], "anchor-earth-seasons")
    result = score_answer(
        item,
        item["exemplar"],
        citations=["nasa-seasons"],
        sources=benchmark["anchors"]["sources"],
        citation_checker=lambda **_: False,
    )
    assert result["citations"]["provided_count"] == 1
    assert result["citations"]["correct_count"] == 0
    assert result["citations"]["correctness"] == 0

    with pytest.raises(ValueError, match="between 0 and 1"):
        score_answer(item, item["exemplar"], confidence=1.1)


def test_cli_validate_and_score_outputs(
    benchmark: dict[str, Any],
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "validation.json"
    assert runner_main(["validate", "--output-json", str(validation_path)]) == 0
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    assert validation["counts"] == {"anchors": 8, "dev": 30, "test": 24}

    answer_path = tmp_path / "answers.json"
    item = _case(benchmark["anchors"], "anchor-earth-seasons")
    answer_path.write_text(
        json.dumps(
            {
                item["id"]: {
                    "answer": item["exemplar"],
                    "citations": ["nasa-seasons"],
                    "confidence": 0.95,
                }
            }
        ),
        encoding="utf-8",
    )
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    assert (
        runner_main(
            [
                "score",
                "--split",
                "anchors",
                "--answers",
                str(answer_path),
                "--system-name",
                "cli-test",
                "--output-json",
                str(json_path),
                "--output-markdown",
                str(markdown_path),
            ]
        )
        == 0
    )
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["system_name"] == "cli-test"
    assert report["dataset"]["question_count"] == 8
    assert report["summary"]["answer_rate"] == pytest.approx(0.125)
    assert markdown_path.read_text(encoding="utf-8").startswith(
        "# Universal QA benchmark report"
    )


def test_schema_files_are_json() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in (
        "dataset.schema.json",
        "result.schema.json",
        "report.schema.json",
    ):
        value = json.loads((root / name).read_text(encoding="utf-8"))
        assert value["$schema"].endswith("2020-12/schema")


def test_default_dataset_paths_are_yaml() -> None:
    assert set(DEFAULT_DATASET_PATHS) == {"anchors", "dev", "test"}
    assert all(path.suffix == ".yaml" for path in DEFAULT_DATASET_PATHS.values())
