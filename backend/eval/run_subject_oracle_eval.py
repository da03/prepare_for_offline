"""Compare subject experts against broad using oracle domain selection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import programasweights as paw

from eval.universal_qa.runner import load_benchmark
from eval.universal_qa.score import score_dataset

ROOT = Path(__file__).resolve().parent
DOMAINS = {
    "history": "history",
    "language": "language_phonetic",
    "geography": "country_travel",
    "tourism": "country_travel",
    "science": "science",
    "practical": "everyday_practical",
}


def _score(program_id: str, dataset: dict, name: str) -> dict:
    function = paw.function(program_id, offline=True)
    answers = {
        item["id"]: {
            "answer": function(item["question"], max_tokens=320),
            "confidence": 0.65,
        }
        for item in dataset["questions"]
    }
    closer = getattr(function, "close", None)
    if callable(closer):
        closer()
    return score_dataset(dataset, answers, system_name=name)["summary"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--split", choices=("dev", "test"), default="dev"
    )
    args = parser.parse_args()
    programs = json.loads(
        (ROOT / "leakage_free_programs.json").read_text()
    )["programs"]
    source = load_benchmark()[args.split]
    report = {}
    for subject, domain in DOMAINS.items():
        dataset = dict(source)
        dataset["questions"] = [
            item for item in source["questions"] if item["domain"] == domain
        ]
        for stage in ("standard", "finetuned"):
            if stage not in programs[f"subject:{subject}"]:
                continue
            broad = _score(
                programs["broad_prose"][stage]["program_id"],
                dataset,
                f"broad_{stage}_{domain}",
            )
            specialist = _score(
                programs[f"subject:{subject}"][stage]["program_id"],
                dataset,
                f"{subject}_{stage}",
            )
            report[f"{subject}_{stage}"] = {
                "domain": domain,
                "questions": len(dataset["questions"]),
                "broad_score": broad["mean_score"],
                "specialist_score": specialist["mean_score"],
                "lift": round(
                    specialist["mean_score"] - broad["mean_score"],
                    6,
                ),
                "broad_answer_rate": broad["answer_rate"],
                "specialist_answer_rate": specialist["answer_rate"],
                "specialist_must_not_rate": specialist[
                    "must_not_violation_rate"
                ],
            }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
