"""Turning stored §9.1 records into the shapes §5's modules take.

The modules work in seconds from midnight and plain dicts, because that is what
the solver and the platform work in. The API works in ISO dates and times,
because that is what a published contract should. This is the one place the two
meet, so that no handler and no module has to know about the other's units.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from ddn import assumptions
from ddn.model.facility import metres_between
from ddn.simulation import State

HOUR = 3600


def seconds(value: Any) -> int:
    """A clock time as seconds from midnight, whatever shape it arrives in."""
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, datetime):
        return value.hour * HOUR + value.minute * 60 + value.second
    if isinstance(value, time):
        return value.hour * HOUR + value.minute * 60
    parsed = datetime.fromisoformat(str(value))
    return parsed.hour * HOUR + parsed.minute * 60 + parsed.second


def straight_line(speed_kph: float):
    """Travel at a stated speed, for a caller who has no matrix and says so.

    Not a default and never reached by accident: `runner` refuses a routing run
    without a matrix, and the only caller of this is a §5.1 pickup day, whose
    own module also refuses to invent a speed. The speed is the caller's
    number, carried through so that a figure produced with it can be traced to
    it.
    """
    def travel(lat: float, lon: float, other_lat: float, other_lon: float) -> int:
        return int(metres_between(lat, lon, other_lat, other_lon)
                   / (speed_kph * 1000 / HOUR))
    return travel


def day_inputs(payload: dict[str, Any]) -> tuple[State, dict[str, Any]]:
    """A §5.6 day's inputs, from what the API was given."""
    pools = {facility: tuple(envelopes)
             for facility, envelopes in payload.get("pools", {}).items()}
    state = State(day=date.fromisoformat(payload["day"]), pools=pools,
                  placement=payload.get("placement") or {})

    kwargs: dict[str, Any] = {
        "facilities": payload["facilities"],
        "bikes": payload.get("bikes", []),
        "vans": payload.get("vans", []),
        "requests": payload.get("requests", []),
        "inflow": payload.get("inflow", []),
        "hub_id": payload.get("hub_id", "HUB"),
    }
    if payload.get("allocation"):
        kwargs["allocation"] = payload["allocation"]
    if payload.get("requests"):
        kwargs["travel"] = straight_line(float(payload["speed_kph"]))
    return state, kwargs


def pickup_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.1's inputs for a re-optimisation cycle."""
    return {
        "requests": payload["requests"],
        "vans": payload["vans"],
        "hub": payload["hub"],
        "travel": straight_line(float(payload["speed_kph"])),
        "cut_off": seconds(payload.get("cut_off",
                                       assumptions.PROCESSING_CUTOFF)),
    }
