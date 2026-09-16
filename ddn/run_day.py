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

REPO = "/Users/lvalverdeb/TeamDev/osrm-microservice"
SP = "/private/tmp/claude-501/-Users-lvalverdeb-TeamDev-osrm-microservice-examples/7d6c99b3-0c46-40fd-80b2-0e5510f5ffbf/scratchpad"
sys.path.insert(0, REPO)
from vrp.matrix import PairCache, build_large_matrix
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

from ddn import contract, lastmile

TODAY = date(2026, 9, 16)
POOL, BIKES = 4550, 120          # §10: positioned for delivery; shared fleet

def free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

def wait(u, n=240):
    for _ in range(n):
        try:
            httpx.get(u, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(0.5)
    return False

op, ap = free(), free()
osrm = subprocess.Popen(["osrm-routed","--algorithm","mld","--port",str(op),
    "--max-table-size","1000",f"{SP}/osrm/costa-rica-latest.osrm"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
gw = subprocess.Popen([f"{REPO}/gateway/target/debug/osrm-api-gateway"],
    env={**os.environ,"OSRM_BASE_URL":f"http://127.0.0.1:{op}","HOST":"127.0.0.1",
         "PORT":str(ap),"VRP_MAX_STOPS":"5000",
         **{f"RATE_LIMIT_{k}":"1000000/minute" for k in
            ("ROUTE","MATRIX","MATCH","TRIP","VRP","NEAREST","NEAREST_BATCH","TILE")}},
    cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = f"http://127.0.0.1:{ap}"

try:
    assert wait(f"http://127.0.0.1:{op}/nearest/v1/driving/-84.08,9.93"), "osrm down"
    assert wait(f"{base}/health"), "gateway down"

    with open(f"{REPO}/data/deliveries_cr.json") as handle:
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

    routable, excluded = contract.triage(packages, today=TODAY)
    by_facility: dict[str, list] = {f["id"]: [] for f in facilities}
    for pkg in routable:
        by_facility[lastmile.nearest_facility(pkg, facilities)].append(pkg)

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
        matrix, _ = build_large_matrix(base, pts, cache=PairCache(), timeout=180.0)
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
    print(f"§8.3 delivery check: {BIKES} bikes x ~25 = {BIKES*25} nominal capacity")
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
