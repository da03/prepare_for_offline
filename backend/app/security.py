"""App-token authentication for the local API.

Any process that can reach the loopback port is part of the threat model
(notably a future browser extension), so every /api call must present the
per-install token. Health and the dev-only token bootstrap are exempt.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from .config import get_settings

EXEMPT_PATHS = {"/api/health", "/api/dev/token"}

ALLOWED_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8765",
    "http://localhost:8765",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
]


def require_token(x_app_token: str | None = Header(default=None)) -> None:
    expected = get_settings().app_token
    if not x_app_token or not secrets.compare_digest(x_app_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-App-Token.",
        )
