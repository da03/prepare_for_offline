# Prepare for Offline

## Install on macOS

1. Download the latest DMG from
   [GitHub Releases](https://github.com/da03/prepare_for_offline/releases/latest).
2. Open the DMG and drag **Prepare for Offline** into **Applications**.
3. On the first launch, right-click the app and choose **Open** (the current
   public build is ad-hoc signed but not yet Apple-notarized).

No Python, Node, Rust, terminal, or model setup is required. The app downloads
the shared Qwen3-0.6B interpreter and prepared PAW programs when you prepare the
first trip, then runs locally.

### Use

1. Open **Prepare** and enter something like `I'm going to ICML 2026 in Seoul`.
2. Optionally attach an itinerary, schedule, PDF, DOCX, or notes.
3. Wait for **Ready Offline**. The fast program becomes usable first; accuracy
   may improve quietly in the background.
4. Open **Ask** and use it with or without a connection.

Current public DMGs target Apple Silicon. Intel users can build natively using
`bash scripts/build_release.sh`; automated Intel release artifacts are also
produced by the release workflow.

Anticipatory neural compilation for travel. Describe a trip in one sentence
while online; Prepare for Offline discovers current public sources, combines
them with private local attachments, and compiles a compact
[ProgramAsWeights](https://programasweights.com) travel assistant. Ask remains
fully local, cited, freshness-aware, and able to abstain.

The motivating case: on a plane with no WiFi you heard something like
_"simida"_ and want to know what it means. Exact search misses it (the
canonical form is `-습니다` / `-seumnida`), so we combine phonetic-tolerant
retrieval with a compiled resolver.

## Product experience

The application has exactly two primary pages:

- **Ask** — routed, progressively refined offline answers with bounded
  follow-ups, citations, support, freshness, and an expandable semantic build
  summary.
- **Prepare** — enter `I'm going to ICML 2026 in Seoul`, optionally attach an
  itinerary or schedule, review one compact trip brief, and reach `Ready
  Offline`.

History and minimal settings are secondary drawers. Compiler names, program
IDs, raw trees, topics, and expert controls are not part of the normal product.

## Architecture

- `backend/` FastAPI on `127.0.0.1` (per-install app token auth).
  - Config-driven program tree: exact answer cards -> finetuned PAW top-k router
    -> parallel cheap itinerary/event/language/local retrieval -> grounded
    main/augment aggregation -> one stable answer.
  - Retrieval: SQLite FTS5 fused with character-trigram/phonetic matching
    (query-side stopword stripping + phonetic folding + curated aliases).
  - PAW experts loaded one at a time (LRU) with real RSS metering (measured
    ~1 GB per resident expert - they do NOT share an in-memory runtime).
  - Local interpreter: Qwen3-0.6B via llama.cpp, used only for multi-source
    synthesis; strong single facts are rendered deterministically.
  - Evidence-support status (high/medium/low) from observable signals, never a
    model self-report.
  - Versioned SQLite migrations persist editable contexts, sources, settings,
    conversations/messages, pack versions, jobs, and linked verification items.
  - Official-first Prepare-time web acquisition (Brave when configured),
    original-page fetching, SSRF protection, privacy-safe public queries,
    licensing metadata, and per-source freshness TTLs.
  - `PackPlanner` creates semantic travel coverage and 100–150 likely questions;
    cancellable jobs compile a fast trip program, mark the trip ready, then
    background-finetune and atomically promote only on non-regression.
  - Privacy: only behavioral expert specs are sent to the compiler; personal
    content stays local.
- `frontend/` Vite + React: only Ask and Prepare, with History/Settings drawers.
- `extension/` Chrome MV3 side panel: token-paired "Save this page" into the
  local pack (server never fetches URLs -> no SSRF; HTML sanitized + size-capped).
- `desktop/` Tauri v2 menu-bar app: spawns the backend as a PyInstaller sidecar
  on a free port, reads a `runtime.json` handshake, injects the token into the
  webview. (Requires a Rust toolchain to build; see `desktop/README.md`.)
- `backend/eval/` evaluation harness (see below).

## Evaluation

`python -m eval.run_travel_eval` evaluates the release travel tree. The current
synthetic grounded set reports 1.0 top-1/top-k route recall, grounded accuracy,
citation correctness, and one-sentence parse completion; median first answer is
sub-millisecond from prepared cards and median final refinement is about 50 ms.

The original component comparison remains available through
`PREPARE_OFFLINE_HOME=/tmp/pfo_eval python -m eval.run_eval`:

| mode | coverage | grounded acc | citation | over-abstain | phonetic |
|------|---------:|-------------:|---------:|-------------:|---------:|
| pack (this product) | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 |
| rag_base (0.6B over sources) | 0.14 | 0.14 | 0.14 | 0.86 | 0.00 |
| base_only (0.6B, no retrieval) | 0.07 | 0.00 | 0.00 | 0.93 | 0.00 |

The tiered + deterministic pipeline massively outperforms trusting the tiny
model with retrieved evidence. Set `EVAL_BASELINE_GGUF=/path/to/model.gguf` to
also benchmark a stronger generic local model (e.g. Qwen3-1.7B) as a product
baseline.

## Run

### macOS app

The Apple Silicon release is built at:

```text
desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/macos/Prepare for Offline.app
desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/dmg/Prepare for Offline_0.3.0_aarch64.dmg
```

Open Prepare, describe the trip, optionally attach files, and keep the app open
until it reports `Ready Offline`. The fast program is usable immediately; a
tested finetuned version may replace it quietly in the background. Then use Ask
without a connection. History and Settings remain secondary controls.

The local build is ad-hoc signed but not Apple-notarized. A public distribution
must be signed with a Developer ID certificate and notarized.

### Development

Backend:

```bash
cd backend
pip install -e .
python run.py            # http://127.0.0.1:8765
```

Frontend (separate terminal):

```bash
cd frontend
npm install
npm run dev              # http://127.0.0.1:5173  (proxies /api -> 8765)
```

Optional current public-source discovery uses Brave:

```bash
export BRAVE_SEARCH_API_KEY=...
```

Without a key, preparation continues from the trip brief and attachments and
shows the missing public-source coverage instead of failing.
Personal installations can also add or remove the key under
**Settings → Advanced**; it is stored locally with owner-only permissions.

New installations start without a hidden destination. Public search is
official-first and optional; without a configured provider the trip still
prepares from its brief and attachments while showing the coverage gap.

## Data location

App data (SQLite DB, app token, packs) lives in `~/.prepare_offline`
(override with `PREPARE_OFFLINE_HOME`).

## Tests

```bash
cd backend
python -m pytest -q
python -m ruff check app tests
python -m eval.run_travel_eval
python -m eval.run_compiler_eval

cd ../frontend
npm test
npm run build
```

The Chrome MV3 companion in `extension/` saves the current page to a chosen
context. Hybrid/vector retrieval exists but remains eval-gated off; enable it
only when a larger corpus demonstrates a measurable need.
