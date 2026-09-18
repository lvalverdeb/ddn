"""§13.2 Pickups — §5.1's mailbags, doorstep events and the current plan."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi import status as http

from ddn.api.deps import KeyDep, ReplaysDep, StoreDep
from ddn.api.idempotency import once
from ddn.api.schemas import Mailbag, PickupEvent

router = APIRouter(tags=["Pickups"])

#: §5.1.7's table, as the events a driver app can report.
COLLECTED, SEAL_BROKEN, FAILED, CANCELLED = (
    "collected", "seal_broken", "failed", "cancelled")
EVENTS = frozenset({COLLECTED, SEAL_BROKEN, FAILED, CANCELLED})


@router.post("/pickups", status_code=http.HTTP_201_CREATED,
             summary="A mailbag is ready (§5.1.1)")
async def request_pickup(bag: Mailbag, store: StoreDep, key: KeyDep,
                 replays: ReplaysDep):
    """§5.1.1: "Customers indicate one or more bags are ready"."""
    def produce() -> dict:
        store.mailbags[bag.mailbag_id] = bag.model_dump(mode="json")
        return {"mailbag_id": bag.mailbag_id, "status": "requested"}

    return await once(replays, key, http.HTTP_201_CREATED, produce)


@router.post("/pickups/{mailbag_id}/events", status_code=http.HTTP_200_OK,
             summary="Collected, seal check, failed (§5.1.7)")
async def append_event(mailbag_id: str, event: PickupEvent, store: StoreDep,
                 key: KeyDep,
                 replays: ReplaysDep):
    """§5.1.7's exceptions, as they happen at the site.

    A broken seal is **collected and flagged**, not refused: §5.1.7 has the hub
    perform full reconciliation with the customer notified, which cannot happen
    if the bag stays on the doorstep.
    """
    bag = store.mailbags.get(mailbag_id)
    if bag is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no mailbag {mailbag_id}")
    if event.event not in EVENTS:
        raise HTTPException(
            status_code=http.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"§5.1.7 knows {', '.join(sorted(EVENTS))}, not {event.event!r}")

    def produce() -> dict:
        collected = event.event in {COLLECTED, SEAL_BROKEN}
        store.mailbags[mailbag_id] = dict(
            bag, status=event.event, collected=collected,
            flagged=event.event == SEAL_BROKEN or event.seal_intact is False,
            re_request=event.event == FAILED)
        store.record(actor=event.actor, action=f"pickup:{event.event}",
                     subject=mailbag_id, at=event.at or datetime.now(UTC),
                     reason=event.reason)
        return {"mailbag_id": mailbag_id, "status": event.event,
                "collected": collected}

    return await once(replays, key, http.HTTP_200_OK, produce)


@router.get("/pickups/plan", summary="Current van routes (§5.1.5)")
def current_plan(store: StoreDep):
    """The plan the §13.3 re-optimisation worker last published.

    Empty until the worker has run: §5.1.5 re-optimises on a cadence, so
    between cycles there is a plan and before the first there is not. Saying so
    is better than inventing one on the spot, which would be a different plan
    from the one the vans are driving.
    """
    return store.pickup_plan or {"cycle": None, "routes": {}, "unplaced": []}
