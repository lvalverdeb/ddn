"""§3.3's facility assignment rule.

Sorting (§5.2.4) and last-mile routing (§5.4) both need to know which facility
an envelope belongs to, and §3.2 puts the decision at upload-file receipt --
before the bag arrives, and long before anything is routed. It lives here
rather than in either stage for that reason: it is a fact about the network,
not about a route.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

EARTH_RADIUS_M = 6_371_000


def metres_between(lat: float, lon: float,
                   other_lat: float, other_lon: float) -> float:
    """Straight-line metres, equirectangular.

    The same approximation `nearest_facility` has always ranked by, now in a
    unit a margin can be expressed in: §3.2 asks for zips "straddling two
    facilities' areas" to be flagged, and "straddling" needs a distance rather
    than an ordering.
    """
    scale = math.cos(math.radians((lat + other_lat) / 2))
    dy = math.radians(other_lat - lat)
    dx = math.radians(other_lon - lon) * scale
    return EARTH_RADIUS_M * math.hypot(dx, dy)


def ranked(package: dict[str, Any],
           facilities: Sequence[dict[str, Any]]) -> tuple[tuple[str, float], ...]:
    """Every facility by straight-line metres from the package, nearest first."""
    lat, lon = package["lat"], package["lon"]
    return tuple(sorted(
        ((f["id"], metres_between(lat, lon, f["lat"], f["lon"]))
         for f in facilities),
        key=lambda pair: (pair[1], pair[0])))


def nearest_facility(package: dict[str, Any],
                     facilities: Sequence[dict[str, Any]]) -> str:
    """§3.3's rule, straight-line.

    §3.3 prefers road distance "where the road network makes them differ
    materially" and accepts straight-line "as a first approximation to be
    validated against operations". Straight-line here because the alternative
    is a 50,000 x 7 road matrix built before any routing has happened, to
    decide something the facilities' geographic separation already decides.
    Worth revisiting per §3.3 if two facilities ever sit across a river.
    """
    return ranked(package, facilities)[0][0]
