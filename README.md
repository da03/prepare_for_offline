# Prepare for Offline

Ask anything, anywhere.

Prepare for Offline compiles small [ProgramAsWeights](https://programasweights.com)
neural programs while connected and runs them locally on Qwen3-0.6B. It has
exactly two primary surfaces:

- **Ask** uses a leakage-free finetuned broad PAW answerer, narrow language
  programs when relevant, and every matching prepared specialist. One prepared
  match answers directly; multiple matches are combined by a PAW aggregator.
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
  ├── PAW matcher checks every prepared topic
  │     ├── one match → prepared answer
  │     └── multiple matches → PAW aggregator → one answer
  └── no prepared match
        ├── heard expression → finetuned expression interpreter
        ├── translation → finetuned translation helper
        └── everything else → leakage-free finetuned broad answerer
```

A prepared PAW adapter over the frozen 0.6B interpreter is not a reliable fact
store. An offline **factual pack** layer therefore answers structured questions
(capitals, major cities, entity hierarchy, official languages, well-known
landmarks) deterministically from curated, source-backed knowledge and takes
precedence over the neural answerer, so a hallucinating adapter can never
override a verified fact. Curated answers are labeled "Verified facts" in the UI.

Built-in packs currently cover a starter set of countries
(`backend/app/services/factual_packs`). Questions about countries without a pack
fall back to the neural answerer, which remains best-effort and can be wrong;
factual grounding must be built per country rather than assumed.

The broad draft can appear first in the same answer card while specialists and
aggregation finish. Program names and routing details are intentionally hidden
from the normal UI.

Every composer submission is a standalone question by default. **Follow up** on
an answer explicitly attaches only that question-answer pair to the next
request; no earlier transcript is sent. A separate PAW rewriter was rejected
after answer-aware Standard and Finetuned candidates failed the rewrite-fidelity
and meaningful-lift gates, so bounded context goes directly through the normal
answer graph without another model invocation.

Every prepared topic is checked independently. When several match, the app
runs all of them rather than reducing the question to the highest match.
The broad spec intentionally contains no examples; examples are used only in
narrow programs when held-out evaluation proves a meaningful gain.

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
that process. A country name therefore does not create a complete country
encyclopedia, and **Ready** means that the program compiled and passed its
answer contract—not that every factual answer was validated.

The PAW compile endpoint returns only when each compilation finishes; it does
not expose intermediate percentage progress. Prepare therefore shows the
current step, elapsed time, and expected duration with an indeterminate bar
instead of inventing fine-grained percentages.

## Local question history

Questions, answers, and routing traces are retained only in the local SQLite
history used by the app. They are not uploaded as telemetry. Topic prompts
submitted through **Prepare** are different: the generated specification is
sent to PAW's online compiler with `public: true` and may be listed on the
public PAW hub. Do not put private or sensitive information in a Prepare prompt.

Developers can export deduplicated question candidates for manual review
without answers:

```bash
cd backend
python -m scripts.export_question_candidates \
  --output question-candidates.json
```

Add `--include-answers` only when needed. Review and redact every export before
sharing it or adding selected questions to development benchmarks; exports are
never committed or uploaded automatically.

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
python -m eval.run_leakage_free_eval
python -m eval.run_language_generalization
python -m eval.run_prepared_ottoman_eval
python -m eval.run_followup_eval
```

Factual country QA has a dedicated deterministic benchmark with a held-out split
whose countries are disjoint from development, so fixing one country cannot pass
the held-out split by memorization:

```bash
cd backend
python -m eval.country_facts.runner validate
python -m eval.run_country_facts_eval --split dev --require-zero-severe --min-pass-rate 1.0
```

Release candidates must maintain a 100% answer rate and are compared by relative
rubric quality, prohibited errors, cold/warm latency, worker RSS, adapter size,
and prepared-topic lift. Imperfect specialists may ship when they are the best
measured option.

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
