# Prepare for Offline - desktop (Tauri menu-bar app)

Wraps the React frontend and the Python backend into a single macOS app. The
backend runs as a bundled **sidecar** on an OS-assigned free loopback port; the
Rust shell reads a `runtime.json` handshake (port + per-install token) and
injects it into the webview before the UI loads.

```
Tauri shell (Rust)
  ├── spawns sidecar: pfo-backend (PyInstaller-packaged FastAPI)
  │     └── binds 127.0.0.1:<free port>, writes ~/.prepare_offline/runtime.json
  ├── reads runtime.json { port, token, api_base }
  ├── injects window.__API_BASE__ / window.__APP_TOKEN__
  └── loads frontend/dist  +  menu-bar tray icon
```

## Prerequisites

- Rust toolchain (`rustup`, `cargo`) - not required to develop the web app, only to build the desktop app.
- `npm install` in `desktop/` (installs the Tauri CLI).
- `pip install pyinstaller` for packaging the backend sidecar.

## Build steps

```bash
# 1. Build the web frontend
cd ../frontend && npm install && npm run build

# 2. Package the Python backend into a sidecar binary
cd ../desktop && bash scripts/build_sidecar.sh

# 3. Generate app icons once (from any square PNG, e.g. the PAW logo)
npm run tauri icon /path/to/logo.png

# 4. Build the macOS app / dmg
npm run build            # -> src-tauri/target/release/bundle/
```

For iteration you can run `npm run dev` (Tauri dev), which still spawns the
packaged sidecar; rebuild the sidecar after backend changes.

## Notes

- The sidecar is launched with `PREPARE_OFFLINE_DEV=0`, disabling the localhost
  token-bootstrap endpoint; the token reaches the UI only via injection.
- `llama-cpp-python` ships native libraries; if PyInstaller misses them, add the
  appropriate `--collect-binaries llama_cpp` / hook. Metal acceleration works in
  the packaged app on Apple Silicon.
- The app data (DB, packs, token, runtime.json) lives in `~/.prepare_offline`.
