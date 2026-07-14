#!/usr/bin/env bash
# Build the Python backend into a single self-contained binary and place it
# where Tauri expects the sidecar (with the Rust target-triple suffix).
#
# The app's runtime import path is thin (fastapi + programasweights' llama.cpp
# runtime + httpx). We deliberately do NOT --collect-all programasweights,
# because that drags in optional research submodules (torch, transformers,
# onnxruntime, spacy, ...) that the offline app never uses. We bundle only the
# native llama.cpp libraries and exclude the heavy stack.
#
# Requires: pip install pyinstaller, and a Rust toolchain (for the triple).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/../../backend"
BIN_DIR="$HERE/../src-tauri/binaries"
mkdir -p "$BIN_DIR"

echo "==> Building backend with PyInstaller (lean)"
pushd "$BACKEND" >/dev/null
rm -rf build dist
pyinstaller --noconfirm --clean --onefile --name pfo-backend \
  --collect-all llama_cpp \
  --collect-submodules app \
  --hidden-import programasweights \
  --hidden-import programasweights.runtime_llamacpp \
  --hidden-import programasweights.cache \
  --hidden-import programasweights.client \
  --hidden-import programasweights.config \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  --exclude-module torch \
  --exclude-module transformers \
  --exclude-module scipy \
  --exclude-module sklearn \
  --exclude-module spacy \
  --exclude-module thinc \
  --exclude-module nltk \
  --exclude-module matplotlib \
  --exclude-module pandas \
  --exclude-module onnxruntime \
  --exclude-module datasets \
  --exclude-module sympy \
  --exclude-module boto3 \
  --exclude-module botocore \
  --exclude-module tensorflow \
  --exclude-module PIL \
  --exclude-module lxml \
  --exclude-module IPython \
  --exclude-module pytest \
  run.py
popd >/dev/null

TRIPLE="$(rustc -Vv | sed -n 's/host: //p')"
echo "==> Target triple: $TRIPLE"
cp "$BACKEND/dist/pfo-backend" "$BIN_DIR/pfo-backend-$TRIPLE"
chmod +x "$BIN_DIR/pfo-backend-$TRIPLE"
echo "==> Placed sidecar at $BIN_DIR/pfo-backend-$TRIPLE"
ls -lh "$BIN_DIR/pfo-backend-$TRIPLE" | awk '{print "    size:", $5}'
