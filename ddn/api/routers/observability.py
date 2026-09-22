"""§13.2's last three rows: simulation, metrics, health."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from fastapi import status as http

from ddn.api import jobs
from ddn.api.deps import PoolDep, StoreDep
from ddn.api.schemas import JobAccepted, JobState

router = APIRouter(tags=["Observability"])


@router.post("/simulation/days", response_model=JobAccepted,
             status_code=http.HTTP_202_ACCEPTED,
             summary="Run a day, or several (§5.6, §10)")
async def start_simulation(payload: dict[str, Any], pool: PoolDep):
    """§5.6's cycle. A job like any other solver work (§13.1)."""
    job_id = await jobs.enqueue(pool, "run_simulation", payload=payload)
    return JobAccepted(job_id=job_id, status="queued",
                       poll=f"/simulation/days/{job_id}")


@router.get("/simulation/days/{job_id}", response_model=JobState)
async def simulation_state(job_id: str, pool: PoolDep):
    return await jobs.state(pool, job_id)


@router.get("/metrics", summary="§11's rows for a day")
def metrics(store: StoreDep,
            day: Annotated[date | None, Query(description="the day to report")] = None):
    """§11, and no targets.

    §11 leaves all twelve targets `[TBD]` and `docs/assumptions.md` explains
    why none is invented: a target is a commitment, and putting one here would
    publish a promise the customer never made.
    """
    runs = [run for run in store.runs.values()
            if day is None or run.get("day") == day.isoformat()]
    return {"day": day.isoformat() if day else None,
            "runs": len(runs),
            "targets": None,
            "note": "§11's targets are [TBD]; none is invented here"}


@router.get("/health", summary="Liveness")
def health():
    return {"status": "ok"}


@router.get("/solver/capabilities", summary="Open Question 5, answered")
def capabilities():
    """`docs/solver-capabilities.md`, as the API's answer to §12 Q5.

    Measured, not declared: the five answers come from running each capability
    against the pinned platform, and the one `no` settles §4.2 for two-stage.
    """
    return {
        "dynamic_stop_insertion": True,
        "flexible_vehicle_to_depot_assignment": False,
        "native_due_date_handling": True,
        "locked_assignments": True,
        "ready_time_constraints": True,
        "source": "docs/solver-capabilities.md",
        "measured_against": "vrp-platform v0.4.0",
    }
