"""§13.2's four job-backed resources: allocation, line-haul, routes, returns.

Every endpoint here invokes the solver or the planner, so every one returns
`202` with a job id and a place to poll. §13.1 admits no exception and this
module makes none: nothing below computes a plan, it only hands work to the
queue and reads back what the queue recorded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi import status as http

from ddn.api import jobs
from ddn.api.deps import KeyDep, PoolDep, ReplaysDep, StoreDep
from ddn.api.idempotency import once
from ddn.api.schemas import JobAccepted, JobState, Lock

router = APIRouter(tags=["Planning"])


async def _accept(pool: Any, function: str, poll: str,
                  **kwargs: Any) -> JobAccepted:
    """§13.1: hand the work to the queue, hand the caller its id."""
    job_id = await jobs.enqueue(pool, function, **kwargs)
    return JobAccepted(job_id=job_id, status="queued",
                       poll=f"{poll}/{job_id}")


# ------------------------------------------------------------------ §4.2

@router.post("/allocation/runs", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED, summary="Plan a day's fleet")
async def start_allocation(payload: dict[str, Any], pool: PoolDep):
    return await _accept(pool, "run_allocation", "/allocation/runs",
                         day=payload["day"], pools=payload["pools"],
                         bikes=payload["bikes"],
                         previous=payload.get("previous"))


@router.get("/allocation/runs/{job_id}", response_model=JobState)
async def allocation_state(job_id: str, pool: PoolDep):
    return await jobs.state(pool, job_id)


# ------------------------------------------------------------------ §5.3

@router.post("/linehaul/plans", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED, summary="Plan tonight's runs")
async def start_linehaul(payload: dict[str, Any], pool: PoolDep):
    return await _accept(pool, "run_linehaul", "/linehaul/plans",
                         payload=payload)


@router.get("/linehaul/plans/{job_id}", response_model=JobState)
async def linehaul_state(job_id: str, pool: PoolDep):
    return await jobs.state(pool, job_id)


#: §13.2 v0.16 spells the line-haul milestones per leg: "leg departed, leg
#: arrived". A trip is several legs and the trip-level pair could not say
#: which one moved, so §7.1's dispatch-order check had to infer it.
LEG_MOVES = frozenset({"leg departed", "leg arrived"})

#: The pair this service accepted before v0.16, still accepted for one release
#: so a driver app on a slower cycle than the document is not broken by a
#: document edit. Recorded under the name the caller sent rather than
#: translated into a leg: trip-level "departed" means the first leg left and
#: "arrived" the last one landed, but *which* leg that is lives in the plan,
#: and this handler holds the store, not the plan. Synthesising an index
#: nobody sent would put a number in the audit trail no caller stands behind.
TRIP_MOVES = frozenset({"departed", "arrived"})

#: The release that stops accepting `TRIP_MOVES`, compared against the app's
#: own published version -- `create_app` reads it from the spec's
#: "**Status:** Draft v…", so this window closes on its own rather than
#: waiting for someone to remember this constant. That does mean a spec
#: revision changes what the API accepts; it is the marker this repository
#: has, and `tests/test_api_behaviour.py` pins both sides of the window so the
#: change cannot be a surprise.
#:
#: **Moved from (0, 17) to (0, 18) when v0.17 landed, and this is a stopgap.**
#: The window was "one release" for a driver app on a slower cycle than the
#: document -- and the document then outran the app: three v0.17 proposals
#: queued against v0.16 at once, so the bump that closed this window was the
#: late-file exception (§5.1.1), nothing to do with line-haul vocabulary.
#: Retiring the trip-level names is a decision about a published interface and
#: is Luis's to make, not a side effect of a spec bump approved for something
#: else. The alternative -- delete `TRIP_MOVES`, the branch below and the three
#: open-side tests in `tests/test_api_behaviour.py` -- is what (0, 17)
#: promised, and is the other half of the fork.
SUNSET = (0, 18)
SUNSET_LABEL = ".".join(str(part) for part in SUNSET)


def _version(published: str) -> tuple[int, ...]:
    r"""`"0.16"` -> `(0, 16)`, compared as numbers.

    As strings `"0.9" < "0.17"` is false, which would close the window nine
    releases early. `SPEC_VERSION` is matched as `([\d.]+)`, so every
    non-empty part is digits.
    """
    return tuple(int(part) for part in published.split(".") if part)


@router.post("/linehaul/plans/{job_id}/events", status_code=http.HTTP_200_OK,
             summary="Leg departed, leg arrived (§5.3)")
async def linehaul_event(job_id: str, payload: dict[str, Any],
                         request: Request, store: StoreDep, key: KeyDep,
                         replays: ReplaysDep):
    """§5.3's milestones, per leg. §7.1 needs the arrival to check dispatch order.

    `leg` is an offset into the plan's legs -- `Leg` (`linehaul/circuit.py:40`)
    carries no id of its own, they are positional in `Trip.legs` -- and is not
    checked against the plan here. CLAUDE.md's §13 rule is that handlers
    contain no routing logic, and resolving the plan would mean this endpoint
    answering "that job has not finished yet", which is not a question about
    an event.
    """
    event = payload.get("event")
    deprecated = (event in TRIP_MOVES
                  and _version(request.app.version) < SUNSET)
    if event not in LEG_MOVES and not deprecated:
        raise HTTPException(
            status_code=http.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"§13.2 knows {', '.join(sorted(LEG_MOVES))}, not {event!r}")

    leg = payload.get("leg")
    # `isinstance(True, int)` is true and JSON's `true` arrives as `True`, so
    # the bool is excluded by hand rather than by type.
    if event in LEG_MOVES and (isinstance(leg, bool)
                               or not isinstance(leg, int) or leg < 0):
        raise HTTPException(
            status_code=http.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="§13.2's line-haul milestones are per leg: 'leg' is a "
                   f"non-negative offset into the plan's legs, not {leg!r}")

    def produce() -> dict:
        record = store.plans.setdefault(job_id, {"events": []})
        record["events"].append(payload)
        store.record(actor=payload.get("actor", "unknown"),
                     action=f"linehaul:{event}", subject=job_id,
                     at=datetime.now(UTC))
        return {"plan_id": job_id, "event": event} | (
            {} if leg is None else {"leg": leg})

    answer = await once(replays, key, http.HTTP_200_OK, produce)
    if deprecated:
        # Set on what `once` returns, not on an injected `Response`: `once`
        # hands back a `JSONResponse` on both paths, and FastAPI merges an
        # injected response's headers only when the handler returns something
        # it has to serialise itself. Injecting one here would drop the header
        # on every call, and a replay -- same key, old name -- would be the
        # second way to lose it.
        answer.headers["Deprecation"] = "true"
        # RFC 8594 types `Sunset` as an HTTP-date and this repository has no
        # date to put in one: its only release marker is the spec's draft
        # version, and inventing a calendar date would commit the operation to
        # a day nobody set. A version string in `Sunset` is malformed, which
        # is worse than absent -- a generated client that parses it throws
        # where nothing would have -- so the release is named in a `Link`.
        answer.headers["Link"] = (
            "</docs/vrp-problem-definition.md#132-resources>; rel=\"sunset\"; "
            f"title=\"§13.2 spells these per leg; the trip-level names are "
            f"removed at v{SUNSET_LABEL}\"")
    return answer


# ------------------------------------------------------------------ §5.4

@router.post("/routes/runs", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED,
             summary="Route one facility for a day")
async def start_routes(payload: dict[str, Any], pool: PoolDep, store: StoreDep):
    """§5.4. The pool is the caller's, or this facility's stored envelopes."""
    if "packages" not in payload:
        payload = dict(payload, packages=[
            e for e in store.envelopes.values()
            if e.get("facility_id") == payload.get("facility", {}).get("id")])
    accepted = await _accept(pool, "run_routes", "/routes/runs",
                             payload=payload)
    # §8.2 re-runs this facility with an override applied, and the re-run has
    # to know which facility and which day. Nothing kept them: `store.runs`
    # was written only by the nightly worker, under its own key, so every
    # lock enqueued a packages-only body and the job died on `KeyError`.
    store.runs[accepted.job_id] = payload
    return accepted


@router.get("/routes/runs/{job_id}", response_model=JobState,
            summary="A run, with its §7.1 violations")
async def routes_state(job_id: str, pool: PoolDep):
    """§13.1: "Every routing result carries its §7.1 violation list"."""
    return await jobs.state(pool, job_id)


@router.post("/routes/runs/{job_id}/locks", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED,
             summary="Force an envelope in or out of the plan and re-run (§8.2)")
async def add_lock(job_id: str, lock: Lock, request: Request, pool: PoolDep,
                   store: StoreDep):
    """§8.2: "The solver must support re-running with locked assignments."

    The lock is stored on the envelope, where §9.1 puts it, and a fresh run is
    enqueued rather than the old one edited -- a plan that changed after it was
    read is worse than a new plan that says so.
    """
    envelope = store.envelopes.get(lock.package_id)
    if envelope is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no envelope {lock.package_id}")
    # §8.2's two directions are mutually exclusive -- the schema refuses a body
    # carrying both -- so each override clears the other rather than layering
    # on it. An envelope pulled out and then pinned to a vehicle is pinned.
    store.envelopes[lock.package_id] = dict(
        envelope, locked_vehicle_id=lock.locked_vehicle_id,
        excluded_by_ops=lock.excluded_by_ops)
    store.record(actor=lock.actor,
                 action="exclude" if lock.excluded_by_ops else "lock",
                 subject=lock.package_id,
                 vehicle_id=lock.locked_vehicle_id, run_id=job_id)

    original = store.runs.get(job_id)
    if original is None:
        raise HTTPException(
            status_code=http.HTTP_404_NOT_FOUND,
            detail=f"no run {job_id} to re-run; §8.2 applies an override to a "
                   "plan, and this id names none")

    payload = dict(original)
    payload["packages"] = [store.envelopes[lock.package_id]] + [
        e for e in payload.get("packages", [])
        if e.get("package_id") != lock.package_id]
    rerun = await _accept(pool, "run_routes", "/routes/runs", payload=payload)
    # The re-run is itself a run: another lock on it must find the same
    # facility, or §8.2's second override would hit the hole the first just
    # left.
    store.runs[rerun.job_id] = payload
    return rerun


# ------------------------------------------------------------------ §5.5

@router.post("/returns/runs", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED, summary="Plan the return run")
async def start_returns(payload: dict[str, Any], pool: PoolDep):
    return await _accept(pool, "run_returns", "/returns/runs", payload=payload)


@router.get("/returns/runs/{job_id}", response_model=JobState)
async def returns_state(job_id: str, pool: PoolDep):
    return await jobs.state(pool, job_id)
