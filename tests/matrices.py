"""Travel for tests: **real road distances, replayed from a recording.**

`tests/fixtures/road.json.gz` holds a table built once by
`vrp.matrix.build_large_matrix` against the platform's gateway — the same call
`simulation/on_road.py` makes in production — over the Costa Rica extract.
`road_matrix` builds a `TravelMatrix` for any subset of those points, so the
suite routes on real roads while still needing no gateway, no graph and no
network.

Recorded through the platform rather than from OSRM's `/table` endpoint so that
the fixture and production come from the same code path: snapping with a stated
threshold and reported distances, tiling rather than a raised table limit, and
the platform's own matrix version.

This replaced a `fake_matrix` that computed straight lines. Straight-line
travel is not a neutral approximation, it is a systematic understatement: hub
to D1 is 11,551 m by road against 8,235 m as the crow flies, a factor of 1.40.
Every figure derived from the short version -- route durations, van-hours,
§8.3's checks -- came out optimistic, in a repository whose main risk is a
number that looks like a measurement and is not.

**The network is real; the places are not.** The coordinates are the fixture's
own, jittered around the placeholder towns in `docs/assumptions.md`. So these
distances have the shape of Costa Rican roads and say nothing about the
operation's geography, which §3.1 still does not supply.

And the jitter puts some of them where no road is: 39 of the 94 snapped beyond
the gateway's 100 m threshold, the furthest by 7.7 km. Those rows are travel
between the roads *nearest* the coordinates. `provenance()` returns the
recorded figures, and `test_matrices.py` asserts they are still what this
paragraph claims.

To add a coordinate, put it in `tests/fixtures/record_road.py` and re-record
against a live OSRM. A point that is missing fails loudly rather than falling
back to arithmetic.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from vrp.model import TravelMatrix

ROAD = Path(__file__).resolve().parent / "fixtures" / "road.json.gz"
PRECISION = 6


@lru_cache(maxsize=1)
def recording() -> dict[str, Any]:
    """The recorded road table, read once."""
    with gzip.open(ROAD, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def provenance() -> dict[str, Any]:
    """How the table was built, and how far its points had to move."""
    table = recording()
    return {key: table[key] for key in
            ("source", "matrix_version", "snap_threshold_m",
             "snapped_beyond_threshold", "furthest_snap_m")}


def _key(lat: float, lon: float) -> str:
    return f"{round(float(lat), PRECISION)},{round(float(lon), PRECISION)}"


def road_matrix(points: Sequence[Mapping[str, Any]]) -> TravelMatrix:
    """Real road travel over `points`, in their order.

    Raises:
        KeyError: for a coordinate that was never recorded, naming it and how
            to add it. Falling back to a computed distance is what this module
            exists to stop.
    """
    table = recording()
    index = table["index"]

    try:
        rows_wanted = [index[_key(p["lat"], p["lon"])] for p in points]
    except KeyError as missing:
        raise KeyError(
            f"no recorded road travel for {missing.args[0]}. Add the "
            "coordinate to tests/fixtures/record_road.py and re-record against "
            "a live OSRM; this module will not compute a distance instead."
        ) from None

    durations = table["durations"]
    distances = table["distances"]
    return TravelMatrix(
        version=f"road:{table['source']}",
        durations=tuple(tuple(durations[a][b] for b in rows_wanted)
                        for a in rows_wanted),
        distances=tuple(tuple(distances[a][b] for b in rows_wanted)
                        for a in rows_wanted))


def flat_matrix(size: int, *, seconds: int = 180,
                metres: int = 700) -> TravelMatrix:
    """Every pair identical. Not geography at all, and not pretending to be.

    For tests about structure -- does a route start at its facility, is a bullet
    reported -- where the distance is irrelevant and a uniform constant is
    clearer than real travel that happens to vary.
    """
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
