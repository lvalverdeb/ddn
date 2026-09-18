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
    lat, lon = package["lat"], package["lon"]
    scale = math.cos(math.radians(lat))
    return min(facilities, key=lambda f: (f["lat"] - lat) ** 2
               + ((f["lon"] - lon) * scale) ** 2)["id"]
