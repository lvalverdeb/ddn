"""Record real road travel once, through the gateway, so the suite never
computes a straight line.

The table is committed as `tests/fixtures/road.json.gz` and replayed by
`tests.matrices`, so `make test` needs no gateway, no graph and no network.

**Recorded the way production builds a matrix.** `vrp.matrix.build_large_matrix`
against the platform's compiled gateway — the same call `simulation/on_road.py`
makes — rather than OSRM's `/table` endpoint directly. That matters for three
things the raw endpoint does not give: coordinates are snapped to the network
with a stated threshold and the snaps are reported, requests are tiled instead
of depending on a raised `--max-table-size`, and the result carries the
platform's own matrix version rather than a hand-written description. A fixture
produced by a different path than production is a fixture that can disagree
with production for reasons nobody can see.

**The network is real; the places are not.** The coordinates are the fixture's
own, jittered around the placeholder towns in `docs/assumptions.md`, because
§3.1 does not supply the operation's geography. What is real is the shape of
the road network, which is what made straight-line travel misleading: the hub
to D1 is 11,551 m by road against 8,235 m as the crow flies.

Which is also why the snap figures are recorded. Random jitter puts some points
where no road is -- 39 of 94 land beyond the gateway's 100 m threshold, one of
them 7.7 km out -- so those rows are travel between the roads *nearest* the
coordinates. Real geography would not do that, and the fixture should not be
read as though it were.

    DDN_PLATFORM_REPO=/path/to/osrm-microservice \\
    DDN_OSRM_GRAPH=/path/to/costa-rica-latest.osrm \\
        uv run python tests/fixtures/record_road.py
"""

from __future__ import annotations

import gzip
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from vrp.matrix import (
    DEFAULT_SNAP_THRESHOLD_M,
    PairCache,
    build_large_matrix,
)

HERE = Path(__file__).resolve().parent
ROAD = HERE / "road.json.gz"
PRECISION = 6


def need(var: str, what: str) -> str:
    """The value of `var`, or exit saying what it is for. As `on_road` does."""
    value = os.environ.get(var)
    if not value:
        sys.exit(f"{var} is unset -- {what}")
    return value


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

    # tests/test_dispatch.py's hub and bags.
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait(url: str, tries: int = 240) -> bool:
    for _ in range(tries):
        try:
            httpx.get(url, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False


def record() -> dict:
    """Spawn OSRM and the gateway, build the matrix, return what to store."""
    repo = need("DDN_PLATFORM_REPO",
                "path to an osrm-microservice checkout with a built gateway")
    graph = need("DDN_OSRM_GRAPH",
                 "base path of a built OSRM graph, e.g. .../costa-rica-latest.osrm")
    gateway_bin = f"{repo}/gateway/target/debug/osrm-api-gateway"

    osrm_port, gateway_port = free_port(), free_port()
    osrm = subprocess.Popen(
        ["osrm-routed", "--algorithm", "mld", "--port", str(osrm_port), graph],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    gateway = subprocess.Popen(
        [gateway_bin],
        env={**os.environ, "OSRM_BASE_URL": f"http://127.0.0.1:{osrm_port}",
             "HOST": "127.0.0.1", "PORT": str(gateway_port),
             "VRP_MAX_STOPS": "5000",
             **{f"RATE_LIMIT_{name}": "1000000/minute" for name in
                ("ROUTE", "MATRIX", "MATCH", "TRIP", "VRP", "NEAREST",
                 "NEAREST_BATCH", "TILE")}},
        cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{gateway_port}"

    try:
        if not wait(f"http://127.0.0.1:{osrm_port}/nearest/v1/driving/-84.08,9.93"):
            sys.exit("osrm-routed did not come up")
        if not wait(f"{base}/health"):
            sys.exit("the gateway did not come up")

        pts = points()
        matrix, snaps = build_large_matrix(base, pts, cache=PairCache(),
                                           timeout=600.0)
        # The gateway reports how far each coordinate moved to reach a road.
        # Worth storing: these are synthetic points jittered around placeholder
        # towns, so some land where no road is, and a distance between snapped
        # locations is a distance between the roads nearest them -- not between
        # the addresses. The raw `/table` endpoint snaps too and says nothing.
        far = [s for s in snaps if s.distance_m > DEFAULT_SNAP_THRESHOLD_M]
        furthest = max((s.distance_m for s in snaps), default=0.0)
        print(f"{len(pts)} points, matrix version {matrix.version}")
        print(f"{len(far)} of {len(snaps)} snapped beyond "
              f"{DEFAULT_SNAP_THRESHOLD_M:.0f} m; furthest {furthest:.0f} m")

        return {
            "source": f"vrp.matrix.build_large_matrix via the gateway; "
                      f"matrix version {matrix.version}",
            "note": ("Real road travel over synthetic coordinates, built the "
                     "way production builds one. The network is real; the "
                     "places are the fixture's, not the operation's."),
            "matrix_version": matrix.version,
            "snap_threshold_m": DEFAULT_SNAP_THRESHOLD_M,
            "snapped_beyond_threshold": len(far),
            "furthest_snap_m": round(furthest, 1),
            "index": {key(lat, lon): row for row, (lat, lon) in enumerate(pts)},
            "durations": [list(row) for row in matrix.durations],
            "distances": [list(row) for row in matrix.distances],
        }
    finally:
        for process in (gateway, osrm):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()


if __name__ == "__main__":
    sys.path.insert(0, str(HERE.parent.parent))
    data = record()
    with gzip.open(ROAD, "wt", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    print(f"wrote {ROAD.name} ({ROAD.stat().st_size / 1024:.0f} KiB)")
