"""The FastAPI application: §13.2's table, assembled.

§13 opens with what this layer is -- "a thin layer: it accepts inputs, starts
jobs, reports status and returns results" -- and this module is the thinnest
part of it. It wires routers to an app and an app to a queue, and contains no
decision about the operation at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from arq import ArqRedis
from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddn.api.routers import envelopes, observability, pickups, planning, processing
from ddn.api.store import Store

TITLE = "Document Delivery Network"
DESCRIPTION = """\
The §13 service interface for the Document Delivery Network.

Schemas are §9 of the problem definition; this document is their published
form. Solver-invoking endpoints return 202 with a job id (§13.1). Lifecycle
changes are made by appending events, which §5.2.6 validates. Ingest and event
endpoints require an `Idempotency-Key`.
"""


def create_app(*, queue: ArqRedis | None = None,
               store: Store | None = None,
               settings: RedisSettings | None = None) -> FastAPI:
    """Build the app. A queue may be supplied, so tests need no Redis."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Only what the caller did not supply. A queue given here is already
        # connected, and opening a second one on startup would leave the
        # handlers talking to a pool nobody drains.
        if getattr(app.state, "queue", None) is None:
            app.state.queue = await create_pool(settings or RedisSettings())
        yield

    app = FastAPI(title=TITLE, description=DESCRIPTION, version="0.11",
                  lifespan=lifespan)
    app.state.store = store or Store()
    app.state.queue = queue

    @app.exception_handler(StarletteHTTPException)
    async def problem(_request, exc: StarletteHTTPException) -> JSONResponse:
        """RFC 9457-shaped errors, so a 409 carries the state it refused."""
        detail = exc.detail
        body = detail if isinstance(detail, dict) else {
            "title": "error", "detail": str(detail), "status": exc.status_code}
        return JSONResponse(status_code=exc.status_code, content=body)

    for module in (envelopes, pickups, processing, planning, observability):
        app.include_router(module.router)
    return app
