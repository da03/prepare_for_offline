#!/usr/bin/env bash
# Bundle the Qwen3-0.6B interpreter so Ask works before any preparation or download.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/../../backend"
RESOURCE_DIR="$HERE/../src-tauri/resources"
TARGET="$RESOURCE_DIR/qwen3-0.6b-q6_k.gguf"
EXPECTED_SHA256="9a16ed5cacba959e63b62e2b6840c3eca2b51c3c3e51d31367ef8e4aafeae33c"

mkdir -p "$RESOURCE_DIR"
echo "==> Resolving Qwen3-0.6B interpreter"
MODEL_PATH="$(
  cd "$BACKEND"
  python3 -c 'from programasweights import cache; from app.config import get_settings; print(cache.get_base_model_path(get_settings().interpreter))'
)"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Interpreter model was not downloaded: $MODEL_PATH" >&2
  exit 1
fi

ACTUAL_SHA256="$(shasum -a 256 "$MODEL_PATH" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Interpreter checksum mismatch: $ACTUAL_SHA256" >&2
  exit 1
fi

cp "$MODEL_PATH" "$TARGET"
echo "==> Bundled interpreter at $TARGET"
ls -lh "$TARGET" | awk '{print "    size:", $5}'
