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


#: §13.2 spells the line-haul milestones per leg: "leg departed, leg arrived".
#: A trip is several legs, and the trip-level `departed`/`arrived` this service
#: accepted before v0.16 could not say which one moved, so §7.1's
#: dispatch-order check had to infer it.
LEG_MOVES = frozenset({"leg departed", "leg arrived"})


@router.post("/linehaul/plans/{job_id}/events", status_code=http.HTTP_200_OK,
             summary="Leg departed, leg arrived (§5.3)")
async def linehaul_event(job_id: str, payload: dict[str, Any],
                         store: StoreDep, key: KeyDep, replays: ReplaysDep):
    """§5.3's milestones, per leg. §7.1 needs the arrival to check dispatch order.

    `leg` is an offset into the plan's legs -- `Leg` (`linehaul/circuit.py:40`)
    carries no id of its own, they are positional in `Trip.legs` -- and is not
    checked against the plan here. CLAUDE.md's §13 rule is that handlers
    contain no routing logic, and resolving the plan would mean this endpoint
    answering "that job has not finished yet", which is not a question about
    an event.
    """
    event = payload.get("event")
    if event not in LEG_MOVES:
        raise HTTPException(
            status_code=http.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"§13.2 knows {', '.join(sorted(LEG_MOVES))}, not {event!r}")

    leg = payload.get("leg")
    # `isinstance(True, int)` is true and JSON's `true` arrives as `True`, so
    # the bool is excluded by hand rather than by type.
    if isinstance(leg, bool) or not isinstance(leg, int) or leg < 0:
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
        return {"plan_id": job_id, "event": event, "leg": leg}

    return await once(replays, key, http.HTTP_200_OK, produce)


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
