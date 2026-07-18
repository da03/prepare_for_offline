# Prepare for Offline desktop app

Wraps the React frontend and the Python backend into a single macOS app. The
backend runs as a bundled **sidecar** on an OS-assigned free loopback port; the
Rust shell reads a `runtime.json` handshake (port + per-install token) and
injects it into the webview before the UI loads.

```
Tauri shell (Rust)
  ├── spawns sidecar: pfo-backend (PyInstaller-packaged FastAPI)
  │     └── binds 127.0.0.1:<free port>, writes ~/.prepare_offline/runtime.json
  ├── passes bundled Qwen3-0.6B GGUF path to the sidecar
  ├── reads runtime.json { port, token, api_base }
  ├── injects window.__API_BASE__ / window.__APP_TOKEN__
  └── loads frontend/dist  +  menu-bar tray icon
```

## Prerequisites

- Rust toolchain (`rustup`, `cargo`) - not required to develop the web app, only to build the desktop app.
- `npm install` in `desktop/` (installs the Tauri CLI).
- `pip install pyinstaller` for packaging the backend sidecar.
- Backend dependencies installed from the ProgramAsWeights package index; the
  release script downloads and checksum-verifies the 594 MB interpreter.

## Build steps

From the repository root, the complete validated build is:

```bash
bash scripts/build_release.sh
```

Or run the individual steps below.

```bash
# 1. Build the web frontend
npm --prefix frontend install
npm --prefix frontend run build

# 2. Package the Python backend into a sidecar binary
bash desktop/scripts/build_sidecar.sh

# 3. Bundle the pinned Qwen3-0.6B interpreter
bash desktop/scripts/bundle_model.sh

# 4. Bundle release-gated reusable PAW programs
bash desktop/scripts/bundle_programs.sh

# 5. Regenerate app icons from the checked-in brand master
npm --prefix desktop run tauri -- icon brand/paw-app-icon-1024.png

# 6. Build the macOS app / dmg
npm --prefix desktop run build -- --target aarch64-apple-darwin
# -> desktop/src-tauri/target/aarch64-apple-darwin/release/bundle/
```

For iteration you can run `npm run dev` (Tauri dev), which still spawns the
packaged sidecar; rebuild the sidecar after backend changes.

## Icon sources and pre-ship check

- `../brand/paw-app-icon.svg` is the editable app-icon master;
  `../brand/paw-app-icon-1024.png` is the checked-in input to `tauri icon`.
- `../brand/paw-tray-template.svg` is the separate monochrome tray master.
  Its optically wider PAW Factory geometry keeps the toe and pad gaps visible
  at menu-bar size. Keep its generated 32 px and 64 px PNGs black with
  transparency so macOS can recolor them as template images.
- Before shipping, inspect the 16/32 px app icons against both light and dark
  backgrounds, then verify Finder, Spotlight, Dock, and the menu bar at their
  smallest normal sizes in both macOS appearances. The paw must remain distinct,
  with no clipped edges or colored tray pixels.

## Notes

- The sidecar is launched with `PREPARE_OFFLINE_DEV=0`, disabling the localhost
  token-bootstrap endpoint; the token reaches the UI only via injection.
- `llama-cpp-python` ships native libraries; if PyInstaller misses them, add the
  appropriate `--collect-binaries llama_cpp` / hook. Metal acceleration works in
  the packaged app on Apple Silicon.
- The app data (DB, packs, token, runtime.json) lives in `~/.prepare_offline`.
- `resources/qwen3-0.6b-q6_k.gguf` is generated and Git-ignored. Its pinned
  checksum is enforced by `scripts/bundle_model.sh`.
