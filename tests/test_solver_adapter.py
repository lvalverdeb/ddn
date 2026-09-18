"""§9.1 in, §9.2 out, §7.1 checked.

The round trip is solved for real. The violations are not: a solver does not
produce a plan that breaks §7.1 on demand, so each bullet is broken by handing
the checker a solution built to break it. That is the point of a post-check —
it must catch a bad plan whatever produced it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from vrp.model import Problem, Route, Solution, Step, TravelMatrix
from vrp.solve.pyvrp_adapter import solve

from ddn import assumptions, contract
from ddn import solver_adapter as sa
from ddn.linehaul import LinehaulPlan, Trip
from ddn.solver_adapter import postcheck

TODAY = date(2026, 9, 16)
HUB = {"id": "HUB", "lat": 9.94, "lon": -84.05,
       "shift_start": 28800, "shift_end": 57600}
D1 = dict(HUB, id="D1", lat=9.99, lon=-84.12)

SPEC = (Path(__file__).resolve().parent.parent
        / "docs" / "vrp-problem-definition.md").read_text(encoding="utf-8")
SECTION_7_1 = SPEC.split("### 7.1")[1].split("### 7.2")[0].replace("*", "")


def matrix(n: int) -> TravelMatrix:
    return TravelMatrix(
        version="test",
        durations=tuple(tuple(0 if i == j else 240 for j in range(n))
                        for i in range(n)),
        distances=tuple(tuple(0 if i == j else 900 for j in range(n))
                        for i in range(n)))


def packages(n: int = 6, **over):
    return [{"package_id": f"P{i}", "customer_id": "C1", "lat": 9.94 + i / 400,
             "lon": -84.05 + i / 500, "geocode_confidence": "high",
             "status": "Ready", "priority": 5000, "sla_date": "2026-09-20",
             **over} for i in range(n)]


def bikes(n: int = 2, **over):
    return [{"vehicle_id": f"M{k}", "type": "motorbike", "facility_id": "HUB",
             "role": "delivery", "capacity_envelopes": 35,
             "capacity_weight_g": 35000, "shift_start": 28800,
             "shift_end": 57600, **over} for k in range(1, n + 1)]


def vans(n: int = 2, **over):
    return [{"vehicle_id": f"V{k}", "type": "van", "facility_id": "HUB",
             "role": "pickup",
             "capacity_mailbags": assumptions.MAILBAGS_PER_VAN,
             "capacity_weight_g": 500_000, "shift_start": 25200,
             "shift_end": 64800, **over} for k in range(1, n + 1)]


def bags(n: int = 4):
    return [{"mailbag_id": f"BAG-{i}", "customer_id": f"C{i}",
             "lat": 9.95 + i / 300, "lon": -84.06 + i / 400,
             "expected_weight_g": 6000, "envelope_count": 30}
            for i in range(n)]


def deployable(name: str = "ddn-lastmile") -> dict:
    """The shipped model priced so a bike is worth deploying.

    `models/ddn-lastmile.json` sets `vehicle_fixed_cost` to 50,000 against
    prizes of at most 1,999, so on a small pool the solver declines everything
    and no route exists to check. That is §8's unanswered objective question —
    `docs/solver-capabilities.md` records it — not a property of this adapter,
    so the fixture prices around it exactly as the capability probe does.
    """
    model = (contract.load_model() if name == "ddn-lastmile"
             else sa.load_pickup_model())
    model["run"] = dict(model["run"],
                        objective=dict(model["run"]["objective"],
                                       vehicle_fixed_cost=500))
    return model


def solved_last_mile(pool=None, fleet=None):
    pool = packages() if pool is None else pool
    fleet = bikes() if fleet is None else fleet
    routable, excluded = contract.triage(pool, today=TODAY)
    problem = sa.last_mile(HUB, routable, fleet, matrix(len(routable) + 1),
                           today=TODAY, model=deployable())
    return problem, solve(problem), excluded


def step(location_id, *, kind="DELIVERY", order_id=None, arrival=28800,
         load=None):
    return Step(type=kind, location_id=location_id, arrival=arrival,
                start_service=arrival, departure=arrival + 600,
                order_id=order_id, load_after=load or {})


def handmade(problem: Problem, routes) -> Solution:
    """A solution built to break something. See the module docstring."""
    return Solution(problem_id=problem.id, routes=tuple(routes), unassigned=(),
                    objective_breakdown={}, status="FEASIBLE", degraded=None,
                    solver=None)


def bullets_in(violations):
    return {v.bullet for v in violations}


# --------------------------------------------------------------- round trip

def test_the_bullets_are_section_7_1s_own_words():
    """Eleven bullets, transcribed. A reworded §7.1 fails here first."""
    assert len(postcheck.BULLETS) == 11
    for bullet in postcheck.BULLETS:
        assert bullet in SECTION_7_1, f"§7.1 no longer says {bullet!r}"
    assert len([line for line in SECTION_7_1.splitlines()
                if line.startswith("- ")]) == 11


def test_six_envelopes_two_bikes_one_facility_round_trips():
    """§9.1 records to a solved plan and back out as §9.2."""
    problem, solution, excluded = solved_last_mile()
    assert solution.status == "FEASIBLE"

    plan = sa.from_solver(solution, problem, excluded=excluded)
    assert plan.served == 6
    assert plan.unassigned == ()
    assert {stop.kind for route in plan.routes for stop in route.stops} <= {
        "delivery", "facility"}
    for route in plan.routes:
        assert route.stops[0].kind == "facility", "§7.1: starts at its facility"
        assert route.stops[-1].kind == "facility", "and ends there"
        assert route.distance_m > 0
        assert route.duration_s > 0
        etas = [stop.eta for stop in route.stops]
        assert etas == sorted(etas), "a route's ETAs run forwards"


def test_a_clean_solve_breaks_no_bullet():
    problem, solution, _ = solved_last_mile()
    assert check(problem, solution) == []


def check(problem, solution, **kw):
    kw.setdefault("packages", packages())
    kw.setdefault("vehicles", bikes())
    kw.setdefault("today", TODAY)
    return sa.check_route_constraints(problem, solution, **kw)


def test_an_unaccepted_plan_serves_nothing():
    """`lastmile.FacilityPlan.accepted`'s rule, applied to §9.2's output."""
    problem, solution, _ = solved_last_mile()
    plan = sa.from_solver(solution, problem, verified=False)
    assert plan.routes == ()
    assert len(plan.unassigned) == 6
    assert {u.reason for u in plan.unassigned} == {sa.NO_TIME}


def test_triage_reasons_reach_the_unassigned_list():
    """§9.2's pre-solve vocabulary, carried through rather than recomputed."""
    pool = packages(5) + packages(1, geocode_confidence="low")
    problem, solution, excluded = solved_last_mile(pool=pool)
    plan = sa.from_solver(solution, problem, excluded=excluded)
    assert any(u.reason == contract.LOW_GEOCODE_CONFIDENCE
               for u in plan.unassigned)
    assert {u.reason for u in plan.unassigned} <= sa.REASONS


def test_pickups_build_as_a_problem():
    """§5.1, which nothing built before: bags as orders, vans as the fleet."""
    requests = bags(4)
    problem = sa.pickup(HUB, requests, vans(), matrix(5), today=TODAY,
                        model=deployable("ddn-pickup"))
    assert len(problem.orders) == 4
    assert all(order.quantities["mailbags"] == 1 for order in problem.orders)
    assert all("pickup" in order.required_skills for order in problem.orders)
    assert solve(problem).status == "FEASIBLE"


def test_a_motorbike_is_refused_a_pickup_problem_outright():
    """§7.1 van-only: a bike here is a mistake upstream, not a stop to decline."""
    with pytest.raises(ValueError, match="van-only"):
        sa.pickup(HUB, bags(2), bikes(1), matrix(3), today=TODAY)


# ------------------------------------------- one deliberate breach per bullet

def test_a_motorbike_over_35_envelopes_is_a_violation():
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", load={"envelopes": 36}),
        step("P0", order_id="P0", load={"envelopes": 36}),
        step("HUB", kind="END", load={"envelopes": 0})))])
    assert postcheck.MOTORBIKE_CAPACITY in bullets_in(check(problem, broken))


def test_a_van_over_its_weight_is_a_violation():
    problem = sa.pickup(HUB, bags(2), vans(), matrix(3), today=TODAY,
                        model=deployable("ddn-pickup"))
    broken = handmade(problem, [Route(vehicle_id="V1", steps=(
        step("HUB", kind="START", load={"grams": 0}),
        step("BAG-0", kind="PICKUP", order_id="BAG-0",
             load={"grams": 500_001, "mailbags": 1}),
        step("HUB", kind="END", load={"grams": 500_001})))])
    found = sa.check_route_constraints(problem, broken, vehicles=vans(),
                                       today=TODAY, stage=postcheck.PICKUP)
    assert postcheck.VAN_WEIGHT in bullets_in(found)


def test_a_route_that_does_not_start_at_its_facility_is_a_violation():
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("P0", kind="START"),
        step("P1", order_id="P1"),
        step("HUB", kind="END")))])
    assert postcheck.ROUTE_WITHIN_SHIFT in bullets_in(check(problem, broken))


def test_a_route_longer_than_its_shift_is_a_violation():
    """The duration half of the same bullet, which INV-6 owns."""
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=28800),
        step("P0", order_id="P0", arrival=57000),
        step("HUB", kind="END", arrival=90000)))])
    assert postcheck.ROUTE_WITHIN_SHIFT in bullets_in(check(problem, broken))


def test_one_envelope_on_two_vehicles_is_a_violation():
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [
        Route(vehicle_id="M1", steps=(step("HUB", kind="START"),
                                      step("P0", order_id="P0"),
                                      step("HUB", kind="END"))),
        Route(vehicle_id="M2", steps=(step("HUB", kind="START"),
                                      step("P0", order_id="P0"),
                                      step("HUB", kind="END")))])
    assert postcheck.ONE_VEHICLE_PER_DAY in bullets_in(check(problem, broken))


def test_the_same_envelope_at_two_facilities_is_a_day_violation():
    """The per-*day* half, which one solve cannot see."""
    problem, _, _ = solved_last_mile()
    one = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START"), step("P0", order_id="P0"),
        step("HUB", kind="END")))])
    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"HUB": one, "D1": one}, packages=packages()))
    assert postcheck.ONE_VEHICLE_PER_DAY in bullets_in(found)


def test_serving_an_envelope_that_is_not_ready_is_a_violation():
    pool = packages(6, status="Sorted")
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START"), step("P0", order_id="P0"),
        step("HUB", kind="END")))])
    found = check(problem, broken, packages=pool)
    assert postcheck.READY_ONLY in bullets_in(found)


def test_dispatching_before_the_line_haul_arrives_is_a_day_violation():
    problem, _, _ = solved_last_mile()
    early = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("D1", kind="START", arrival=25200),
        step("P0", order_id="P0", arrival=25800),
        step("D1", kind="END", arrival=26400)))])
    plan = LinehaulPlan(trips=(Trip(van_id="V1", destination="D1",
                                    package_ids=("P0",), departure=10000,
                                    arrival=30000, returns=40000),))
    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"D1": early}, packages=packages(),
        linehaul=plan))
    assert postcheck.AFTER_ARRIVAL in bullets_in(found)


def test_dispatching_from_a_depot_nothing_carried_to_is_a_day_violation():
    problem, _, _ = solved_last_mile()
    orphan = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("D1", kind="START"), step("P0", order_id="P0"),
        step("D1", kind="END")))])
    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"D1": orphan}, packages=packages(),
        linehaul=LinehaulPlan()))
    assert postcheck.AFTER_ARRIVAL in bullets_in(found)


def test_a_van_leaving_before_it_is_unloaded_is_a_day_violation():
    plan = LinehaulPlan(trips=(Trip(van_id="V1", destination="D1",
                                    package_ids=(), departure=50000,
                                    arrival=60000, returns=70000),))
    found = sa.check_day_constraints(sa.Day(
        today=TODAY, linehaul=plan, van_back_at={"V1": 49000}))
    assert postcheck.VAN_UNLOADED_FIRST in bullets_in(found)
    assert "FACILITY_UNLOAD_MIN" in str(found[0]), "the limit is a placeholder"


def test_dispatching_after_the_sla_date_is_a_violation():
    pool = packages(6, sla_date="2026-09-15")
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START"), step("P0", order_id="P0"),
        step("HUB", kind="END")))])
    assert postcheck.NOT_PAST_SLA in bullets_in(check(problem, broken,
                                                      packages=pool))


def test_a_motorbike_given_pickup_stops_is_a_violation():
    """The bullet as a post-check, not only as the builder's refusal.

    The realistic breach is not a bike in the fleet the problem was built from
    — `sa.pickup` refuses that outright — but today's allocation putting a
    motorbike on pickup duty under a van's id, so that the §9.1 record and the
    problem disagree about what the vehicle is.
    """
    problem = sa.pickup(HUB, bags(2), vans(), matrix(3), today=TODAY,
                        model=deployable("ddn-pickup"))
    mislabelled = [dict(vans(1)[0], type="motorbike")]
    broken = handmade(problem, [Route(vehicle_id="V1", steps=(
        step("HUB", kind="START"),
        step("BAG-0", kind="PICKUP", order_id="BAG-0", load={"mailbags": 1}),
        step("HUB", kind="END")))])
    found = sa.check_route_constraints(problem, broken, vehicles=mislabelled,
                                       today=TODAY, stage=postcheck.PICKUP)
    assert postcheck.PICKUPS_VAN_ONLY in bullets_in(found)


def test_a_plan_naming_an_unknown_vehicle_is_reported_not_raised():
    """`vrp.verify` raises on it; a post-check must survive a malformed plan."""
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="GHOST", steps=(
        step("HUB", kind="START"), step("HUB", kind="END")))])
    found = check(problem, broken)
    assert postcheck.UNKNOWN_VEHICLE in bullets_in(found)


def test_a_van_over_its_mailbag_count_is_a_violation_naming_the_placeholder():
    problem = sa.pickup(HUB, bags(2), vans(), matrix(3), today=TODAY,
                        model=deployable("ddn-pickup"))
    over = assumptions.MAILBAGS_PER_VAN + 1
    broken = handmade(problem, [Route(vehicle_id="V1", steps=(
        step("HUB", kind="START"),
        step("BAG-0", kind="PICKUP", order_id="BAG-0",
             load={"mailbags": over}),
        step("HUB", kind="END")))])
    found = sa.check_route_constraints(problem, broken, vehicles=vans(),
                                       today=TODAY, stage=postcheck.PICKUP)
    assert postcheck.VAN_MAILBAGS in bullets_in(found)
    breach = next(v for v in found if v.bullet == postcheck.VAN_MAILBAGS)
    assert "Open Question 1" in breach.detail, "the limit is [TBD], not measured"


def test_a_bag_split_across_two_vans_is_a_violation():
    problem = sa.pickup(HUB, bags(2), vans(), matrix(3), today=TODAY,
                        model=deployable("ddn-pickup"))
    broken = handmade(problem, [
        Route(vehicle_id="V1", steps=(
            step("HUB", kind="START"),
            step("BAG-0", kind="PICKUP", order_id="BAG-0", load={"mailbags": 1}),
            step("HUB", kind="END"))),
        Route(vehicle_id="V2", steps=(
            step("HUB", kind="START"),
            step("BAG-0", kind="PICKUP", order_id="BAG-0", load={"mailbags": 1}),
            step("HUB", kind="END")))])
    found = sa.check_route_constraints(problem, broken, vehicles=vans(),
                                       today=TODAY, stage=postcheck.PICKUP)
    assert postcheck.BAGS_WHOLE in bullets_in(found)


def test_a_violation_reads_as_section_7_1():
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("P0", kind="START"), step("HUB", kind="END")))])
    text = str(check(problem, broken)[0])
    assert text.startswith("§7.1 ")
    assert re.search(r"\[[^\]]+\]$", text), "it should name the vehicle or order"
