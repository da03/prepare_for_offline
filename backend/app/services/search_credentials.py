"""Local search-provider credential storage.

Environment variables win for managed deployments. Personal installations may
store a key in the app data directory with owner-only permissions.
"""

from __future__ import annotations

import os

from ..config import get_settings


def _path():
    return get_settings().home / "brave_search_api_key"


def get_key() -> str:
    env = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if env:
        return env
    path = _path()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def set_key(value: str) -> None:
    key = value.strip()
    if not key:
        raise ValueError("Search API key cannot be empty")
    path = _path()
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def delete_key() -> None:
    path = _path()
    if path.exists():
        path.unlink()


def status() -> dict:
    return {
        "provider": "brave",
        "configured": bool(get_key()),
        "managed_by_environment": bool(
            os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        ),
    }
