from __future__ import annotations

import pytest


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PREPARE_OFFLINE_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("PREPARE_OFFLINE_DEV", "1")
    monkeypatch.setenv("PREPARE_OFFLINE_SKIP_MODEL_DOWNLOAD", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path / "data"
    get_settings.cache_clear()


@pytest.fixture
def client(isolated_home):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        token = test_client.get("/api/dev/token").json()["token"]
        test_client.headers.update({"X-App-Token": token})
        yield test_client
