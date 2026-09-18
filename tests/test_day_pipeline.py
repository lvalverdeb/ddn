"""§10's peak day driven through §5.1 → §5.2 → §5.3.

The assertions here are about **accounting**, not about capacity. Readiness is
computed from three invented throughputs (`docs/assumptions.md`), so how *much*
gets positioned is a property of those numbers; that every envelope ends up
somewhere, with a reason when it does not travel, is a property of the code.
`capacity-finding.md` is the standing warning about confusing the two.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from vrp.model import TravelMatrix
from vrp.solve.pyvrp_adapter import solve

from ddn import assumptions, contract
from ddn import solver_adapter as sa
from ddn.linehaul import DAY, latest_departure, plan
from ddn.model import Status, VehicleRole, VehicleType
from ddn.model import travel as road
from ddn.pickups import run as run_pickups
from ddn.processing import schedule
from ddn.solver_adapter import postcheck
from tests.fixtures import peak_day
from tests.matrices import fake_matrix

HOUR = 3600
UNLOAD = assumptions.FACILITY_UNLOAD_MIN * 60

#: A test input, not an assumption: how long after a customer asks that the bag
#: is expected back at the hub. §5.2.5 computes readiness from the van's
#: *expected* return, and the fixture has no dispatch behind it.
COLLECTION_LAG = 90 * 60


@pytest.fixture(scope="module")
def day():
    return peak_day.load()


def seconds(moment: datetime, *, origin: datetime) -> int:
    return int((moment - origin).total_seconds())


def as_facility(facility) -> dict:
    return {"id": facility.facility_id, "lat": facility.lat, "lon": facility.lon,
            "route_release_time": (facility.route_release_time.hour * HOUR
                                   + facility.route_release_time.minute * 60),
            "transit_from_hub_min": facility.transit_from_hub_min}


def readiness(day) -> dict[str, int]:
    """Every ready envelope's `expected_ready_at`, from §5.2.5's formula."""
    midnight = datetime.combine(day.collection_day, datetime.min.time())
    arrival_of = {
        bag.mailbag_id: seconds(request.requested_at, origin=midnight) + COLLECTION_LAG
        for request in day.requests for bag in request.mailbags
    }
    pool = [{"package_id": e.package_id, "mailbag_id": e.mailbag_id,
             "package_type": e.package_type}
            for e in day.ready()]
    return {r.package_id: r.ready_at for r in schedule(pool, arrival_of)}


def linehaul_for(day, ready_at):
    depots = [as_facility(f) for f in day.facilities if not f.is_hub]
    bound = [{"package_id": e.package_id, "facility_id": e.facility_id,
              "expected_ready_at": ready_at[e.package_id]}
             for e in day.ready() if e.facility_id != "HUB"]
    vans = [{"vehicle_id": v.vehicle_id,
             "linehaul_release_at": 0 if v.linehaul_release_at is None
             else seconds(v.linehaul_release_at,
                          origin=datetime.combine(day.collection_day,
                                                  datetime.min.time()))}
            for v in day.vehicles if v.type is VehicleType.VAN]
    return plan(depots, bound, vans, unload_seconds=UNLOAD), bound


def test_every_ready_envelope_is_positioned_or_rolled_with_a_reason(day):
    """The acceptance condition: nothing falls between the stages."""
    ready_at = readiness(day)
    assert len(ready_at) == peak_day.READY == 4550

    night, _ = linehaul_for(day, ready_at)
    hub_direct = {e.package_id for e in day.ready() if e.facility_id == "HUB"}
    carried = {pid for trip in night.trips for pid in trip.package_ids}
    rolled = {pid for ids in night.rolled.values() for pid in ids}

    positioned = hub_direct | carried
    assert positioned | rolled == {e.package_id for e in day.ready()}
    assert not (positioned & rolled), "an envelope is in one place or the other"
    assert len(positioned) + len(rolled) == 4550

    for facility_id in night.rolled:
        assert night.reason_for(facility_id) in {
            "no van could reach the depot before its morning release",
            "not ready before the van's latest departure",
        }, "§5.3 gives two causes and they are not the same problem"


def test_hub_direct_envelopes_need_no_line_haul(day):
    """§3.3: the hub dispatches its own; only depot-bound envelopes travel."""
    ready_at = readiness(day)
    _, bound = linehaul_for(day, ready_at)
    assert len(bound) == 4550 - peak_day.READY_BY_FACILITY["HUB"]
    assert all(e["facility_id"] != "HUB" for e in bound)


def test_no_van_departs_too_late_to_make_its_release(day):
    """§5.3: a van that cannot make the release deadline does not depart."""
    ready_at = readiness(day)
    night, _ = linehaul_for(day, ready_at)
    deadlines = {f.facility_id: latest_departure(as_facility(f),
                                                 unload_seconds=UNLOAD)
                 for f in day.facilities}
    releases = {f.facility_id: DAY + as_facility(f)["route_release_time"]
                for f in day.facilities}

    assert night.trips, "the fixture's fleet should reach at least one depot"
    for trip in night.trips:
        assert trip.departure <= deadlines[trip.destination]
        assert trip.arrival + UNLOAD <= releases[trip.destination], (
            "unloaded before the depot releases its routes")


def test_a_van_still_out_on_pickups_is_not_sent_on_line_haul(day):
    """§5.3 and §7.1: a van departs only once it is back and unloaded."""
    midnight = datetime.combine(day.collection_day, datetime.min.time())
    ready_at = readiness(day)
    night, _ = linehaul_for(day, ready_at)
    release_of = {v.vehicle_id: (0 if v.linehaul_release_at is None
                                 else seconds(v.linehaul_release_at,
                                              origin=midnight))
                  for v in day.vehicles if v.type is VehicleType.VAN}
    for trip in night.trips:
        assert trip.departure >= release_of[trip.van_id]


def test_the_pickup_day_collects_bags_or_accounts_for_them(day):
    """§5.1 over the fixture's own 180 bags and six earmarked vans."""
    midnight = datetime.combine(day.collection_day, datetime.min.time())
    requests = [{"mailbag_id": bag.mailbag_id, "customer_id": bag.customer_id,
                 "lat": request.lat, "lon": request.lon,
                 "requested_at": seconds(request.requested_at, origin=midnight),
                 "expected_weight_g": bag.expected_weight_g,
                 "envelope_count": bag.envelope_count}
                for request in day.requests for bag in request.mailbags]
    vans = [{"vehicle_id": v.vehicle_id, "type": "van", "role": "pickup",
             "capacity_mailbags": v.capacity_mailbags,
             "capacity_weight_g": v.capacity_weight_g,
             "shift_start": seconds(v.shift_start, origin=midnight)}
            for v in day.vehicles
            if v.type is VehicleType.VAN and v.role is VehicleRole.PICKUP]

    hub = {"id": "HUB", "lat": day.facilities[0].lat,
           "lon": day.facilities[0].lon}
    points = [hub, *requests]
    dispatch = run_pickups(
        requests, vans, hub,
        travel=road.over(fake_matrix(points), road.index_of(points)),
        cut_off=(assumptions.PROCESSING_CUTOFF.hour * HOUR
                 + assumptions.PROCESSING_CUTOFF.minute * 60))

    assert len(dispatch.collected) + len(dispatch.unplaced) == len(requests)
    assert set(dispatch.routes) == {v["vehicle_id"] for v in vans}
    assert len(dispatch.collected) > 0


def test_a_pickup_van_is_never_given_a_delivery_stop(day):
    """§7.1, proved by solving rather than by reading the allocation."""
    midnight = datetime.combine(day.delivery_day, datetime.min.time())
    pool = [e for e in day.ready() if e.facility_id == "HUB"][:8]
    packages = [{"package_id": e.package_id, "lat": e.lat, "lon": e.lon,
                 "status": "Ready", "geocode_confidence": "high",
                 "priority": float(e.priority),
                 "sla_date": e.sla_date.isoformat()} for e in pool]

    fleet = [{"vehicle_id": v.vehicle_id, "type": "motorbike",
              "facility_id": "HUB", "role": "delivery",
              "capacity_envelopes": 35, "capacity_weight_g": 35_000,
              "shift_start": seconds(v.shift_start, origin=midnight),
              "shift_end": seconds(v.shift_end, origin=midnight)}
             for v in day.vehicles
             if v.type is VehicleType.MOTORBIKE and v.facility_id == "HUB"][:2]
    earmarked = dict(fleet[0], vehicle_id="V-PICKUP", role="pickup")

    hub = {"id": "HUB", "lat": day.facilities[0].lat,
           "lon": day.facilities[0].lon,
           "shift_start": fleet[0]["shift_start"],
           "shift_end": fleet[0]["shift_end"]}
    size = len(packages) + 1
    matrix = TravelMatrix(
        version="test",
        durations=tuple(tuple(0 if i == j else 180 for j in range(size))
                        for i in range(size)),
        distances=tuple(tuple(0 if i == j else 700 for j in range(size))
                        for i in range(size)))

    model = contract.load_model()
    model["run"] = dict(model["run"],
                        objective=dict(model["run"]["objective"],
                                       vehicle_fixed_cost=500))
    problem = sa.last_mile(hub, packages, [*fleet, earmarked], matrix,
                           today=day.delivery_day, model=model)
    solution = solve(problem)

    on_the_van = [step.order_id for route in solution.routes
                  if route.vehicle_id == "V-PICKUP"
                  for step in route.steps if step.order_id]
    assert on_the_van == [], "§7.1 keeps earmarked pickup vehicles off delivery"
    assert postcheck.PICKUPS_VAN_ONLY not in {
        v.bullet for v in sa.check_route_constraints(
            problem, solution, packages=packages,
            vehicles=[*fleet, earmarked], today=day.delivery_day)}


def test_the_ready_pool_is_still_section_10s(day):
    """Guards the three tests above from drifting off the fixture."""
    assert len(day.ready()) == 4550
    assert {e.status for e in day.ready()} == {Status.READY}


def test_nothing_rolls_on_the_fixture_as_it_stands(day):
    """States the baseline the two tests below perturb.

    With the placeholder throughputs every envelope is ready by 16:09 and the
    earliest deadline is 02:30 the next morning, so the whole depot-bound pool
    travels. That makes the reason-checking above vacuous unless a roll is
    forced — which is what the next two tests do.
    """
    night, _ = linehaul_for(day, readiness(day))
    assert night.rolled == {}
    assert night.carried == 4550 - peak_day.READY_BY_FACILITY["HUB"]


def test_too_few_vans_rolls_a_depot_naming_the_fleet(day):
    """§5.3: "a van that cannot make the release deadline should not depart"."""
    ready_at = readiness(day)
    depots = [as_facility(f) for f in day.facilities if not f.is_hub]
    bound = [{"package_id": e.package_id, "facility_id": e.facility_id,
              "expected_ready_at": ready_at[e.package_id]}
             for e in day.ready() if e.facility_id != "HUB"]
    night = plan(depots, bound, [{"vehicle_id": "VAN-01"}], unload_seconds=UNLOAD)

    assert len(night.trips) == 1, "one van, one depot"
    assert len(night.rolled) == 5
    assert set(night.reasons.values()) == {
        "no van could reach the depot before its morning release"}
    carried = {pid for trip in night.trips for pid in trip.package_ids}
    rolled = {pid for ids in night.rolled.values() for pid in ids}
    assert carried | rolled == {e["package_id"] for e in bound}


def test_a_hub_too_slow_rolls_envelopes_naming_the_clock(day):
    """The other cause: the fleet was there and the envelopes were not."""
    depots = [as_facility(f) for f in day.facilities if not f.is_hub]
    late = [{"package_id": e.package_id, "facility_id": e.facility_id,
             "expected_ready_at": 3 * DAY}
            for e in day.ready() if e.facility_id == "D1"]
    vans = [{"vehicle_id": f"VAN-{n:02d}"} for n in range(1, 11)]
    night = plan(depots, late, vans, unload_seconds=UNLOAD)

    assert night.reason_for("D1") == "not ready before the van's latest departure"
    assert len(night.rolled["D1"]) == peak_day.READY_BY_FACILITY["D1"]
    assert night.trips[0].package_ids == (), "the van went, and went empty"
