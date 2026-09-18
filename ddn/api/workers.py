"""§13.3's two scheduled processes.

§13.3 is precise about the boundary: they "call the same internal functions as
the endpoints; they do not call the API over HTTP". So neither function below
builds a request or knows a URL. They take the state, call `ddn.pickups` and
`ddn.simulation` directly, and write what they produced where the read
endpoints look for it.

That is not a style preference. A scheduler that went through HTTP would be a
second client of its own service -- retrying, timing out and authenticating
against itself -- to reach code it is already linked against.
"""

from __future__ import annotations

from typing import Any

from arq import ArqRedis

from ddn import assumptions, pickups
from ddn.api import jobs
from ddn.api.inputs import pickup_inputs
from ddn.api.store import Store

#: §13.3: "every [TBD, default 30] minutes during the collection window".
#: `docs/assumptions.md` carries the 30 as §5.1.5's own illustration.
CADENCE_SECONDS = assumptions.REOPT_CADENCE_MIN * 60


def reoptimise_pickups(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """§13.3's first process: re-plan the vans and publish what they should do.

    §5.1.5 is the rule it implements -- re-optimise on a fixed cadence, with
    stops already visited frozen -- and `ddn.pickups.dispatch` implements that.
    This only decides *when*, and where the answer is published.
    """
    dispatch = pickups.run(**pickup_inputs(payload))
    plan = {
        "cycle": payload.get("cycle"),
        "routes": {van: list(route) for van, route in dispatch.routes.items()},
        "returned_at": dict(dispatch.returned_at),
        "unplaced": list(dispatch.unplaced),
        "re_requests": list(dispatch.re_requests),
        "flagged": list(dispatch.flagged),
        "visits": len(dispatch.visits),
    }
    store.pickup_plan = plan
    return plan


async def nightly_cycle(store: Store, pool: ArqRedis,
                        payload: dict[str, Any]) -> dict[str, str]:
    """§13.3's second: allocation, then line-haul, then tomorrow's routes.

    Enqueued rather than run inline. §13.1's reasoning does not stop applying
    because the caller is a timer: solver work belongs on the queue, where a
    failure is retried and visible, and the nightly run is the one nobody is
    watching when it fails.

    The order is §5's own and the dependency is real -- §4.2 decides what each
    facility has to route with, and §5.3 decides what will be there by morning
    to route.
    """
    allocation = await jobs.enqueue(
        pool, "run_allocation", day=payload["day"], pools=payload["pools"],
        bikes=payload["bikes"], previous=payload.get("previous"))
    linehaul = await jobs.enqueue(pool, "run_linehaul",
                                  payload=payload["linehaul"])
    routes = {
        facility["id"]: await jobs.enqueue(
            pool, "run_routes", payload=dict(payload["routes"], facility=facility))
        for facility in payload.get("facilities", [])
    }
    store.runs.setdefault("nightly", {})[payload["day"]] = {
        "allocation": allocation, "linehaul": linehaul, "routes": routes}
    return {"allocation": allocation, "linehaul": linehaul, **routes}
