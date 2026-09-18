"""Turning stored §9.1 records into the shapes §5's modules take.

The modules work in seconds from midnight and plain dicts, because that is what
the solver and the platform work in. The API works in ISO dates and times,
because that is what a published contract should. This is the one place the two
meet, so that no handler and no module has to know about the other's units.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time
from typing import Any

from vrp.model import TravelMatrix

from ddn import assumptions
from ddn.model import travel as road
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


def matrix_of(payload: Mapping[str, Any], key: str = "matrix") -> TravelMatrix:
    """The road matrix a caller supplied, or a refusal.

    §3.3 and §5.1 are both road distance. This service builds no matrix and
    invents no speed: a caller that wants routing supplies travel from the
    gateway, and one that does not gets told so rather than served a straight
    line dressed as a road.
    """
    raw = payload.get(key)
    if not raw:
        raise ValueError(
            "this call needs road travel: supply `matrix` with `durations` and "
            "`distances` from the gateway. §3.3 assigns by road distance and "
            "§5.1 routes on it; neither is served by a guessed speed")
    return TravelMatrix(
        version=raw.get("version", "api"),
        durations=tuple(tuple(row) for row in raw["durations"]),
        distances=tuple(tuple(row) for row in raw["distances"]))


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
        # §5.1's legs run between the hub and the bag sites, so the matrix is
        # built over exactly those, in that order.
        hub = next(f for f in payload["facilities"]
                   if f["id"] == payload.get("hub_id", "HUB"))
        points = [hub, *payload["requests"]]
        kwargs["travel"] = road.over(matrix_of(payload), road.index_of(points))
    return state, kwargs


def pickup_inputs(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.1's inputs for a re-optimisation cycle."""
    points = [payload["hub"], *payload["requests"]]
    return {
        "requests": payload["requests"],
        "vans": payload["vans"],
        "hub": payload["hub"],
        "travel": road.over(matrix_of(payload), road.index_of(points)),
        "cut_off": seconds(payload.get("cut_off",
                                       assumptions.PROCESSING_CUTOFF)),
    }
