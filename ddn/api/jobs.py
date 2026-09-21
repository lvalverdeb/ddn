"""§13.1's job model: solver work leaves the request.

§13.1 is explicit twice over -- "any endpoint that invokes the solver returns
`202 Accepted` with a job id" and "Jobs run on a dedicated queue with retry and
visibility, **not in-process background tasks**". FastAPI's `BackgroundTasks`
is the thing being ruled out by name: it dies with the process and nobody can
ask what became of the work.

Arq because it is asyncio-native, so an async handler enqueues without a thread
bridge, and because its job records *are* the status endpoint -- `status` and
`result_info` are what `GET …/{id}` reports rather than a second table this
module would have to keep in step.

**The jobs call the same internal functions the schedulers call.** §13.3 says
the scheduled processes "call the same internal functions as the endpoints;
they do not call the API over HTTP", and the inverse holds here: a job is a
thin call into `ddn.simulation`, `ddn.allocation`, `ddn.linehaul` or
`ddn.returns`. No stage logic lives in this file either.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

from arq import ArqRedis
from arq.connections import RedisSettings
from arq.jobs import Job

QUEUE = "ddn"

#: Where the queue lives. Named like the repository's other inputs
#: (`DDN_PLATFORM_REPO`, `DDN_OSRM_GRAPH`): required things are asked for by
#: name rather than guessed. Unset falls back to a local Redis, which is what a
#: developer running `make dev` has.
REDIS_DSN = "DDN_REDIS_DSN"


def redis_settings() -> RedisSettings:
    """§13.1's queue connection, from the environment or localhost."""
    dsn = os.environ.get(REDIS_DSN)
    return RedisSettings.from_dsn(dsn) if dsn else RedisSettings()


async def run_allocation(ctx: dict[str, Any], *, day: str,
                         pools: dict[str, int], bikes: list[str],
                         previous: dict[str, str] | None = None) -> dict[str, Any]:
    """§4.2, as a job. The work is `ddn.allocation`'s; this only carries it.

    Delegates to `runner.allocation_plan` rather than repeating its three
    lines. They were duplicated, which made the runner's copy unreachable and
    left two places for §4.2 to be answered differently.
    """
    from ddn.api.runner import allocation_plan

    return allocation_plan({"day": day, "pools": pools, "bikes": bikes,
                            "previous": previous})


async def run_simulation(ctx: dict[str, Any], *, payload: dict[str, Any]
                         ) -> dict[str, Any]:
    """§5.6 and §10, as a job."""
    from ddn.api.runner import simulate

    return simulate(payload)


async def run_routes(ctx: dict[str, Any], *, payload: dict[str, Any]
                     ) -> dict[str, Any]:
    """§5.4 for one facility, with its §7.1 violation list (§13.1)."""
    from ddn.api.runner import deliver

    return deliver(payload)


async def run_linehaul(ctx: dict[str, Any], *, payload: dict[str, Any]
                       ) -> dict[str, Any]:
    """§5.3, as a job."""
    from ddn.api.runner import linehaul_plan

    return linehaul_plan(payload)


async def run_returns(ctx: dict[str, Any], *, payload: dict[str, Any]
                      ) -> dict[str, Any]:
    """§5.5, as a job."""
    from ddn.api.runner import return_run

    return return_run(payload)


#: Every function the queue will run. A worker is started with exactly these.
FUNCTIONS = [run_allocation, run_simulation, run_routes, run_linehaul,
             run_returns]


class WorkerSettings:
    """What `arq ddn.api.jobs.WorkerSettings` needs to start a worker.

    §13.1 asks for "a dedicated queue with retry and visibility", and this is
    the process that provides it: a separate container from the API, so that
    solver work cannot take a request thread with it and so that the two scale
    apart. `max_tries` is arq's own retry; a job that keeps failing ends in the
    queue's dead set where it can be looked at, rather than disappearing.
    """

    functions = FUNCTIONS
    # arq reads this at startup. Without it, arq falls back to its own default
    # -- localhost -- and a worker in a container crash-loops against nothing
    # while the API enqueues happily to the real Redis. The API never had the
    # bug because its lifespan calls `redis_settings()`; this class was written
    # in the same commit and never wired to it, and nothing noticed until a
    # container ran.
    redis_settings = redis_settings()
    max_tries = 3
    job_timeout = 900
    keep_result = 3600


async def enqueue(pool: ArqRedis, function: str, **kwargs: Any) -> str:
    """Put work on the queue and return the id §13.1 promises the caller."""
    job = await pool.enqueue_job(function, **kwargs)
    if job is None:  # pragma: no cover - arq returns None only on a dropped id
        raise RuntimeError(f"{function} was not enqueued")
    return job.job_id


async def state(pool: ArqRedis, job_id: str) -> dict[str, Any]:
    """What became of a job: arq's own record, not a copy of it."""
    job = Job(job_id, pool)
    status = await job.status()
    info = await job.result_info()
    result = info.result if info and info.success else None
    envelope = {
        "job_id": job_id,
        "status": str(getattr(status, "value", status)),
        "result": result if isinstance(result, dict) else None,
    }
    # §13.1 asks every *routing* result to carry its §7.1 list. Defaulting the
    # key to [] here handed one to §4.2's allocation too, which produces no
    # routes and has no §7.1 surface -- the empty list `runner.allocation_plan`
    # was changed to stop claiming, restored one layer up by the envelope.
    if isinstance(result, dict) and "violations" in result:
        envelope["violations"] = result["violations"]
    return envelope


def isoday(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)
