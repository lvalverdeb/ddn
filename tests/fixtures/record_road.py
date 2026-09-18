"""Record real road travel once, so the suite never computes a straight line.

Run against a live OSRM (see the module's `__main__` note). The result is
committed as `tests/fixtures/road.json.gz` and replayed by `tests.matrices`, so
`make test` still needs no gateway, no graph and no network.

**These are real road distances over synthetic coordinates.** The points are
the fixture's own — jittered around the placeholder towns in
`docs/assumptions.md` — so the *network* is real Costa Rican roads and the
*places* are not the operation's. That is a different and much smaller lie than
a straight line: it gets the shape of the road network right, which is what
made straight-line travel misleading. Between the hub and D1 the road is 1.40x
the crow-flies distance.

    osrm-routed --algorithm mld --max-table-size 1000 costa-rica-latest.osrm
    uv run python tests/fixtures/record_road.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROAD = HERE / "road.json.gz"
OSRM = "http://127.0.0.1:5099"
PRECISION = 6


def key(lat: float, lon: float) -> str:
    return f"{round(float(lat), PRECISION)},{round(float(lon), PRECISION)}"


def points() -> list[tuple[float, float]]:
    """Every coordinate the suite and the notebooks route between."""
    from tests.fixtures import peak_day

    day = peak_day.load()
    seen: dict[str, tuple[float, float]] = {}

    def add(lat: float, lon: float) -> None:
        seen.setdefault(key(lat, lon), (float(lat), float(lon)))

    # §3.1's facilities, and §5.1's bag sites for the peak day.
    for facility in day.facilities:
        add(facility.lat, facility.lon)
    for request in day.requests:
        add(request.lat, request.lon)

    # tests/test_dispatch.py's bags, and the coordinates its HUB uses.
    add(9.9333, -84.0833)
    for n in range(10):
        add(9.9333 + n / 500, -84.0833 + n / 600)

    # tests/test_processing.py and notebook 02.
    add(9.9981, -84.1197)
    add(9.9657, -84.1015)
    add(9.94, -84.08)
    for n in range(6):
        add(9.95 + n / 300, -84.06 + n / 400)

    return list(seen.values())


def record() -> dict:
    import httpx

    pts = points()
    coords = ";".join(f"{lon},{lat}" for lat, lon in pts)
    response = httpx.get(f"{OSRM}/table/v1/driving/{coords}",
                         params={"annotations": "duration,distance"},
                         timeout=600.0)
    response.raise_for_status()
    table = response.json()
    if table.get("code") != "Ok":
        raise SystemExit(f"OSRM refused: {table}")

    return {
        "source": "OSRM v26.8.0, costa-rica-latest, car profile",
        "note": ("Real road travel over synthetic coordinates. The network is "
                 "real; the places are the fixture's, not the operation's."),
        "index": {key(lat, lon): row for row, (lat, lon) in enumerate(pts)},
        "durations": [[int(v) if v is not None else -1 for v in row]
                      for row in table["durations"]],
        "distances": [[int(v) if v is not None else -1 for v in row]
                      for row in table["distances"]],
    }


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent.parent))
    data = record()
    with gzip.open(ROAD, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    size = ROAD.stat().st_size
    print(f"recorded {len(data['index'])} points -> {ROAD.name} "
          f"({size / 1024:.0f} KiB)")
