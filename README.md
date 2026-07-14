# Prepare for Offline

Anticipatory neural compilation for travel. While you still have connectivity,
"Prepare for Offline" builds a small, purpose-built capability environment for a
future disconnected context: fuzzy-behavior [PAW](https://programasweights.com)
experts plus a tiered local knowledge pack. Offline, it answers questions
locally, grounded in cited sources, and honestly abstains (queuing to verify
later) when the evidence is insufficient.

The motivating case: on a plane with no WiFi you heard something like
_"simida"_ and want to know what it means. Exact search misses it (the
canonical form is `-습니다` / `-seumnida`), so we combine phonetic-tolerant
retrieval with a compiled resolver.

## Architecture

- `backend/` FastAPI on `127.0.0.1` (per-install app token auth).
  - Single-author answer graph: Tier-1 answer card -> deterministic Tier-2
    fact -> Tier-3 clip sentence extraction -> one evidence-grounded answerer
    -> abstain + queue. Exactly one author per path.
  - Retrieval: SQLite FTS5 fused with character-trigram/phonetic matching
    (query-side stopword stripping + phonetic folding + curated aliases).
  - PAW experts loaded one at a time (LRU) with real RSS metering (measured
    ~1 GB per resident expert - they do NOT share an in-memory runtime).
  - Local interpreter: Qwen3-0.6B via llama.cpp, used only for multi-source
    synthesis; strong single facts are rendered deterministically.
  - Evidence-support status (high/medium/low) from observable signals, never a
    model self-report.
  - `PackPlanner` turns destination + interests + storage budget into a
    `PackPlan` (budget genuinely drives topic selection); likely-question
    synthesis precomputes answer cards and estimates coverage; resumable jobs
    with a checksum + offline-smoke-test readiness gate.
  - Privacy: only behavioral expert specs are sent to the compiler; personal
    content stays local.
- `frontend/` Vite + React + Tailwind: Ask, Prepare (live job progress),
  Packs (coverage, memory, extension pairing token), Verify.
- `extension/` Chrome MV3 side panel: token-paired "Save this page" into the
  local pack (server never fetches URLs -> no SSRF; HTML sanitized + size-capped).
- `desktop/` Tauri v2 menu-bar app: spawns the backend as a PyInstaller sidecar
  on a free port, reads a `runtime.json` handshake, injects the token into the
  webview. (Requires a Rust toolchain to build; see `desktop/README.md`.)
- `backend/eval/` evaluation harness (see below).

## Evaluation

`PREPARE_OFFLINE_HOME=/tmp/pfo_eval python -m eval.run_eval` compares answering
strategies on the labeled Korea set. Representative result (Qwen3-0.6B):

| mode | coverage | grounded acc | citation | over-abstain | phonetic |
|------|---------:|-------------:|---------:|-------------:|---------:|
| pack (this product) | 1.00 | 1.00 | 1.00 | 0.00 | 1.00 |
| rag_base (0.6B over sources) | 0.14 | 0.14 | 0.14 | 0.86 | 0.00 |
| base_only (0.6B, no retrieval) | 0.07 | 0.00 | 0.00 | 0.93 | 0.00 |

The tiered + deterministic pipeline massively outperforms trusting the tiny
model with retrieved evidence. Set `EVAL_BASELINE_GGUF=/path/to/model.gguf` to
also benchmark a stronger generic local model (e.g. Qwen3-1.7B) as a product
baseline.

## Run (dev)

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

A baseline Korea pack is seeded on first startup, so airplane-mode Q&A works
out of the box. Use the Prepare tab to (optionally) compile the phonetic
resolver expert while online.

## Data location

App data (SQLite DB, app token, packs) lives in `~/.prepare_offline`
(override with `PREPARE_OFFLINE_HOME`).

## Status

- Phase 1 (core): done - phonetic retrieval, single-author graph, support
  status, verify-later queue, airplane-mode tested.
- Phase 2 (contextual prep): done - PackPlanner + budget selection, likely-question
  synthesis, precomputed answer cards + coverage, reusable router/sufficiency
  experts (deterministic fallbacks), resumable jobs + readiness gate, privacy
  boundaries, evaluation harness.
- Phase 3 (packaging): Tauri scaffold + sidecar packaging script present;
  build requires a Rust toolchain (see `desktop/README.md`).
- Phase 4 (extensions): Chrome MV3 clipper done; hybrid/vector retrieval module
  present but eval-gated OFF (FTS+trigram already reaches 100% grounded
  accuracy on the current pack), enable with `PREPARE_OFFLINE_EMBED_GGUF`.
