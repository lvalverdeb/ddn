"""Road travel, read out of a matrix.

§5.1's dispatcher asks how long a leg takes and refuses to invent an answer.
This is how a caller gives it one from road data: a lookup over a matrix the
gateway built, keyed by the coordinates the records carry.

Keyed by coordinate rather than by id because a pickup van's position *is* a
coordinate — it is wherever the last bag was — so there is no id to look up
once it has left the hub. §5.3.2's circuits are the case where there *is* an
id: a depot is a fixed place with a name, and `between` reads those pairs out
of the same matrix.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vrp.model import TravelMatrix, UnreachableArc

Travel = Callable[[float, float, float, float], int]

#: What §5.3.2's circuits ask for: seconds from one facility to another.
#: Restated here rather than imported from `linehaul`, which would put the
#: model package below a stage that depends on it. The two match structurally,
#: which is all `linehaul.plan` requires of what it is handed.
Transit = Callable[[str, str], int]

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


def between(matrix: TravelMatrix, rows: Mapping[str, int]) -> Transit:
    """Seconds between two facilities, for §5.3.2's inter-depot legs.

    §3.1 gives transit *from the hub* and nothing between depots, and the
    circuit planner declines every transfer rather than guess one. This is
    where the missing half comes from: the same gateway matrix §3.3 already
    needs to rank facilities by road distance, read by id.

    Road travel is not symmetric and the numbers say so — one-way streets and
    divided highways make the return leg a different drive. A circuit that
    timed `a -> b` by the `b -> a` figure would depart on the wrong minute, so
    the lookup is ordered and never averaged.

    Args:
        matrix: a table spanning at least every facility in `rows`.
        rows: facility id -> its row, as `tests.matrices.rows` builds it.

    Returns:
        A `transit(from_id, to_id)` for `linehaul.plan`.

    Raises (when called):
        KeyError: for a facility the matrix was not built over. Same reason as
            `over`: the nearest row is not a safe guess.
        ValueError: for a pair with no road path between them.
    """
    def transit(origin_id: str, destination_id: str) -> int:
        origin, destination = rows[origin_id], rows[destination_id]
        try:
            return int(matrix.duration(origin, destination))
        except UnreachableArc as unreachable:
            raise ValueError(
                f"no road path from {origin_id} to {destination_id}; a "
                "circuit cannot be timed over an arc that does not exist"
            ) from unreachable

    return transit
