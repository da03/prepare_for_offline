"""Runtime configuration, application data directory, and per-install app token.

Security posture (Phase 1, kept intentionally simple but real):
- The server binds only to 127.0.0.1.
- A random per-install token is generated once and stored in the data dir.
- Every /api request (except health and the dev token bootstrap) must present
  the token via the X-App-Token header.
"""

from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path


def _default_home() -> Path:
    override = os.environ.get("PREPARE_OFFLINE_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".prepare_offline"


class Settings:
    def __init__(self) -> None:
        self.home: Path = _default_home()
        self.packs_dir: Path = self.home / "packs"
        self.db_path: Path = self.home / "prepare_offline.db"
        self.token_path: Path = self.home / "app_token"

        # Host is fixed to loopback. Port 0 asks the OS for a free port
        # (used by the packaged app); dev defaults to a stable port.
        self.host: str = os.environ.get("PREPARE_OFFLINE_HOST", "127.0.0.1")
        self.port: int = int(os.environ.get("PREPARE_OFFLINE_PORT", "8765"))

        # Dev mode enables a localhost-only endpoint that reveals the token so
        # the Vite dev frontend can bootstrap. Disabled in the packaged app,
        # where the token is injected into index.html instead.
        self.dev_mode: bool = os.environ.get("PREPARE_OFFLINE_DEV", "1") == "1"

        # Interpreter is Qwen3-0.6B only (no 1.7B interpreter exists).
        self.interpreter: str = os.environ.get(
            "PREPARE_OFFLINE_INTERPRETER", "Qwen/Qwen3-0.6B"
        )

        # Compilers per programasweights AGENTS.md: iterate with the fast one,
        # finalize locked specs with the finetuned one.
        self.compiler_fast: str = "paw-4b-qwen3-0.6b"
        self.compiler_final: str = "paw-ft-bs48"

        self._ensure_dirs()
        self.app_token: str = self._load_or_create_token()

    def _ensure_dirs(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.packs_dir.mkdir(parents=True, exist_ok=True)

    def _load_or_create_token(self) -> str:
        if self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        self.token_path.write_text(token, encoding="utf-8")
        try:
            self.token_path.chmod(0o600)
        except OSError:
            pass
        return token

    @property
    def runtime_path(self) -> Path:
        return self.home / "runtime.json"

    def write_runtime(self, port: int) -> None:
        """Write the port + token handshake the desktop shell reads to connect."""
        import json

        payload = {
            "port": port,
            "token": self.app_token,
            # WKWebView's App Transport Security supports an explicit
            # localhost exception; keep the server itself bound to 127.0.0.1.
            "api_base": f"http://localhost:{port}",
        }
        self.runtime_path.write_text(json.dumps(payload), encoding="utf-8")
        try:
            self.runtime_path.chmod(0o600)
        except OSError:
            pass


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
