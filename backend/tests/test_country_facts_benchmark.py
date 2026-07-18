from __future__ import annotations

from eval.country_facts.runner import load_splits
from eval.country_facts.score import score_answer, score_dataset


def _case(dataset, case_id):
    return next(case for case in dataset["cases"] if case["id"] == case_id)


def test_shipped_splits_validate_and_are_held_out():
    splits = load_splits()
    assert len(splits["dev"]["cases"]) >= 6
    assert len(splits["test"]["cases"]) >= 8
    dev_countries = {case["country"] for case in splits["dev"]["cases"]}
    test_countries = {case["country"] for case in splits["test"]["cases"]}
    assert dev_countries.isdisjoint(test_countries)


def test_observed_korea_major_cities_answer_fails():
    dataset = load_splits()["dev"]
    case = _case(dataset, "dev-kr-major-cities")
    observed = (
        "South Korea's major cities are Seoul, Gangnam, Incheon, and Gimpo. "
        "Seoul is the central hub for business, culture, and conference travel. "
        "Gimpo is closer to the border and useful for transit and logistics."
    )
    result = score_answer(case, observed)
    assert result["passed"] is False
    # Busan and Daegu are required major cities that the failure omits.
    assert result["must_satisfied"] < result["must_total"]


def test_correct_korea_major_cities_answer_passes():
    dataset = load_splits()["dev"]
    case = _case(dataset, "dev-kr-major-cities")
    good = (
        "South Korea's largest cities include Seoul, Busan, Incheon, Daegu, "
        "Daejeon, Gwangju, and Ulsan."
    )
    result = score_answer(case, good)
    assert result["passed"] is True
    assert result["prohibited_hits"] == []


def test_gangnam_district_answer_is_not_false_flagged():
    dataset = load_splits()["dev"]
    case = _case(dataset, "dev-kr-gangnam-district")
    good = "Gangnam is a district of Seoul, not a separate city."
    result = score_answer(case, good)
    assert result["passed"] is True
    assert result["severe_violation"] is False


def test_merlion_good_and_bad():
    dataset = load_splits()["dev"]
    case = _case(dataset, "dev-sg-merlion")
    bad = (
        "Best guess: Singapore is named after a large island and the lion "
        "statue honors ties to South Korea."
    )
    good = (
        "The Merlion has a lion head and a fish body. The lion recalls the name "
        "Singapura, meaning Lion City in Sanskrit, while the fish tail recalls the "
        "old fishing village of Temasek."
    )
    assert score_answer(case, bad)["passed"] is False
    assert score_answer(case, good)["passed"] is True


def test_singapore_languages_single_language_claim_fails():
    dataset = load_splits()["dev"]
    case = _case(dataset, "dev-sg-languages")
    bad = "The only official language is English."
    result = score_answer(case, bad)
    assert result["passed"] is False
    assert result["prohibited_hits"]


def test_entity_type_proximity_mechanism_detects_district_listed_as_city():
    case = {
        "id": "synthetic-entity",
        "country": "South Korea",
        "question": "cities?",
        "family": "major_cities",
        "severity": "high",
        "must_include": [["Seoul"]],
        "entity_type_errors": [
            {"mention": "Gangnam", "cue_terms": ["cities"]}
        ],
        "sources": [{"title": "x", "publisher": "y", "url": "https://example.com", "as_of": "2026-07-18"}],
    }
    listed = "The cities are Seoul, Gangnam, and Incheon."
    result = score_answer(case, listed)
    assert result["entity_type_errors"]
    assert result["severe_violation"] is True


def test_score_dataset_summary_shapes():
    dataset = load_splits()["test"]
    answers = {case["id"]: "" for case in dataset["cases"]}
    report = score_dataset(dataset, answers, system_name="empty")
    assert report["summary"]["answer_rate"] == 0.0
    assert report["summary"]["cases"] == len(dataset["cases"])
    assert "by_family" in report["summary"]
