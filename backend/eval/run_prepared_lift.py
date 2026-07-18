"""Compare one prepared PAW topic program against the broad PAW baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import programasweights as paw

from app.services.neural_specs import prepared_topic_spec, spec_sha256
from eval.universal_qa.runner import load_benchmark
from eval.universal_qa.score import score_dataset


def _answers(program_id: str, questions: list[dict]) -> dict:
    function = paw.function(program_id, offline=True)
    try:
        return {
            item["id"]: {
                "answer": function(item["question"], max_tokens=300),
                "citations": [],
                "confidence": 0.65,
            }
            for item in questions
        }
    finally:
        closer = getattr(function, "close", None)
        if callable(closer):
            closer()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topic", required=True)
    parser.add_argument("--program-id", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--split", default="dev", choices=("anchors", "dev", "test"))
    args = parser.parse_args()
    dataset = dict(load_benchmark()[args.split])
    dataset["questions"] = [
        item for item in dataset["questions"] if item["domain"] == args.domain
    ]
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "app"
            / "services"
            / "neural_programs.json"
        ).read_text()
    )
    broad_id = manifest["programs"]["broad"]["standard"]["program_id"]
    broad = score_dataset(
        dataset,
        _answers(broad_id, dataset["questions"]),
        system_name="broad",
    )
    prepared = score_dataset(
        dataset,
        _answers(args.program_id, dataset["questions"]),
        system_name="prepared",
    )
    result = {
        "topic": args.topic,
        "domain": args.domain,
        "split": args.split,
        "program_id": args.program_id,
        "spec_sha256": spec_sha256(prepared_topic_spec(args.topic)),
        "question_count": len(dataset["questions"]),
        "broad_score": broad["summary"]["mean_score"],
        "prepared_score": prepared["summary"]["mean_score"],
        "lift": round(
            prepared["summary"]["mean_score"] - broad["summary"]["mean_score"],
            6,
        ),
        "broad_answer_rate": broad["summary"]["answer_rate"],
        "prepared_answer_rate": prepared["summary"]["answer_rate"],
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
