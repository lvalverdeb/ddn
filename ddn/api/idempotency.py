"""§13.1's replay records, in the queue's Redis.

They were a dict on the `Store`, which made the guarantee weaker than it
sounded. A restart dropped every record, so a driver app retrying across a
deploy acted twice. And two API containers each kept their own dict, so a retry
that landed on the other one acted twice as well -- with no restart involved,
on a perfectly healthy day.

Redis is already here for §13.1's queue and already `appendonly`, so this adds
no service and no dependency. It also makes the record shared, which is the
half that matters: idempotency that is per-process is not idempotency, it is a
cache.

**The key is claimed before the work runs.** A retry after a timeout is often
*concurrent* with the original -- the first call was slow, not dead -- so
checking "have I seen this key?" and then acting is a race that loses exactly
when it is most needed. `SET NX` claims first, and the second caller is told
the first is still in flight rather than quietly doing it again.

A claim is released if the work raises, because a request that failed has not
happened and must be retryable. Only a completed response is kept.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

#: How long a key is remembered. Long enough to cover a driver app retrying
#: across a deploy or a phone that was in a tunnel; short enough that keys do
#: not accumulate for ever. Not a §9 value -- the spec says nothing about it.
TTL_SECONDS = "DDN_IDEMPOTENCY_TTL_SECONDS"
DEFAULT_TTL = 24 * 3600

PREFIX = "ddn:idempotency:"
#: Written on claim and replaced by the response. Distinguishable from any
#: stored body, which is always a JSON object.
IN_FLIGHT = "in-flight"


def ttl_seconds() -> int:
    return int(os.environ.get(TTL_SECONDS, DEFAULT_TTL))


@dataclass(frozen=True, slots=True)
class Replay:
    """A response already given, to be given again rather than acted on twice."""

    status_code: int
    body: dict[str, Any]


class InFlight(Exception):
    """This key is claimed and the first call has not finished."""


class Replays:
    """§13.1's replay records, keyed by `Idempotency-Key`."""

    def __init__(self, redis: Redis, ttl: int | None = None) -> None:
        self._redis = redis
        self._ttl = ttl if ttl is not None else ttl_seconds()

    @staticmethod
    def _key(key: str) -> str:
        return f"{PREFIX}{key}"

    async def claim(self, key: str) -> Replay | None:
        """Claim the key, or say what the first call already answered.

        Returns `None` when the claim succeeded and the caller should do the
        work. Returns a `Replay` when the work is already done.

        Raises:
            InFlight: the key is claimed and no response is stored yet.
        """
        claimed = await self._redis.set(self._key(key), IN_FLIGHT, nx=True,
                                        ex=self._ttl)
        if claimed:
            return None

        stored = await self._redis.get(self._key(key))
        if stored is None:
            # It expired between the SET and the GET. Rare, and the honest
            # answer is to let the caller try again rather than guess.
            raise InFlight
        text = stored.decode() if isinstance(stored, bytes) else stored
        if text == IN_FLIGHT:
            raise InFlight
        record = json.loads(text)
        return Replay(status_code=record["status_code"], body=record["body"])

    async def complete(self, key: str, *, status_code: int,
                       body: dict[str, Any]) -> None:
        """Keep the response, so a retry is answered with this one."""
        await self._redis.set(
            self._key(key),
            json.dumps({"status_code": status_code, "body": body}),
            ex=self._ttl)

    async def release(self, key: str) -> None:
        """Drop the claim. A request that raised has not happened."""
        await self._redis.delete(self._key(key))


async def once(replays: Replays, key: str, status_code: int,
               produce: Callable[[], dict[str, Any]]):
    """Do it once, or hand back what was handed back the first time.

    `produce` runs only when this key is new, so it is free to have effects --
    writing envelopes, recording audit, enqueuing a job -- and a retry has
    none of them.
    """
    from fastapi import HTTPException
    from fastapi import status as http
    from fastapi.responses import JSONResponse

    try:
        seen = await replays.claim(key)
    except InFlight:
        raise HTTPException(
            status_code=http.HTTP_409_CONFLICT,
            detail={
                "title": "request in flight",
                "detail": ("§13.1: a call with this idempotency key is still "
                           "being processed. Retry; the answer will be the "
                           "first call's."),
                "status": http.HTTP_409_CONFLICT,
            }) from None

    if seen is not None:
        return JSONResponse(status_code=seen.status_code, content=seen.body,
                            headers={"Idempotent-Replay": "true"})

    try:
        body = produce()
    except BaseException:
        await replays.release(key)
        raise

    await replays.complete(key, status_code=status_code, body=body)
    return JSONResponse(status_code=status_code, content=body)
