"""§13.2 Processing — §5.2's hub events and §5.2.5's ready counts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http

from ddn.api.deps import KeyDep, ReplaysDep, StoreDep
from ddn.api.events import advance
from ddn.api.idempotency import once
from ddn.api.schemas import ProcessingEvent, ReadyCount

router = APIRouter(tags=["Processing"])


@router.post("/processing/events", status_code=http.HTTP_200_OK,
             summary="Reconciled, discrepancy, assembled, sorted (§5.2)")
async def append_event(event: ProcessingEvent, store: StoreDep, key: KeyDep,
                 replays: ReplaysDep):
    """§13.4: the API receives these; it does not perform them.

    §5.2.1 holds a disputed envelope rather than moving it on -- "envelopes in
    dispute are held and are not routable until resolved" -- so a discrepancy
    is recorded against the envelope and the transition is not made.
    """
    envelope = store.envelopes.get(event.package_id)
    if envelope is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no envelope {event.package_id}")

    def produce() -> dict:
        if event.discrepancy:
            store.envelopes[event.package_id] = dict(
                envelope, in_dispute=True, discrepancy=event.discrepancy)
            store.record(actor=event.actor, action="processing:discrepancy",
                         subject=event.package_id,
                         at=event.at or datetime.now(UTC),
                         discrepancy=event.discrepancy)
            return {"package_id": event.package_id,
                    "status": envelope["status"], "held": True}

        moved = advance(envelope, event.event)
        store.envelopes[event.package_id] = moved
        store.record(actor=event.actor, action=f"processing:{event.event}",
                     subject=event.package_id,
                     at=event.at or datetime.now(UTC))
        return {"package_id": event.package_id, "status": moved["status"],
                "held": False}

    return await once(replays, key, http.HTTP_200_OK, produce)


@router.get("/processing/ready", response_model=ReadyCount,
            summary="Expected ready counts (§5.2.5)")
def ready(
    store: StoreDep,
    facility: Annotated[str, Query(description="§3.1 facility id")],
    by: Annotated[datetime, Query(description="the moment to count up to")],
):
    """§5.2.5: line-haul planning asks how many will be ready by a departure."""
    return ReadyCount(facility_id=facility, by=by,
                      ready=store.ready_at(facility, by))
