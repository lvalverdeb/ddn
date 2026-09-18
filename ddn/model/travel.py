"""Road travel, read out of a matrix.

§5.1's dispatcher asks how long a leg takes and refuses to invent an answer.
This is how a caller gives it one from road data: a lookup over a matrix the
gateway built, keyed by the coordinates the records carry.

Keyed by coordinate rather than by id because a pickup van's position *is* a
coordinate — it is wherever the last bag was — so there is no id to look up
once it has left the hub.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vrp.model import TravelMatrix, UnreachableArc

Travel = Callable[[float, float, float, float], int]

#: Six decimal places is ~0.1 m, finer than any address. Coordinates arrive
#: from JSON and from records built at different times, so they are matched at
#: a fixed precision rather than by float equality.
PRECISION = 6


def _key(lat: float, lon: float) -> tuple[float, float]:
    return (round(float(lat), PRECISION), round(float(lon), PRECISION))


def index_of(points: Sequence[Mapping[str, Any]]) -> dict[tuple[float, float], int]:
    """Matrix rows for a list of records, in the matrix's own order."""
    return {_key(point["lat"], point["lon"]): row
            for row, point in enumerate(points)}


def over(matrix: TravelMatrix,
         points: Mapping[tuple[float, float], int]) -> Travel:
    """A `travel` function for §5.1, reading road seconds from `matrix`.

    Raises (when called):
        KeyError: for a coordinate the matrix was not built over. A caller that
            reaches somewhere the matrix does not span has built the wrong
            matrix, and the nearest row is not a safe guess.
        ValueError: for an unreachable pair. The platform raises
            `UnreachableArc` rather than returning a number, which is the
            behaviour that stops `UNREACHABLE` (-1) becoming the cheapest arc
            in the matrix; §9.2 has no reason code for it either
            (`capacity-finding.md` §4), so it is re-raised rather than
            resolved.
    """
    def travel(lat: float, lon: float, other_lat: float, other_lon: float) -> int:
        origin, destination = points[_key(lat, lon)], points[_key(other_lat, other_lon)]
        try:
            return int(matrix.duration(origin, destination))
        except UnreachableArc as unreachable:
            raise ValueError(
                f"no road path from row {origin} to row {destination}; §9.2 has "
                "no reason code for an unreachable address") from unreachable

    return travel
