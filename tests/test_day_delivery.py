"""§10's delivery half: selection at D1, the shift limit, and the return run.

§10 states D1's morning pool as 620 against 24 bikes — "D1 24 × 25 = 600 vs
620 → 20 unassigned". The fixture carries 850 D1-bound envelopes with
priorities, so the morning pool here is 620 of them: the *count* is §10's and
the priorities are the fixture's.
"""

from __future__ import annotations

from datetime import date

import pytest
from vrp.model import TravelMatrix
from vrp.solve.pyvrp_adapter import solve

from ddn import assumptions, contract, returns
from ddn import solver_adapter as sa
from ddn.allocation import EFFECTIVE_PER_BIKE
from ddn.lastmile import select
from ddn.model import Status
from ddn.solver_adapter import postcheck
from tests.fixtures import peak_day

HOUR = 3600
D1_BIKES = peak_day.BIKE_ALLOCATION["D1"]
D1_POOL = 620            # §10
D1_CAPACITY = D1_BIKES * EFFECTIVE_PER_BIKE


@pytest.fixture(scope="module")
def day():
    return peak_day.load()


def as_record(envelope, sites) -> dict:
    """A §9.1 record for the return run: the outcome, and the customer's site.

    `ddn.returns` keys on `previous_outcome` and on being at the hub, per §5.5 —
    an envelope rejected at a depot rides back on the van and joins tomorrow
    night's run instead.
    """
    return {"package_id": envelope.package_id,
            "customer_id": envelope.customer_id,
            "previous_outcome": str(envelope.previous_outcome),
            "facility_id": envelope.facility_id,
            "weight_g": envelope.weight_g,
            "customer_lat": sites[envelope.customer_id].lat,
            "customer_lon": sites[envelope.customer_id].lon,
            "sla_date": envelope.sla_date.isoformat()}


@pytest.fixture(scope="module")
def d1_morning(day):
    """§10's 620-envelope morning pool at D1, as §9.1 records."""
    pool = [e for e in day.ready() if e.facility_id == "D1"][:D1_POOL]
    return [{"package_id": e.package_id, "lat": e.lat, "lon": e.lon,
             "status": "Ready", "geocode_confidence": "high",
             "priority": float(e.priority),
             "sla_date": e.sla_date.isoformat()} for e in pool]


def test_section_10s_arithmetic_is_still_section_10s(d1_morning):
    assert len(d1_morning) == D1_POOL == 620
    assert D1_BIKES == 24
    assert D1_CAPACITY == 600
    assert D1_POOL - D1_CAPACITY == 20


def test_d1_leaves_exactly_twenty_unassigned(day, d1_morning):
    """§10: "D1 24 × 25 = 600 vs 620 → 20 unassigned"."""
    kept, declined = select(d1_morning, capacity=D1_CAPACITY,
                            today=day.delivery_day)
    assert len(declined) == 20
    assert len(kept) == 600
    assert {u.reason for u in declined} == {sa.NO_CAPACITY}


def test_the_twenty_are_the_lowest_priority_non_sla_today(day, d1_morning):
    """§8: priority-weighted, with §6.1's must-serve envelopes never trimmed."""
    kept, declined = select(d1_morning, capacity=D1_CAPACITY,
                            today=day.delivery_day)
    dropped = {u.package_id for u in declined}
    optional = [p for p in d1_morning
                if date.fromisoformat(p["sla_date"]) != day.delivery_day]
    expected = {p["package_id"] for p in sorted(
        optional, key=lambda p: (p["priority"], p["package_id"]))[:20]}

    assert dropped == expected
    worst_kept = min(p["priority"] for p in kept
                     if p["package_id"] not in dropped
                     and date.fromisoformat(p["sla_date"]) != day.delivery_day)
    assert max(p["priority"] for p in d1_morning
               if p["package_id"] in dropped) <= worst_kept


def test_an_sla_today_envelope_is_never_trimmed(day, d1_morning):
    """§6.1: a hard must-deliver-today constraint, not a weighting."""
    _, declined = select(d1_morning, capacity=D1_CAPACITY,
                         today=day.delivery_day)
    dropped = {u.package_id for u in declined}
    due = {p["package_id"] for p in d1_morning
           if date.fromisoformat(p["sla_date"]) == day.delivery_day}
    assert due, "the fixture should carry envelopes due on the delivery day"
    assert not (due & dropped)


def test_a_locked_envelope_is_never_unassigned(day, d1_morning):
    """§8.2: operations may force an envelope in, and the plan honours it."""
    worst = min(d1_morning, key=lambda p: (p["priority"], p["package_id"]))
    pool = [dict(p, locked_vehicle_id="MOTO-050")
            if p["package_id"] == worst["package_id"] else p
            for p in d1_morning]

    kept, declined = select(pool, capacity=D1_CAPACITY, today=day.delivery_day)
    assert worst["package_id"] not in {u.package_id for u in declined}
    assert worst["package_id"] in {p["package_id"] for p in kept}
    assert len(declined) == 20, "forcing one in pushes another out"


def test_forced_envelopes_beyond_capacity_are_all_still_offered(day, d1_morning):
    """§6.1 and §8.2 are constraints; a fleet too small is the solver's answer."""
    pool = [dict(p, sla_date=day.delivery_day.isoformat()) for p in d1_morning]
    kept, declined = select(pool, capacity=10, today=day.delivery_day)
    assert len(kept) == D1_POOL
    assert declined == ()


# ------------------------------------------------------------- the shift limit

def matrix(size: int, seconds: int = 180) -> TravelMatrix:
    return TravelMatrix(
        version="test",
        durations=tuple(tuple(0 if i == j else seconds for j in range(size))
                        for i in range(size)),
        distances=tuple(tuple(0 if i == j else 700 for j in range(size))
                        for i in range(size)))


def test_no_route_exceeds_the_shift(day, d1_morning):
    """§7.1 and §7.4: route duration is a hard constraint, per vehicle."""
    shift_start, shift_end = 7 * HOUR, 15 * HOUR
    pool = d1_morning[:30]
    bikes = [{"vehicle_id": f"MOTO-{n:03d}", "type": "motorbike",
              "facility_id": "D1", "role": "delivery", "capacity_envelopes": 35,
              "capacity_weight_g": 35_000, "shift_start": shift_start,
              "shift_end": shift_end} for n in range(1, 4)]
    facility = {"id": "D1", "lat": 9.9981, "lon": -84.1197,
                "shift_start": shift_start, "shift_end": shift_end}

    model = contract.load_model()
    model["run"] = dict(model["run"],
                        objective=dict(model["run"]["objective"],
                                       vehicle_fixed_cost=500))
    problem = sa.last_mile(facility, pool, bikes, matrix(len(pool) + 1),
                           today=day.delivery_day, model=model)
    solution = solve(problem)
    assert solution.status == "FEASIBLE"

    plan = sa.from_solver(solution, problem)
    for route in plan.routes:
        assert route.duration_s <= shift_end - shift_start
        assert route.stops[0].eta >= shift_start
        assert route.stops[-1].departure <= shift_end

    violations = sa.check_route_constraints(
        problem, solution, packages=pool, vehicles=bikes,
        today=day.delivery_day)
    assert postcheck.ROUTE_WITHIN_SHIFT not in {v.bullet for v in violations}
    assert violations == []


# ----------------------------------------------------------------- return run

def test_the_return_run_covers_every_envelope_exactly_once(day):
    """§5.5: rejected, defective and SLA-expired envelopes go back."""
    pool = peak_day.returns_pool()
    sites = {request.customer_id: request for request in day.requests}
    records = [as_record(e, sites) for e in pool]

    stops = returns.sites(records)
    assert len(stops) == peak_day.RETURN_SITES == 30

    carried = [pid for stop in stops for pid in stop.package_ids]
    assert len(carried) == peak_day.RETURN_POOL == 80
    assert len(set(carried)) == 80, "no envelope is returned twice"
    assert set(carried) == {e.package_id for e in pool}


def test_a_postponed_envelope_does_not_go_back(day):
    """§6: Postponed is held at the facility for another attempt, not returned."""
    pool = peak_day.returns_pool()
    sites = {request.customer_id: request for request in day.requests}
    records = [as_record(e, sites) for e in pool]
    held = dict(records[0], package_id="HELD-1",
                previous_outcome=str(Status.POSTPONED))

    carried = {pid for stop in returns.sites([*records, held])
               for pid in stop.package_ids}
    assert "HELD-1" not in carried
    assert len(carried) == 80


def test_the_return_fleet_is_vans_until_question_13_is_answered(day):
    """Open Question 13, carried as a placeholder rather than as a decision."""
    crew = returns.fleet(["VAN-01", "VAN-02"], shift_start=17 * HOUR,
                         shift_end=21 * HOUR)
    assert len(crew) == peak_day.RETURN_VANS == 2
    assert {v["type"] for v in crew} == {assumptions.RETURN_VEHICLE_TYPE}
    assert {v["type"] for v in crew} == {"van"}


def test_the_return_run_solves_from_the_hub(day):
    pool = peak_day.returns_pool()
    sites = {request.customer_id: request for request in day.requests}
    records = [as_record(e, sites) for e in pool]
    stops = returns.sites(records)
    hub = {"id": "HUB", "lat": day.facilities[0].lat,
           "lon": day.facilities[0].lon,
           "shift_start": 17 * HOUR, "shift_end": 21 * HOUR}

    model = returns.load_model()
    model["run"] = dict(model["run"],
                        objective=dict(model["run"]["objective"],
                                       vehicle_fixed_cost=500))
    problem = returns.to_problem(
        hub, stops,
        returns.fleet(["VAN-01", "VAN-02"], shift_start=17 * HOUR,
                      shift_end=21 * HOUR),
        matrix(len(stops) + 1, seconds=120), model=model)
    solution = solve(problem)
    assert solution.status == "FEASIBLE"
    for route in solution.routes:
        assert route.steps[0].location_id == "HUB"
        assert route.steps[-1].location_id == "HUB"


def test_the_fixture_still_carries_section_10s_outcomes():
    """Guards the return tests from drifting off §10."""
    assert (peak_day.REJECTED, peak_day.DEFECTIVE) == (50, 30)
    assert peak_day.RETURN_POOL == 80
    assert peak_day.DELIVERED == 2880
    assert peak_day.POSTPONED == 120
