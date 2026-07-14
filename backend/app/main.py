"""FastAPI application factory for the Prepare-for-Offline local server."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .db import init_db
from .routes import chat, health, ingest, packs, prepare, queue
from .security import ALLOWED_ORIGINS
from .services.packs import KOREA_PACK_ID, build_korea_pack


def create_app() -> FastAPI:
    app = FastAPI(title="Prepare for Offline", version=__version__)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_origin_regex=r"chrome-extension://.*",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(packs.router)
    app.include_router(prepare.router)
    app.include_router(queue.router)
    app.include_router(ingest.router)

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        # Ensure a baseline offline-answerable pack exists even before the user
        # runs Prepare-for-Offline (so airplane mode works out of the box).
        from .db import connect

        conn = connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM packs WHERE pack_id=?", (KOREA_PACK_ID,)
            ).fetchone()
            if row is None:
                build_korea_pack(conn)
        finally:
            conn.close()

    return app


app = create_app()
