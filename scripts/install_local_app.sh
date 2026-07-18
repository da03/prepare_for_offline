#!/usr/bin/env bash
# Build and install the current Prepare for Offline app in /Applications.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HOME/.cargo/env"
TARGET="${PFO_TARGET:-$(rustc -Vv | awk '$1 == "host:" {print $2}')}"
APP="$ROOT/desktop/src-tauri/target/$TARGET/release/bundle/macos/Prepare for Offline.app"
INSTALL_PATH="/Applications/Prepare for Offline.app"

npm --prefix "$ROOT/frontend" run build
bash "$ROOT/desktop/scripts/build_sidecar.sh"
bash "$ROOT/desktop/scripts/bundle_model.sh"
bash "$ROOT/desktop/scripts/bundle_programs.sh"
npm --prefix "$ROOT/desktop" run build -- --target "$TARGET" --bundles app

codesign --verify --deep --strict "$APP"
osascript -e 'tell application "Prepare for Offline" to quit' 2>/dev/null || true
pkill -TERM -f "$INSTALL_PATH/Contents/MacOS/" 2>/dev/null || true
rm -rf "$INSTALL_PATH"
ditto "$APP" "$INSTALL_PATH"
codesign --verify --deep --strict "$INSTALL_PATH"
open -a "Prepare for Offline"

VERSION="$(
  plutil -extract CFBundleShortVersionString raw \
    "$INSTALL_PATH/Contents/Info.plist"
)"
echo "Installed Prepare for Offline $VERSION"
