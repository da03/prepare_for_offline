#!/usr/bin/env bash
# Bundle release-gated reusable PAW programs for first-launch offline use.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/../../backend"
RESOURCE_DIR="$HERE/../src-tauri/resources/paw-programs"
MANIFEST="$BACKEND/app/services/neural_programs.json"
PROGRAM_IDS=()
while IFS= read -r PROGRAM_ID; do
  [[ -n "$PROGRAM_ID" ]] && PROGRAM_IDS+=("$PROGRAM_ID")
done < <(
  MANIFEST="$MANIFEST" python3 -c \
    'import json, os; d=json.load(open(os.environ["MANIFEST"])); print("\n".join((d["programs"][r].get("finetuned") or d["programs"][r]["standard"])["program_id"] for r in d["shipping_roles"]))'
)
PROGRAM_CACHE="$(
  cd "$BACKEND"
  python3 -c 'from programasweights.config import get_programs_dir; print(get_programs_dir())'
)"

mkdir -p "$RESOURCE_DIR"
for EXISTING in "$RESOURCE_DIR"/*; do
  [[ "$(basename "$EXISTING")" == "README.md" ]] || rm -rf "$EXISTING"
done
for PROGRAM_ID in "${PROGRAM_IDS[@]}"; do
  echo "==> Resolving PAW program $PROGRAM_ID"
  (
    cd "$BACKEND"
    PROGRAM_ID="$PROGRAM_ID" python3 -c \
      'import os; from programasweights.client import PAWClient; PAWClient().download_paw(os.environ["PROGRAM_ID"])'
  )
  SOURCE="$PROGRAM_CACHE/$PROGRAM_ID"
  if [[ ! -d "$SOURCE" ]]; then
    echo "PAW program was not cached: $SOURCE" >&2
    exit 1
  fi
  PROGRAM_ID="$PROGRAM_ID" SOURCE="$SOURCE" MANIFEST="$MANIFEST" python3 -c '
import hashlib, json, os, pathlib
document = json.load(open(os.environ["MANIFEST"]))
selected = next(
    stage
    for stages in document["programs"].values()
    for stage in stages.values()
    if stage["program_id"] == os.environ["PROGRAM_ID"]
)
meta = json.load(open(pathlib.Path(os.environ["SOURCE"]) / "meta.json"))
assert meta["program_id"] == os.environ["PROGRAM_ID"]
assert hashlib.sha256(meta["spec"].encode()).hexdigest() == selected["spec_sha256"]
'
  rm -rf "$RESOURCE_DIR/$PROGRAM_ID"
  mkdir -p "$RESOURCE_DIR/$PROGRAM_ID"
  cp "$SOURCE/adapter.gguf" "$RESOURCE_DIR/$PROGRAM_ID/adapter.gguf"
  cp "$SOURCE/meta.json" "$RESOURCE_DIR/$PROGRAM_ID/meta.json"
  cp "$SOURCE/prompt_template.txt" "$RESOURCE_DIR/$PROGRAM_ID/prompt_template.txt"
done

echo "==> Bundled ${#PROGRAM_IDS[@]} reusable PAW program(s)"
