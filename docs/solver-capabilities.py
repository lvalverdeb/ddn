"""Open Question 5, answered by running each capability."""
from dataclasses import replace
from datetime import date

from vrp.model import Lock, TimeWindow, TravelMatrix
from vrp.quote import quote_insertion
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

from ddn import contract

TODAY = date(2026, 9, 16)
HUB = {"id": "HUB", "lat": 9.94, "lon": -84.05, "shift_start": 28800, "shift_end": 57600}

def pkgs(n, **over):
    return [{"package_id": f"P{i}", "lat": 9.94 + i/400, "lon": -84.05 + i/500,
             "geocode_confidence": "high", "status": "Ready", "priority": 5000,
             "sla_date": "2026-09-20", **over} for i in range(n)]

def bikes(n=2):
    return [{"vehicle_id": f"M{k}", "type": "motorbike", "facility_id": "HUB",
             "role": "delivery", "capacity_envelopes": 35,
             "capacity_weight_g": 35000, "shift_start": 28800,
             "shift_end": 57600} for k in range(1, n+1)]

def matrix(n):
    return TravelMatrix(version="m",
        durations=tuple(tuple(0 if i==j else 240 for j in range(n)) for i in range(n)),
        distances=tuple(tuple(0 if i==j else 900 for j in range(n)) for i in range(n)))

def demo_model():
    # The shipped model prices a vehicle at 50,000 against prizes of at most
    # 1,999, so it declines work rather than deploying a bike. That is §8's
    # objective question, not a solver capability, so the demonstration uses a
    # balance that actually deploys one.
    m = contract.load_model()
    m["run"] = dict(m["run"], objective=dict(m["run"]["objective"], vehicle_fixed_cost=500))
    return m

def build(packages, vehicles=None, **kw):
    r, _ = contract.triage(packages, today=TODAY)
    kw.setdefault("model", demo_model())
    return contract.to_problem(HUB, r, vehicles or bikes(), matrix(len(r)+1), today=TODAY, **kw)

print("1. DYNAMIC STOP INSERTION")
p = build(pkgs(8))
s = solve(p, 800, 0)
assignment = {r.vehicle_id: [st.order_id for st in r.steps if st.order_id] for r in s.routes}
held_out = p.orders[-1].id
without = {v: [o for o in ids if o != held_out] for v, ids in assignment.items()}
q = quote_insertion(p, without, held_out)
print(f"   inserting {q.order_id} into a live plan costs {q.price} on {q.vehicle_id}")
print(f"   resulting route: {' -> '.join(q.route)}")

print("\n2. FLEXIBLE VEHICLE-TO-DEPOT ASSIGNMENT")
print("   Vehicle.start_location_id is a required `str`: one depot, fixed before the solve.")
print("   PIN_DEPOT pins an order to a depot, not a vehicle. Not a decision variable.")

print("\n3. NATIVE DUE-DATE HANDLING")
p = build(pkgs(6, sla_date="2026-09-16"))          # SLA today
tiers = {o.priority_tier for o in p.orders}
s = solve(p, 800, 0)
served = {st.order_id for r in s.routes for st in r.steps if st.order_id}
must = {o.id for o in p.orders if o.priority_tier == 0}
print(f"   SLA-today -> tier {tiers}, source {p.orders[0].priority_source}")
print(f"   must-serve orders all served: {must <= served} ({len(must)} of them)")
hard = replace(p, orders=tuple(replace(o, delivery=replace(o.delivery,
        time_windows=(TimeWindow(start=28800, end=32400, hardness='HARD'),))) for o in p.orders))
hs = solve(hard, 800, 0)
print(f"   hard window honoured: verifier ok = {verify(hard, hs).ok}")

print("\n4. LOCKED ASSIGNMENTS")
p = build(pkgs(6), vehicles=bikes(2))
p = replace(p, locks=(Lock(kind="PIN_ORDER_TO_VEHICLE", order_id="P4", vehicle_id="M2"),))
s = solve(p, 800, 0)
where = {st.order_id: r.vehicle_id for r in s.routes for st in r.steps if st.order_id}
print(f"   P4 pinned to M2 -> solver placed it on {where.get('P4')}")
print(f"   verifier (INV-8) accepts: {verify(p, s).ok}")

print("\n5. READY-TIME CONSTRAINTS ON STOPS")
p = build(pkgs(4))
p = replace(p, orders=tuple(replace(o, release_time=39600) for o in p.orders))
s = solve(p, 800, 0)
dep = [r.steps[0].departure for r in s.routes if r.steps]
print(f"   released 11:00 -> solver departs at {dep} (39600 = 11:00)")
early = replace(s, routes=tuple(replace(r, steps=tuple(
    replace(st, arrival=st.arrival-7200, start_service=st.start_service-7200,
            departure=st.departure-7200) for st in r.steps)) for r in s.routes if r.steps))
rep = verify(p, early)
v = [x.detail for x in rep.violations if x.invariant == "INV-17"]
print(f"   a plan departing two hours early: INV-17 -> {v[0] if v else 'NOT CAUGHT'}")
