"""Stand-in travel for tests. **Not road distance.**

Production code takes road travel from a gateway-built matrix and offers no
straight-line fallback — §3.3 assigns by road distance and §5.1 routes on it.
The suite has no gateway and no graph, by design: `make test` runs anywhere
with no infrastructure, and that promise is worth more than measuring real
geography in a unit test.

So tests build matrices here, and these functions are named to make it
impossible to mistake one for the real thing. A figure measured on a
`fake_matrix` describes the fake. `docs/capacity-finding.md` is what happens
when that distinction slips.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from vrp.model import TravelMatrix

EARTH_RADIUS_M = 6_371_000


def _metres(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    """Equirectangular, and only ever used to shape a fake."""
    scale = math.cos(math.radians((a["lat"] + b["lat"]) / 2))
    dy = math.radians(b["lat"] - a["lat"])
    dx = math.radians(b["lon"] - a["lon"]) * scale
    return EARTH_RADIUS_M * math.hypot(dx, dy)


def fake_matrix(points: Sequence[Mapping[str, Any]], *, kph: float = 40.0,
                detour: float = 1.0) -> TravelMatrix:
    """A matrix shaped like roads but made of straight lines.

    `detour` multiplies the straight-line distance: real roads run longer than
    the crow flies, typically 1.2-1.4x, so a test that wants to feel road-like
    can say so explicitly rather than pretend.
    """
    distances, durations = [], []
    for a in points:
        row_d, row_t = [], []
        for b in points:
            metres = 0.0 if a is b else _metres(a, b) * detour
            row_d.append(int(metres))
            row_t.append(int(metres / (kph * 1000 / 3600)))
        distances.append(tuple(row_d))
        durations.append(tuple(row_t))
    return TravelMatrix(version="fake", durations=tuple(durations),
                        distances=tuple(distances))


def flat_matrix(size: int, *, seconds: int = 180,
                metres: int = 700) -> TravelMatrix:
    """Every pair the same. For tests about structure rather than geography."""
    return TravelMatrix(
        version="flat",
        durations=tuple(tuple(0 if i == j else seconds for j in range(size))
                        for i in range(size)),
        distances=tuple(tuple(0 if i == j else metres for j in range(size))
                        for i in range(size)))


def rows(points: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """id -> matrix row, for whichever key each record carries."""
    return {p.get("id") or p["package_id"]: row
            for row, p in enumerate(points)}
