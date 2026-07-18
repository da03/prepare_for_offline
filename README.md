# Prepare for Offline

Ask anything, anywhere.

Prepare for Offline compiles small [ProgramAsWeights](https://programasweights.com)
neural programs while connected and runs them locally on Qwen3-0.6B. It has
exactly two primary surfaces:

- **Ask** runs a finetuned broad PAW answerer, then every relevant prepared
  specialist. One match replaces the broad draft directly; multiple matches
  are combined by a finetuned PAW aggregator.
- **Prepare** compiles one topic prompt—such as `Korean language for travel`—as
  an additional PAW specialist.

There are no documents, retrieval indexes, citations, web searches, file
attachments, or knowledge packs. Prepare uses PAW’s normal
specification-to-program API; it does not train from a corpus or an online
teacher.

## Install on macOS

1. Download the latest DMG from
   [GitHub Releases](https://github.com/da03/prepare_for_offline/releases/latest).
2. Drag **Prepare for Offline** into **Applications**.
3. For an ad-hoc-signed build, right-click it and choose **Open** once.

Release builds bundle Qwen3-0.6B and the built-in PAW programs, so Ask works
without a first-launch download. The current public build targets Apple
Silicon.

## How Ask works

```text
question
  ├── finetuned broad PAW answerer
  └── PAW matcher checks every prepared topic
        ├── no match → broad answer
        ├── one match → prepared answer
        └── multiple matches → PAW aggregator → one answer
```

The broad draft can appear first in the same answer card while specialists and
aggregation finish. Program names and routing details are intentionally hidden
from the normal UI.

Every prepared topic is checked independently. When several match, the app
runs all of them rather than reducing the question to the highest match.

## How Prepare works

Prepare deterministically wraps the user’s prompt in a fixed QA contract and
passes that exact specification to PAW:

1. Compile with Standard (`paw-4b-qwen3-0.6b`) as an internal prototype.
2. Run answer-contract smoke tests, without routing user questions to it.
3. Submit the identical specification to Finetuned Standard (`paw-ft-bs48`).
4. Mark the topic ready only after the finetuned immutable program passes;
   retain Standard solely for diagnostics and rollback.

PAW has no corpus-ingestion or arbitrary dataset-finetuning API. A prepared
program specializes the knowledge represented by the PAW compiler/runtime; it
cannot promise to learn private, live, or future facts that were never part of
that process.

## Development

Backend:

```bash
cd backend
python -m pip install \
  --extra-index-url https://pypi.programasweights.com/simple/ \
  -e ".[dev]"
python run.py
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

Build the macOS app:

```bash
bash scripts/build_release.sh
```

Build, replace, and relaunch the local `/Applications` copy:

```bash
bash scripts/install_local_app.sh
```

## Evaluation

The checked-in benchmark has anchor, development, and held-out splits with
weighted required claims and prohibited errors. Reference URLs are for human
rubric authors only; they never enter the product or PAW specifications.

```bash
cd backend
python -m eval.universal_qa.runner validate
python -m eval.run_neural_matrix \
  --mode topk --split anchors \
  --output /tmp/paw-topk-anchors.json
```

Release candidates must maintain a 100% answer rate and are compared on rubric
quality, prohibited errors, top-k lift over top-1, cold/warm latency, worker
RSS, adapter size, and prepared-topic lift.

## Verification

```bash
cd backend
python -m pytest -q
python -m ruff check app tests eval scripts

cd ../frontend
npm test -- --run
npm run build

cd ../desktop/src-tauri
cargo fmt --check
cargo check
```
