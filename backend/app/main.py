"""FastAPI application factory for the Prepare-for-Offline local server."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .db import init_db
from .routes import (
    ask,
    chat,
    command,
    contexts,
    conversations,
    health,
    ingest,
    packs,
    prepare,
    queue,
    settings,
    trips,
)
from .security import ALLOWED_ORIGINS


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    from .db import connect

    conn = connect()
    try:
        # A background finetune can be interrupted by app shutdown. Keep the
        # fast program active and let the user resume optimization later.
        conn.execute(
            "UPDATE contexts SET optimization_status='deferred' "
            "WHERE optimization_status IN ('queued','optimizing')"
        )
        conn.commit()
    finally:
        conn.close()
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
    app.include_router(chat.router)
    app.include_router(ask.router)
    app.include_router(command.router)
    app.include_router(packs.router)
    app.include_router(prepare.router)
    app.include_router(queue.router)
    app.include_router(ingest.router)
    app.include_router(contexts.router)
    app.include_router(conversations.router)
    app.include_router(settings.router)
    app.include_router(trips.router)

    return app


app = create_app()
