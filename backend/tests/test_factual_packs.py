from __future__ import annotations

from app.services import factual_packs
from eval.country_facts.runner import load_splits
from eval.country_facts.score import score_dataset


def test_major_cities_lookup_is_grounded_and_correct():
    result = factual_packs.lookup("What are the major cities of South Korea?")
    assert result is not None
    assert result["pack_key"] == "country:south-korea"
    assert result["family"] == "major_cities"
    assert "Busan" in result["answer"] and "Daegu" in result["answer"]
    assert result["support"] == "prepared_facts"
    assert result["sources"]


def test_merlion_lookup_matches_indirect_phrasing():
    result = factual_packs.lookup("why does singapore have that lion thing")
    assert result is not None
    assert result["pack_key"] == "country:singapore"
    assert "Merlion" in result["answer"]


def test_singapore_languages_lookup_lists_all_four():
    result = factual_packs.lookup("What are the official languages of Singapore?")
    assert result is not None
    for language in ("English", "Malay", "Mandarin", "Tamil"):
        assert language in result["answer"]


def test_unmatched_questions_fall_back():
    assert factual_packs.lookup("What is the history of South Korea?") is None
    assert factual_packs.lookup("What is the capital of Australia?") is None
    assert factual_packs.lookup("") is None


def test_builtin_packs_answer_every_development_case_cleanly():
    dev = load_splits()["dev"]
    answers = {}
    for case in dev["cases"]:
        found = factual_packs.lookup(case["question"])
        answers[case["id"]] = found["answer"] if found else ""
    report = score_dataset(dev, answers, system_name="factual-packs")
    summary = report["summary"]
    assert summary["answer_rate"] == 1.0
    assert summary["pass_rate"] == 1.0
    assert summary["severe_violation_count"] == 0
    assert summary["prohibited_claim_rate"] == 0.0
