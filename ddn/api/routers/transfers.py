"""§13.2 Transfers — §5.3.2's inter-depot moves, raised and tracked."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi import status as http

from ddn.api.deps import KeyDep, ReplaysDep, StoreDep
from ddn.api.idempotency import once
from ddn.api.schemas import Transfer, TransferEvent
from ddn.model import Status

router = APIRouter(tags=["Transfers"])

#: §13.2's three events, and the §5.2.6 move each one makes.
MOVES = {
    "loaded": (Status.TRANSFER_REQUESTED, Status.IN_TRANSFER),
    "arrived": (Status.IN_TRANSFER, Status.READY),
    # §5.3.2's rebalancing transfers are a cost comparison operations may
    # drop; the envelope is still Ready where it stands.
    "cancelled": (Status.TRANSFER_REQUESTED, Status.READY),
}


@router.post("/transfers", status_code=http.HTTP_201_CREATED,
             summary="Raise a transfer, with its reason (§5.3.2)")
async def raise_transfer(transfer: Transfer, store: StoreDep, key: KeyDep,
                         replays: ReplaysDep):
    """§5.3.2's triggers, as a request with a deadline.

    §7.1 forbids raising one that cannot arrive in time -- "it goes to the
    return run instead" -- and §9.1 defines `deadline` as
    min(receiving release, SLA date), so a deadline already past is refused
    here rather than planned around later.
    """
    envelope = store.envelopes.get(transfer.package_id)
    if envelope is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no envelope {transfer.package_id}")

    def produce() -> dict:
        # §7.1 forbids raising a transfer that cannot arrive in time, and the
        # only part of that this layer can judge is a deadline already behind
        # the moment it was raised. `created_at` is the caller's, not this
        # process's clock: every date in this repository is passed in, so that
        # replaying a past day answers what that day answered. Without one,
        # nothing is assumed -- `linehaul.plan` declines it against real
        # circuit times, and `check_day_constraints` reports it as a §7.1
        # violation if it is ever carried late.
        if transfer.created_at and transfer.deadline <= transfer.created_at:
            raise HTTPException(
                status_code=http.HTTP_409_CONFLICT,
                detail={
                    "title": "transfer cannot arrive in time",
                    "detail": ("§7.1: a transfer is not raised for an envelope "
                               "that cannot reach the destination before its "
                               "SLA date; it goes to the return run instead"),
                    "status": http.HTTP_409_CONFLICT,
                    "package_id": transfer.package_id,
                })
        record = transfer.model_dump(mode="json")
        record["status"] = str(Status.TRANSFER_REQUESTED)
        store.transfers[transfer.transfer_id] = record
        store.record(actor=transfer.actor, action="transfer:raised",
                     subject=transfer.package_id,
                     reason=str(transfer.reason),
                     to_facility=transfer.to_facility_id)
        return {"transfer_id": transfer.transfer_id,
                "status": record["status"]}

    return await once(replays, key, http.HTTP_201_CREATED, produce)


@router.get("/transfers", summary="Transfers, optionally by status")
def list_transfers(
    store: StoreDep,
    status: Annotated[str | None, Query(description="§5.2.6 state")] = None,
):
    wanted = list(store.transfers.values())
    if status is not None:
        wanted = [t for t in wanted if t.get("status") == status]
    return {"transfers": wanted, "count": len(wanted)}


@router.post("/transfers/{transfer_id}/events", status_code=http.HTTP_200_OK,
             summary="Loaded, arrived, cancelled (§5.3.2)")
async def append_event(transfer_id: str, event: TransferEvent,
                       store: StoreDep, key: KeyDep, replays: ReplaysDep):
    """§13.1: the §5.2.6 state machine validates, here as everywhere.

    An envelope is Ready again only once its transfer has *arrived* -- §7.1:
    "an envelope in transfer is not routable until it arrives at the
    destination depot" -- so `arrived` is what moves the envelope's facility,
    not `loaded`.
    """
    from ddn.api.events import advance, conflict

    record = store.transfers.get(transfer_id)
    if record is None:
        raise HTTPException(status_code=http.HTTP_404_NOT_FOUND,
                            detail=f"no transfer {transfer_id}")
    if event.event not in MOVES:
        raise HTTPException(
            status_code=http.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"§13.2 knows {', '.join(sorted(MOVES))}, not {event.event!r}")

    def produce() -> dict:
        source, target = MOVES[event.event]
        # §5.2.6 says which moves are legal; the event says which move it *is*.
        # Both `arrived` and `cancelled` end at Ready, so the state machine
        # alone cannot tell an arrival from a cancellation -- an `arrived` on a
        # transfer still sitting at Transfer requested would take the
        # cancellation edge and report success.
        if record["status"] != str(source):
            raise conflict(record["package_id"], Status(record["status"]),
                           target)
        moved = advance({"package_id": record["package_id"],
                         "status": record["status"]}, target)
        store.transfers[transfer_id] = dict(record, status=moved["status"])

        envelope = store.envelopes.get(record["package_id"])
        if envelope is not None:
            updated = dict(envelope, status=moved["status"])
            if event.event == "arrived":
                updated["facility_id"] = record["to_facility_id"]
            store.envelopes[record["package_id"]] = updated

        store.record(actor=event.actor, action=f"transfer:{event.event}",
                     subject=record["package_id"], transfer_id=transfer_id)
        return {"transfer_id": transfer_id, "status": moved["status"],
                "package_id": record["package_id"]}

    return await once(replays, key, http.HTTP_200_OK, produce)
