"""What every handler needs, and the one rule they all share.

§13.1: "Ingest and event endpoints require an idempotency key so retried calls
from driver apps or customer systems cannot duplicate envelopes or outcomes."
A driver's phone loses signal mid-POST and retries; without this the envelope
is collected twice and the outcome recorded twice, and §11's rates are wrong
for the rest of the day.

A replay returns **the original response**, not a fresh one -- the same body and
the same status. Returning a new 201 would tell the caller it had created
something a second time, which is the confusion the key exists to prevent.
"""

from __future__ import annotations

from typing import Annotated

from arq import ArqRedis
from fastapi import Depends, Header, HTTPException, Request
from fastapi import status as http

from ddn.api.idempotency import Replays
from ddn.api.store import Store


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_pool(request: Request) -> ArqRedis:
    return request.app.state.queue


def get_replays(request: Request) -> Replays:
    """§13.1's replay records, in the same Redis as the queue."""
    return Replays(request.app.state.queue)


def idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """§13.1's required header on ingest and event endpoints."""
    if not key:
        raise HTTPException(
            status_code=http.HTTP_400_BAD_REQUEST,
            detail={
                "title": "Idempotency-Key required",
                "detail": ("§13.1 requires an idempotency key on ingest and "
                           "event endpoints so a retried call cannot duplicate "
                           "an envelope or an outcome"),
                "status": http.HTTP_400_BAD_REQUEST,
            })
    return key


StoreDep = Annotated[Store, Depends(get_store)]
PoolDep = Annotated[ArqRedis, Depends(get_pool)]
KeyDep = Annotated[str, Depends(idempotency_key)]
ReplaysDep = Annotated[Replays, Depends(get_replays)]
