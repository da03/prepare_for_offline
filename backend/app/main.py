"""FastAPI application factory for the Prepare-for-Offline local server."""

from __future__ import annotations

import shutil
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import get_settings
from .db import init_db
from .routes import (
    ask,
    conversations,
    health,
    neural,
)
from .security import ALLOWED_ORIGINS


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    from .db import connect
    from .services import (
        bundled_runtime,
        neural_jobs,
        program_registry,
    )

    bundled_runtime.install()
    conn = connect()
    try:
        program_registry.ensure_builtins(conn)
        neural_jobs.recover_startup(conn)
    finally:
        conn.close()
    legacy_search_key = get_settings().home / "brave_search_api_key"
    if legacy_search_key.exists():
        legacy_search_key.unlink()
    for legacy_directory in ("knowledge", "packs"):
        shutil.rmtree(
            get_settings().home / legacy_directory,
            ignore_errors=True,
        )
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Prepare for Offline", version=__version__, lifespan=lifespan
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_origin_regex=r"chrome-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(ask.router)
    app.include_router(conversations.router)
    app.include_router(neural.router)

    return app


app = create_app()
