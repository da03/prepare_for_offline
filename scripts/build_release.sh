#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$HOME/.cargo/env"
TARGET="${PFO_TARGET:-$(rustc -Vv | sed -n 's/host: //p')}"
VERSION="$(python3 -c 'import json; print(json.load(open("'"$ROOT"'/desktop/src-tauri/tauri.conf.json"))["version"])')"

echo "==> Frontend"
if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
  npm --prefix "$ROOT/frontend" install
fi
npm --prefix "$ROOT/frontend" run build

echo "==> Python sidecar"
bash "$ROOT/desktop/scripts/build_sidecar.sh"

echo "==> Tauri app + DMG ($TARGET)"
if [[ ! -d "$ROOT/desktop/node_modules" ]]; then
  npm --prefix "$ROOT/desktop" install
fi
npm --prefix "$ROOT/desktop" run build -- --target "$TARGET"

APP="$ROOT/desktop/src-tauri/target/$TARGET/release/bundle/macos/Prepare for Offline.app"
DMG="$(python3 -c 'import glob; paths=glob.glob("'"$ROOT"'/desktop/src-tauri/target/'"$TARGET"'/release/bundle/dmg/Prepare for Offline_'"$VERSION"'_*.dmg"); print(paths[-1] if paths else "")')"
if [[ -z "$DMG" || ! -f "$DMG" ]]; then
  echo "DMG not found for $TARGET" >&2
  exit 1
fi

codesign --verify --deep --strict "$APP"
hdiutil verify "$DMG" >/dev/null
echo "Built:"
echo "  $APP"
echo "  $DMG"
