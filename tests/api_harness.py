"""An API with a queue, and no Redis anywhere.

CLAUDE.md promises tests that run "without a gateway or routing data", and a
service layer should not be the thing that breaks that promise: anyone who
clones this repository runs the whole suite with `uv run pytest` and nothing
else. So the §13.1 queue is real Arq driving `fakeredis`.

**Real Arq, not a stand-in.** Jobs are enqueued through `enqueue_job`, executed
by an actual `arq.Worker`, and their status read back from arq's own records --
which is what `GET …/{id}` reports. A hand-written fake queue would have tested
the handlers against a mock of the thing most likely to be wrong.

One thing is stubbed: `log_redis_info`, which arq calls once on worker startup
to log the server's version and memory. `fakeredis` implements no `INFO`
command. It is a log line, not behaviour, and stubbing it is the whole of the
difference between this and a Redis.
"""

from __future__ import annotations

from typing import Any

import arq.connections
import arq.worker
from arq import ArqRedis, Worker
from fakeredis.aioredis import FakeRedis

from ddn.api import create_app
from ddn.api.jobs import FUNCTIONS
from ddn.api.store import Store


async def _no_redis_info(redis: Any, log_func: Any) -> None:
    """fakeredis has no INFO; arq only logs what it returns."""
    return


def patch_arq() -> None:
    arq.worker.log_redis_info = _no_redis_info
    arq.connections.log_redis_info = _no_redis_info


def make_pool() -> ArqRedis:
    patch_arq()
    return ArqRedis(connection_pool=FakeRedis().connection_pool)


def make_app(pool: ArqRedis, store: Store | None = None):
    return create_app(queue=pool, store=store or Store())


async def drain(pool: ArqRedis) -> None:
    """Run every queued job to completion, then stop.

    `burst=True` is what makes this a test rather than a service: the worker
    takes what is on the queue, runs it and exits, so a test can assert on the
    result instead of polling.
    """
    worker = Worker(functions=FUNCTIONS, redis_pool=pool, burst=True,
                    poll_delay=0.01, keep_result=300, max_jobs=200)
    await worker.async_run()
