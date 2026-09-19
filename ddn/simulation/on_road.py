"""§5.4 across every facility, on real geography — the day-one run.

Spawns its own `osrm-routed` over a Costa Rica graph and the compiled gateway,
so travel times are real road distances rather than a synthetic grid.

Rate limits are raised for the run, following `tests/conftest_gateway.py` in
the platform repo and for the reason it states: the deployed limits shed a long
batch partway through. The first attempt did exactly that — 305 of the hub's
625 matrix tiles came back 429, `build_large_matrix` recorded them as NFR-04
degradation, and half the matrix was sentinel. It read as 1,271 unreachable
addresses. `plan_facility` now refuses a degraded matrix outright.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import date

import httpx
from vrp.matrix import PairCache, build_large_matrix
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

from ddn import assumptions, contract, lastmile
from ddn.model.facility import nearest_facility


def _need(var: str, what: str) -> str:
    """The value of environment variable `var`, or exit saying what it is for.

    Args:
        var: Name of the environment variable to read.
        what: What the caller should point it at, quoted back in the error.

    Returns:
        The variable's value.
    """
    value = os.environ.get(var)
    if not value:
        sys.exit(f"{var} is unset -- {what}")
    return value


# This run needs two things that live in neither repository: the platform's
# *compiled* gateway plus its corpus, and a built OSRM graph. Both are
# machine-local and neither belongs in git, so they are named rather than
# guessed. `vrp` itself is imported above as the installed distribution --
# ddn depends on vrp-platform, so reaching into a checkout with sys.path would
# contradict the dependency the package declares.
REPO = _need(
    "DDN_PLATFORM_REPO",
    "path to an osrm-microservice checkout with a built gateway and corpus",
)
OSRM_GRAPH = _need(
    "DDN_OSRM_GRAPH",
    "base path of a built OSRM graph, e.g. /somewhere/costa-rica-latest.osrm",
)

GATEWAY = f"{REPO}/gateway/target/debug/osrm-api-gateway"
CORPUS = f"{REPO}/data/deliveries_cr.json"

# osrm-routed and the gateway are started with their output discarded, so a
# missing input is otherwise a silent two-minute wait on a port that never
# opens. `.cell_metrics` is the last file osrm-customize writes, which is what
# "the graph is built" means -- the same test the platform's rc.d script makes.
for _path, _hint in (
    (GATEWAY, "cargo build --manifest-path gateway/Cargo.toml"),
    (CORPUS, "make corpus"),
    (f"{OSRM_GRAPH}.cell_metrics", "make process-osrm"),
):
    if not os.path.exists(_path):
        sys.exit(f"missing {_path} -- build it with: {_hint}")

TODAY = date(2026, 9, 16)
#: §10: "All 4,550 ready envelopes ... are positioned for delivery tomorrow."
#: A literal rather than a registry read, and the distinction is the point: the
#: registry holds `[TBD]` stand-ins, and 4,550 is a figure the document states.
#: Putting it in `docs/assumptions.md` would file a stated number among the
#: invented ones, which is the confusion that page exists to prevent.
POOL = 4550
#: The fleet, by contrast, *is* a placeholder -- §4.2's table is `[TBD]` and
#: §10 supplies 120 illustratively. So it is read, not copied.
BIKES = assumptions.MOTORBIKES_TOTAL

def free() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def wait(u: str, n: int = 240) -> None:
    for _ in range(n):
        try:
            httpx.get(u, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False

op, ap = free(), free()
osrm = subprocess.Popen(["osrm-routed","--algorithm","mld","--port",str(op),
    "--max-table-size","1000",OSRM_GRAPH],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
gw = subprocess.Popen([GATEWAY],
    env={**os.environ,"OSRM_BASE_URL":f"http://127.0.0.1:{op}","HOST":"127.0.0.1",
         "PORT":str(ap),"VRP_MAX_STOPS":"5000",
         **{f"RATE_LIMIT_{k}":"1000000/minute" for k in
            ("ROUTE","MATRIX","MATCH","TRIP","VRP","NEAREST","NEAREST_BATCH","TILE")}},
    cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{ap}"

try:
    assert wait(f"http://127.0.0.1:{op}/nearest/v1/driving/-84.08,9.93"), "osrm down"
    assert wait(f"{base}/health"), "gateway down"

    with open(CORPUS) as handle:
        corpus = json.load(handle)
    facilities = [{"id": ("HUB" if i == 0 else f"D{i}"),
                   "lat": d["latitude"], "lon": d["longitude"],
                   "shift_start": 21600, "shift_end": 50400}   # 06:00-14:00, §7.4's 8h
                  for i, d in enumerate(corpus["depots"])]

    # §9.1 records from the corpus. Priority bands per §8.1's illustration.
    packages = []
    for i, r in enumerate(corpus["deliveries"][:POOL]):
        score = 1500 if i % 12 == 0 else (150 if i % 3 else 40)
        packages.append({"package_id": r["order_id"], "lat": r["latitude"],
                         "lon": r["longitude"], "geocode_confidence": "high",
                         "status": "Ready", "priority": score,
                         "sla_date": "2026-09-16" if i % 97 == 0 else "2026-09-25",
                         "weight_g": int(r["weight_kg"] * 1000),
                         "attempt_number": 1})

    # One cache across the whole run: MTX-10 expects ≥90% pair reuse, and the
    # sort matrix below covers every pair a facility matrix will ask for again.
    cache = PairCache()

    routable, excluded = contract.triage(packages, today=TODAY)

    # §3.3 assigns "by road distance", so sorting needs a matrix before any
    # facility has one of its own: the facilities and the whole routable pool
    # in one table. It is the largest build of the run and buys the thing that
    # cannot be corrected later -- an envelope sorted to a depot that is nearer
    # on paper and further by road is simply at the wrong depot all day.
    sort_points = [*facilities, *routable]
    sort_matrix, _ = build_large_matrix(
        base, [(p["lat"], p["lon"]) for p in sort_points],
        cache=cache, timeout=600.0)
    sort_index = {p.get("id", p.get("package_id")): row
                  for row, p in enumerate(sort_points)}
    print(f"sort matrix {sort_matrix.size} locations")

    by_facility: dict[str, list] = {f["id"]: [] for f in facilities}
    for pkg in routable:
        by_facility[nearest_facility(pkg, facilities, sort_matrix,
                                     sort_index)].append(pkg)

    pools = {f: len(v) for f, v in by_facility.items()}
    bikes = lastmile.allocate(pools, BIKES)
    model = contract.load_model()

    print(f"pool {len(packages)}  routable {len(routable)}  excluded {len(excluded)}")
    print(f"fleet {BIKES} bikes, allocated by demand (§4.2 two-stage)\n")
    print(f"{'facility':>9}{'offered':>9}{'bikes':>7}{'served':>8}{'unassigned':>12}"
          f"{'per bike':>10}{'outcome':>18}")

    plans = []
    unreachable = {}
    for f in facilities:
        pkgs = by_facility[f["id"]]
        if not pkgs:
            continue
        pts = [(f["lat"], f["lon"])] + [(p["lat"], p["lon"]) for p in pkgs]
        matrix, _ = build_large_matrix(base, pts, cache=cache, timeout=180.0)
        keep = lastmile.reachable_subset(matrix, len(pts))
        dropped = len(pts) - len(keep)
        if dropped:
            from vrp.matrix import submatrix
            matrix = submatrix(matrix, keep)
            pkgs = [pkgs[i - 1] for i in keep[1:]]
            unreachable[f["id"]] = dropped
        plan = lastmile.plan_facility(f, pkgs, bikes[f["id"]], matrix,
                                      today=TODAY, model=model,
                                      solve=solve, verify=verify)
        plans.append(plan)
        per = plan.served / plan.bikes_used if plan.bikes_used else 0
        status = "accepted" if plan.accepted else f"REFUSED/{plan.solution.status}"
        print(f"{plan.facility_id:>9}{plan.offered:>9}{plan.bikes:>7}{plan.served:>8}"
              f"{plan.unassigned:>12}{per:>10.1f}{status:>18}")

    served = sum(p.served for p in plans)
    offered = sum(p.offered for p in plans)
    used = sum(p.bikes_used for p in plans)
    ok = sum(1 for p in plans if p.accepted)
    print(f"{'TOTAL':>9}{offered:>9}{BIKES:>7}{served:>8}{offered-served:>12}"
          f"{(served/used if used else 0):>10.1f}"
          f"{f'{ok}/{len(plans)} accepted':>18}")
    print()
    rate = assumptions.ENVELOPES_PER_BIKE
    print(f"§8.3 delivery check: {BIKES} bikes x ~{rate} = {BIKES * rate} "
          "nominal capacity")
    print(f"  actually served {served} of {offered} offered "
          f"({served/offered:.0%}); {offered-served} unassigned")
    print(f"  effective envelopes per deployed bike: {served/used:.1f}" if used else "")
    if unreachable:
        print(f"  dropped before solving, no road path: {sum(unreachable.values())} "
              f"{unreachable}")
finally:
    for p in (gw, osrm):
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
