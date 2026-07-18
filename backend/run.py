"""Launcher for the local API server (binds to 127.0.0.1).

Dev:      python run.py            (stable port 8765)
Packaged: PREPARE_OFFLINE_PORT=0 python run.py   (OS-assigned free port +
          runtime.json handshake, which the Tauri shell reads to connect)
"""

from __future__ import annotations

import multiprocessing
import socket

import uvicorn

from app.config import get_settings
from app.main import app as fastapi_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    settings = get_settings()
    port = settings.port if settings.port else _free_port()
    settings.write_runtime(port)
    print(f"[paw-offline] data dir: {settings.home}")
    print(f"[paw-offline] app token: {settings.app_token}")
    print(f"[paw-offline] runtime:   {settings.runtime_path}")
    print(f"[paw-offline] http://{settings.host}:{port}")
    # Pass the app object directly (not an import string) so this works both in
    # dev and inside a frozen PyInstaller binary.
    uvicorn.run(fastapi_app, host=settings.host, port=port, reload=False)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
