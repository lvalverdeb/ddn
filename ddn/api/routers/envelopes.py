"""§13.2 Envelopes — §5.1.1's ingest, §6's outcomes, §9.1's record."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from fastapi import status as http

from ddn.api.deps import KeyDep, ReplaysDep, StoreDep
from ddn.api.events import advance
from ddn.api.idempotency import once
from ddn.api.schemas import Envelope, EnvelopeBatch, EnvelopeEvent, Status

router = APIRouter(tags=["Envelopes"])


@router.post("/envelopes/batch", status_code=http.HTTP_202_ACCEPTED,
             summary="Upload-file ingest (§5.1.1)")
async def ingest(batch: EnvelopeBatch, store: StoreDep, key: KeyDep,
                 replays: ReplaysDep):
    """§5.1.1: the upload file usually arrives before the bag.

    Accepted rather than created: §5.2 has to reconcile, geocode, assemble and
    sort before any of these is Ready, so what the hub has taken on here is
    work, not a set of routable envelopes.
    """
    def produce() -> dict:
        for envelope in batch.envelopes:
            store.envelopes[envelope.package_id] = envelope.model_dump(mode="json")
        return {"accepted": len(batch.envelopes),
                "package_ids": [e.package_id for e in batch.envelopes]}

    return await once(replays, key, http.HTTP_202_ACCEPTED, produce)


@router.get("/envelopes/{package_id}", response_model=Envelope,
            summary="One envelope (§9.1)")
def read(package_id: str, store: StoreDep):
    envelope = store.envelopes.get(package_id)
    if envelope is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no envelope {package_id}")
    return envelope


@router.post("/envelopes/{package_id}/events",
             status_code=http.HTTP_200_OK,
             summary="Outcome or address correction (§6)")
async def append_event(package_id: str, event: EnvelopeEvent, store: StoreDep,
                 key: KeyDep,
                 replays: ReplaysDep):
    """§13.1: status is never set directly; §5.2.6 validates the move.

    §6's address correction rides on the same event: a Postponed envelope whose
    address was wrong is re-geocoded, "which may change facility" -- so new
    coordinates are accepted here and the facility decision is left to §5.2.4,
    which owns it.
    """
    envelope = store.envelopes.get(package_id)
    if envelope is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no envelope {package_id}")

    def produce() -> dict:
        moved = advance(envelope, event.event)
        if event.lat is not None and event.lon is not None:
            moved = dict(moved, lat=event.lat, lon=event.lon,
                         coord_source="geocoded_address")
        if event.outcome is not None:
            # §9.1's `previous_outcome` is §6's word, not §5.2.6's. Recording
            # the *status* there left a cancelled envelope carrying
            # "Return run", which `returns.goes_back` does not recognise — so
            # it reached §5.5 and was filtered out, destroyed rather than
            # returned.
            moved = dict(moved, previous_outcome=str(event.outcome))
        elif event.reason:
            moved = dict(moved, previous_outcome=str(Status(event.event)))
        if event.reason:
            moved = dict(moved, postponed_reason=event.reason)
        store.envelopes[package_id] = moved
        store.record(actor=event.actor, action=f"event:{event.event}",
                     subject=package_id, at=event.at or datetime.now(UTC),
                     reason=event.reason)
        return {"package_id": package_id, "status": moved["status"]}

    return await once(replays, key, http.HTTP_200_OK, produce)
