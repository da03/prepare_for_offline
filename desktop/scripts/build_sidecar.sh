#!/usr/bin/env bash
# Build the Python backend into a single self-contained binary and place it
# where Tauri expects the sidecar (with the Rust target-triple suffix).
#
# Requires: pip install pyinstaller, and a Rust toolchain (for the triple).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/../../backend"
BIN_DIR="$HERE/../src-tauri/binaries"
mkdir -p "$BIN_DIR"

echo "==> Building backend with PyInstaller"
pushd "$BACKEND" >/dev/null
pyinstaller --noconfirm --clean --onefile --name pfo-backend \
  --collect-all llama_cpp \
  --collect-all programasweights \
  --collect-submodules app \
  run.py
popd >/dev/null

TRIPLE="$(rustc -Vv | sed -n 's/host: //p')"
echo "==> Target triple: $TRIPLE"
cp "$BACKEND/dist/pfo-backend" "$BIN_DIR/pfo-backend-$TRIPLE"
chmod +x "$BIN_DIR/pfo-backend-$TRIPLE"
echo "==> Placed sidecar at $BIN_DIR/pfo-backend-$TRIPLE"
