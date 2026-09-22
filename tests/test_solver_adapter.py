"""§9.1 in, §9.2 out, §7.1 checked.

The round trip is solved for real. The violations are not: a solver does not
produce a plan that breaks §7.1 on demand, so each bullet is broken by handing
the checker a solution built to break it. That is the point of a post-check —
it must catch a bad plan whatever produced it.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
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

    `models/*.json` price a vehicle at 50,000 against a standard envelope's
    15,000, so on a small pool the solver correctly declines everything and no
    route exists to check. That is §8's unanswered cost ratio, not a property
    of this adapter, so the fixture prices around it.

    **It prices the fleet, not `run.objective`.** It used to edit the latter,
    and that did nothing at all: `vrp.servicemodel.run_config` is the only
    reader of `run.objective` and is never called here, so the 500 went
    nowhere. The cost that reaches PyVRP is the one `contract.costs` takes off
    each `fleet[]` entry.

    **And it is still belt-and-braces, which is worth saying.** Every pool in
    this file affords the shipped 50,000 — the suite is green with the pricing
    removed. The decline is real, but it needs a pool of three or four (see
    `test_contract.py::test_between_two_equal_priority_routings_the_shorter
    _one_wins`, which meets it). This exists so that adding such a pool does
    not mean rediscovering why nothing was served.
    """
    model = (contract.load_model() if name == "ddn-lastmile"
             else sa.load_pickup_model())
    return priced(model, fixed_cost=500)


def priced(model: dict, *, fixed_cost: int) -> dict:
    """The same model with every vehicle class priced to deploy."""
    return dict(model, fleet=[dict(spec, fixed_cost=fixed_cost)
                              for spec in model["fleet"]])


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
    """A solution built to break something. See the module docstring.

    Every order the routes do not serve is listed unassigned. Left out, the
    platform reports INV-1 "neither served nor listed unassigned" once per
    dropped order, and `INVARIANT_BULLET` maps INV-1 onto
    `ONE_VEHICLE_PER_DAY` -- so a test asserting that bullet was satisfied by
    bookkeeping noise before it ever reached the thing it was about. A handmade
    solution should break exactly what it means to break.
    """
    served = {s.order_id for route in routes for s in route.steps if s.order_id}
    return Solution(
        problem_id=problem.id, routes=tuple(routes),
        unassigned=tuple({"order_id": order.id, "reason_code": "NOT_PLACED"}
                         for order in problem.orders if order.id not in served),
        objective_breakdown={}, status="FEASIBLE", degraded=None, solver=None)


def details_in(violations, bullet):
    """What was actually said about one bullet, not merely that it was named.

    Four of §7.1's bullets share their reported string with a `vrp.verify`
    invariant, so `bullet in bullets_in(...)` cannot tell the local check from
    the platform's. The detail can.
    """
    return [v.detail for v in violations if v.bullet == bullet]


def bullets_in(violations):
    return {v.bullet for v in violations}


# --------------------------------------------------------------- round trip

def test_every_bullet_checked_is_section_7_1s_own_words():
    """A reworded §7.1 fails here first, which is how v0.12 was noticed."""
    assert postcheck.BULLETS, "an empty tuple would pass the loop below"
    for bullet in postcheck.BULLETS:
        assert bullet in SECTION_7_1, f"§7.1 no longer says {bullet!r}"


def test_the_two_halves_account_for_every_bullet():
    """The module docstring splits §7.1 by how much of the day each needs.

    That split is prose, so it can drift from the code exactly as the count
    did. Eight bullets one solve can answer, five it cannot -- and the second
    number was four until v0.12 added the two transfer bullets and nobody
    moved it.
    """
    day_spanning = {
        postcheck.ONE_VEHICLE_PER_DAY, postcheck.AFTER_ARRIVAL,
        postcheck.VAN_UNLOADED_FIRST, postcheck.COMBINED_LOAD,
        postcheck.TRANSFER_WITHIN_SLA,
    }

    assert day_spanning <= set(postcheck.BULLETS)
    assert len(day_spanning) == 5
    assert len(postcheck.BULLETS) - len(day_spanning) == 8
    assert "Eight bullets" in postcheck.__doc__
    assert "Five are not" in postcheck.__doc__


def test_section_7_1s_bullets_are_all_accounted_for():
    """Every bullet §7.1 writes is one this module transcribes, and no other.

    The count is read off the section rather than written down here. It used to
    be the literal 13, which is the wrong shape for it: §7.1 went from eleven
    bullets to thirteen in v0.12, and what survived that bump was every *other*
    copy of the number — two comments in `postcheck`, a docstring, and
    `solver_adapter/__init__.py` — all still saying eleven beside a tuple
    holding thirteen. A count taken from the section cannot pass while the
    section and the tuple disagree, whichever way the next revision moves it.

    `NOT_YET_ENFORCED` is in the sum because a bullet parked there is one
    §7.1 writes and no check answers; it is empty today.
    """
    written = [line[2:].replace("**", "").strip()
               for line in SECTION_7_1.splitlines() if line.startswith("- ")]
    accounted = set(postcheck.BULLETS) | set(postcheck.NOT_YET_ENFORCED)

    assert len(written) == len(postcheck.BULLETS) + len(postcheck.NOT_YET_ENFORCED)
    assert set(written) == accounted, (
        f"unaccounted §7.1 bullets: {sorted(set(written) - accounted)}")
    assert postcheck.NOT_YET_ENFORCED == (), (
        "a bullet here is one the module claims and no check answers")


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


# Each step is 600s of service and each hop 240s on `matrix`, so a route whose
# arrivals do not chain 28800 -> 29640 -> 30480 is one `vrp.verify` reports on
# INV-4 before any local check runs. INV-4 maps to ROUTE_WITHIN_SHIFT, which is
# how these tests used to pass with the check they name switched off.
START, VISIT, HOME = 28800, 29640, 30480


def test_a_route_that_does_not_start_at_its_facility_is_a_violation():
    """§7.1: "starts and ends at its home facility".

    The arrivals chain against `matrix` so INV-4 stays quiet, and the assertion
    is on the detail. Both matter: with neither, disabling `_home_bullets`
    entirely left this test green, because INV-4 was reporting under the same
    bullet string for a route that was merely mistimed.
    """
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("P0", kind="START", arrival=START),
        step("P1", order_id="P1", arrival=VISIT),
        step("HUB", kind="END", arrival=HOME)))])

    found = check(problem, broken)
    assert details_in(found, postcheck.ROUTE_WITHIN_SHIFT) == [
        "starts at P0, not its home HUB"]


def test_a_route_that_does_not_end_at_its_facility_is_a_violation():
    """The other half of the same sentence, which had no test at all."""
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=START),
        step("P1", order_id="P1", arrival=VISIT),
        step("P0", kind="END", arrival=HOME)))])

    found = check(problem, broken)
    assert details_in(found, postcheck.ROUTE_WITHIN_SHIFT) == [
        "ends at P0, not its home HUB"]


def test_a_route_outside_its_shift_is_a_violation():
    """The duration half of the same bullet, which INV-6 owns and this module
    delegates. Asserted as INV-6 so the row says who actually judged it."""
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=57000),
        step("P0", order_id="P0", arrival=57840),
        step("HUB", kind="END", arrival=58680)))])

    found = details_in(check(problem, broken), postcheck.ROUTE_WITHIN_SHIFT)
    assert any(d.startswith("INV-6:") and "outside shift" in d for d in found), (
        f"the shift window is INV-6's to report; got {found}")


def test_one_envelope_on_two_vehicles_is_a_violation():
    problem, _, _ = solved_last_mile()
    broken = handmade(problem, [
        Route(vehicle_id="M1", steps=(step("HUB", kind="START", arrival=START),
                                      step("P0", order_id="P0", arrival=VISIT),
                                      step("HUB", kind="END", arrival=HOME))),
        Route(vehicle_id="M2", steps=(step("HUB", kind="START", arrival=START),
                                      step("P0", order_id="P0", arrival=VISIT),
                                      step("HUB", kind="END", arrival=HOME)))])

    found = check(problem, broken)
    assert "served by M1, M2 in one solve" in details_in(
        found, postcheck.ONE_VEHICLE_PER_DAY)


def test_the_same_envelope_at_two_facilities_is_a_day_violation():
    """The per-*day* half, which one solve cannot see."""
    problem, _, _ = solved_last_mile()
    one = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=START),
        step("P0", order_id="P0", arrival=VISIT),
        step("HUB", kind="END", arrival=HOME)))])
    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"HUB": one, "D1": one}, packages=packages()))

    assert details_in(found, postcheck.ONE_VEHICLE_PER_DAY) == [
        "served 2 times today, by D1/M1, HUB/M1"]


def test_serving_an_envelope_that_is_not_ready_is_a_day_violation():
    """The same bullet across the day, which `_across_the_day` checks.

    `READY_ONLY` is checked twice — once per solve and once over the day — and
    only the per-solve half had a test. Disabling the day-level one left the
    whole suite green, which is how a check comes to be trusted for something
    it is not doing. Found by re-running the audit's M3 against both sites
    rather than the first one.
    """
    pool = packages(6, status="Sorted")
    problem, _, _ = solved_last_mile()
    one = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=START),
        step("P0", order_id="P0", arrival=VISIT),
        step("HUB", kind="END", arrival=HOME)))])

    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"HUB": one}, packages=pool))

    assert details_in(found, postcheck.READY_ONLY) == ["is Sorted, not Ready"]


def test_dispatching_past_the_sla_date_is_a_day_violation():
    """§7.1's other day-level package bullet, and it was unheld too.

    `NOT_PAST_SLA` is checked in the same loop as `READY_ONLY` — once per
    solve and once across the day — and the audit's M3 was re-run against both
    sites of `READY_ONLY` but not against this one. Measured on the tree at
    987 passed: turning `postcheck.py:413`'s comparison into `if False:` left
    every test green, so the day-level half of §6.1's clock was enforced by
    code nothing exercised. This is that mutation, closed.
    """
    pool = packages(6, sla_date=(TODAY - timedelta(days=1)).isoformat())
    problem, _, _ = solved_last_mile()
    one = handmade(problem, [Route(vehicle_id="M1", steps=(
        step("HUB", kind="START", arrival=START),
        step("P0", order_id="P0", arrival=VISIT),
        step("HUB", kind="END", arrival=HOME)))])

    found = sa.check_day_constraints(sa.Day(
        today=TODAY, solutions={"HUB": one}, packages=pool))

    assert postcheck.NOT_PAST_SLA in bullets_in(found)
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    assert details_in(found, postcheck.NOT_PAST_SLA) == [
        f"SLA date {yesterday} is before {TODAY.isoformat()}"]


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


def test_the_pickup_fleet_is_priced_from_the_model():
    """§8's objective rides on the vehicle, and `_van` replaces the one
    `servicemodel.build` priced.

    `contract.costs` exists because those costs were being left behind with
    the generated vehicles — "for the whole life of the repository", as its
    docstring says — and it did not look broken: `pyvrp_adapter` omits a zero
    cost and PyVRP applies its own `unit_distance_cost=1`, so routes came back
    sensibly short at a rate nobody had chosen and a van cost nothing to send.

    Nothing checked *this* path either. Measured: deleting
    `**contract.costs(...)` from `_van` left all 983 tests green.
    """
    model = deployable("ddn-pickup")
    problem = sa.pickup(HUB, bags(2), vans(1), matrix(3), today=TODAY,
                        model=model)
    priced = problem.vehicles[0]
    declared = next(spec for spec in model["fleet"] if spec["class"] == "VAN")

    assert priced.fixed_cost == int(declared["fixed_cost"])
    assert priced.cost_per_metre == int(declared["cost_per_metre"])
    assert priced.cost_per_second == int(declared["cost_per_second"])
    assert priced.fixed_cost > 0, "a van that costs nothing to send is free"


def test_a_pickup_model_that_prices_nothing_is_refused(): 
    """Absent is refused rather than defaulted: a default here is invisible
    and load-bearing, which is the failure this replaced."""
    model = deployable("ddn-pickup")
    unpriced = dict(model, fleet=[{k: v for k, v in spec.items()
                                   if k != "cost_per_metre"}
                                  for spec in model["fleet"]])

    with pytest.raises(ValueError, match="cost_per_metre"):
        sa.pickup(HUB, bags(2), vans(1), matrix(3), today=TODAY,
                  model=unpriced)
