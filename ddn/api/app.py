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
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ddn.api.jobs import redis_settings
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
            app.state.queue = await create_pool(settings or redis_settings())
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

    app.openapi = _published(app)  # type: ignore[method-assign]
    return app


IDEMPOTENCY_HEADER = "Idempotency-Key"


def _published(app: FastAPI):
    """Make the document say what the service actually does.

    §13.1 calls the OpenAPI document "the published form of §9", which only
    holds if it is true. Two things were not.

    `Idempotency-Key` is refused when absent and was published as optional,
    because FastAPI reads required-ness from the dependency's default and the
    dependency needs one to produce §13.1's explanation rather than a bare 422.
    A client generated from that document would omit the header and meet a 400
    the document never mentioned.

    And the two refusals §13 asks for -- 400 for a missing key, 409 with the
    current state for a transition §5.2.6 does not draw -- appeared nowhere in
    the responses.

    Both are derived from the document itself: the header is found by looking
    for it, and the event endpoints by their path. Nothing here is a second
    list of endpoints to keep in step with the routers.
    """
    problem = {"$ref": "#/components/schemas/Problem"}

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version,
                             description=app.description, routes=app.routes)
        for path, operations in schema["paths"].items():
            for operation in operations.values():
                headers = [p for p in operation.get("parameters", [])
                           if p["in"] == "header"
                           and p["name"] == IDEMPOTENCY_HEADER]
                conflicts = []
                for header in headers:
                    header["required"] = True
                    operation["responses"]["400"] = {
                        "description": ("§13.1: the idempotency key is missing. "
                                        "A retried call without one could "
                                        "duplicate an envelope or an outcome."),
                        "content": {"application/json": {"schema": problem}}}
                    conflicts.append(
                        "a call with this idempotency key is still in flight")
                if path.endswith("/events"):
                    conflicts.append(
                        "§5.2.6 draws no such transition, and the body carries "
                        "the state the envelope is actually in")
                if conflicts:
                    operation["responses"]["409"] = {
                        "description": "; ".join(conflicts).capitalize() + ".",
                        "content": {"application/json": {"schema": problem}}}
        app.openapi_schema = schema
        return schema

    return openapi
