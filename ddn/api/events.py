"""§5.2.6, enforced at the edge.

§13.1: "Callers append events (collected, reconciled, assembled, sorted,
outcome recorded); the §5.2.6 state machine validates each transition. Status
is never set directly."

So this module owns one decision -- may this envelope move there from where it
is -- and it answers it by asking `ddn.model.lifecycle`, which transcribed the
diagram. There is no second table here. A state machine with two copies is two
state machines, and the API's copy would be the one that drifts.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi import status as http

from ddn.model import Status, may
from ddn.model.lifecycle import TRANSITIONS


def conflict(package_id: str, current: Status, wanted: Status) -> HTTPException:
    """§5.2.6 draws no such edge: 409, saying where the envelope actually is."""
    allowed = sorted(TRANSITIONS[current])
    return HTTPException(
        status_code=http.HTTP_409_CONFLICT,
        detail={
            "title": "illegal transition",
            "detail": (f"§5.2.6 draws no {current} -> {wanted}; from {current} "
                       f"an envelope may reach: "
                       f"{', '.join(allowed) or 'nothing (terminal)'}"),
            "status": http.HTTP_409_CONFLICT,
            "current_state": str(current),
            "package_id": package_id,
        })


def advance(envelope: dict[str, Any], wanted: Status) -> dict[str, Any]:
    """Move an envelope, or refuse with the state it is actually in.

    Returns a new record rather than mutating: an event that is refused must
    leave no trace, and a half-applied transition is the worst of both.
    """
    current = Status(envelope["status"])
    if not may(current, wanted):
        raise conflict(envelope["package_id"], current, wanted)
    return dict(envelope, status=str(wanted))
