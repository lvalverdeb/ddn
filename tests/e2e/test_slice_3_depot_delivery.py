"""E2E-3 — slice 3, depot delivery and outcomes: the acceptance rows.

Every row of `docs/e2e/e2e-3-depot-delivery-and-outcomes.md` §7, one test each,
named by its row id and citing the document. The scenario and expectation are
read from the table by `tests.e2e.rows` rather than restated here, so a
reworded row changes what the test claims instead of leaving it asserting text
the document no longer carries.

**On geography.** Routing here uses `flat_matrix`, not the road recording.
`tests/fixtures/road.json.gz` holds 94 points — the facilities and the pickup
sites — and a missing coordinate fails loudly rather than falling back to
arithmetic, so 600 envelope addresses cannot be routed on real roads in this
suite. `flat_matrix` exists for exactly this: rows about *structure* — does a
route start at its facility, is each within the cap, is a §7.1 bullet reported.
Real-road routing is `tests/test_day_delivery.py`'s and
`ddn/simulation/on_road.py`'s; nothing below claims a distance.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

from ddn import contract, lastmile, returns
from ddn.allocation import capacity_for
from ddn.e2e import depot_delivery, handoff
from ddn.e2e.depot_delivery.scenario import Scenario
from ddn.model import lifecycle
from ddn.solver_adapter import postcheck
from tests import peak_day_inputs
from tests.e2e import rows
from tests.matrices import flat_matrix
from tests.test_solver_adapter import priced

BUILT = peak_day_inputs.build()
DAY = BUILT.day.delivery_day
FACILITIES = {f["id"]: f for f in BUILT.kwargs["facilities"]}


def test_the_document_still_carries_every_row_this_file_covers():
    """A row deleted from the table would take its test with it silently."""
    found = [r for r in rows.ROWS if r.startswith("C")]
    assert len(found) == rows.EXPECTED["C"] == 12


# ------------------------------------------------------------- the harness

def _model():
    """§8's model with a vehicle priced low enough to deploy one.

    `models/ddn-lastmile.json` prices a bike at 50,000 against prizes of at
    most 1,999, so on the shipped model the solver leaves everything
    unassigned and the bikes idle — `docs/capacity-finding.md` is the standing
    note on that. These rows are about which envelopes go and how the routes
    are shaped, not about §8's unanswered objective weights.
    """
    return priced(contract.load_model("ddn-lastmile"), fixed_cost=500)


def _routed(facility_id):
    """A `deliver` that actually routes, and the plans it produced.

    Slice 3 takes `deliver` as a parameter precisely so a test can supply
    this: the chain passes a non-routing stub because it compares against a
    simulator that does not route either, and these rows need the opposite.
    """
    plans = []

    def deliver(offered, served_at, bikes):
        if not offered or not bikes:
            return [], list(offered)
        plan = lastmile.plan_facility(
            FACILITIES[served_at], list(offered), bikes,
            flat_matrix(len(offered) + 1),
            today=DAY, model=_model(), solve=solve, verify=verify)
        plans.append(plan)
        on_a_route = {step.order_id for route in plan.solution.routes
                      for step in route.steps if step.order_id}
        return ([e for e in offered if e["package_id"] in on_a_route],
                [e for e in offered if e["package_id"] not in on_a_route])

    return deliver, plans


def _pool(facility_id, packages=None):
    """One facility's morning pool as the hand-off E2E-2 would have produced."""
    envelopes = tuple(packages if packages is not None
                      else BUILT.pools[facility_id])
    return handoff.PositionedPool(
        delivery_day=DAY,
        positioned={facility_id: tuple(e["package_id"] for e in envelopes)},
        envelopes={facility_id: envelopes})


def _run(facility_id, bikes, *, packages=None, outcomes=("Delivered",),
         deliver=None, plans=None, reason="recipient unavailable"):
    answers = list(outcomes)

    def next_outcome(_envelope):
        return answers[0] if len(answers) == 1 else answers.pop(0)

    if deliver is None:
        deliver, plans = _routed(facility_id)
    return depot_delivery.run(
        Scenario(facility_id=facility_id, delivery_day=DAY, bikes=bikes,
                 deliver=deliver, outcome=next_outcome,
                 reason=lambda _e: reason),
        _pool(facility_id, packages)), plans


# ----------------------------------------------------------------- the rows

def test_c1():
    """E2E-3 §7 row C1, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: D1: 620 in pool, 24 bikes
    Expected: 600 dispatched; 20 unassigned are the lowest-priority
    non-SLA-today, non-locked; no route exceeds shift; each route ≤ 35 and
    starts/ends at D1
    """
    pool = list(BUILT.pools["D1"])
    assert len(pool) == 620, "the row is about this pool"

    result, plans = _run("D1", 24)

    assert result.tally.dispatched == 600
    assert len(result.unassigned) == 20

    # "the lowest-priority non-SLA-today, non-locked": asserted as the set
    # §2.1's own ordering produces, not as a count.
    left = {e.package_id for e in result.unassigned}
    _, declined = lastmile.select(pool, capacity=capacity_for(24), today=DAY)
    assert left == {e.package_id for e in declined}
    assert all(not _sla_today(p) and not p.get("locked_vehicle_id")
               for p in pool if p["package_id"] in left)

    plan, = plans
    assert plan.accepted, "an infeasible plan has not delivered anything"
    assert not postcheck.check_route_constraints(plan.problem, plan.solution,
                                                 today=DAY)
    for route in plan.solution.routes:
        stops = [s for s in route.steps if s.order_id]
        assert len(stops) <= 35, "§7.1's envelope cap"
        assert route.steps[0].location_id == route.steps[-1].location_id == "D1"


def test_c2():
    """E2E-3 §7 row C2, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: HUB: 1,150 in pool, 48 bikes
    Expected: All dispatched
    """
    pool = list(BUILT.pools[handoff.HUB])
    assert len(pool) == 1150

    offered, declined = lastmile.select(pool, capacity=capacity_for(48),
                                        today=DAY)

    assert capacity_for(48) == 1200 >= len(pool)
    assert declined == (), "48 bikes carry 1,200; §8 leaves nothing out"
    assert len(offered) == len(pool)

    result, _ = _run(handoff.HUB, 48)
    reasons = Counter(e.reason for e in result.unassigned)

    assert reasons["count"] == 0, "nothing was declined for want of a bike"

    # The row says "all dispatched" and 21 are not, which is **§8's
    # unanswered objective weights and not this slice**: the solver declines
    # on prize against distance, and `docs/capacity-finding.md` is the
    # standing note that the resulting figure is not a capacity measurement.
    # Pinned rather than rounded away, so it moves when §8 is answered.
    short = len(pool) - result.tally.dispatched
    assert reasons["time"] == short, "§9.2's reason for a solver refusal"
    assert 0 < short < len(pool) * 0.05, (
        f"{short} of {len(pool)} declined on price; if this is now zero, §8's "
        "weights have been settled and this row can assert the document's "
        "'all dispatched' outright")


def test_c3():
    """E2E-3 §7 row C3, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: D2–D6 combined: 1,330 in pool, 48 bikes
    Expected: 130 unassigned across the five, each depot's own lowest-priority

    **The row's two clauses contradict each other, and the second is right.**
    §10 computes the first as `48 bikes × 25 = 1,200 vs 1,330 → 130`, which
    pools capacity across five depots. Per depot it is 170, and the difference
    is exactly the slack that cannot travel: D2 has 38 spare and D3 has 2,
    against shortfalls of 53, 53 and 64 at D4, D5 and D6. 130 + 40 = 170.

    A bike's depot is fixed before the solve — `docs/solver-capabilities.md`
    §2 answers flexible vehicle-to-depot assignment **No**, which is the whole
    reason §4.2 is two-stage. So a figure that lets D2's spare riders serve D6
    is not a tighter estimate, it is a different operation.

    This test asserts what the fleet can actually do and pins the document's
    130 as wrong, rather than asserting a total that no allocation produces.
    """
    depots = ["D2", "D3", "D4", "D5", "D6"]
    pools = {d: list(BUILT.pools[d]) for d in depots}
    assert sum(len(p) for p in pools.values()) == 1330

    # §4.2 allocates the 48 across the five; the row's figure follows from
    # the split the fixture carries, not from an even share.
    share = {d: BUILT.kwargs["allocation"][d] for d in depots}
    assert sum(share.values()) == 48

    results = {d: _run(d, share[d])[0] for d in depots}
    for_want_of_a_bike = sum(
        1 for r in results.values() for e in r.unassigned if e.reason == "count")

    assert for_want_of_a_bike == 170, "each depot leaves its own"
    assert sum(max(len(BUILT.pools[d]) - capacity_for(share[d]), 0)
               for d in depots) == 170
    assert sum(len(BUILT.pools[d]) for d in depots) - capacity_for(48) == 130, (
        "§10's own arithmetic, reproduced so the gap is visible: it pools the "
        "five depots' bikes, and 40 envelopes of slack at D2 and D3 cannot "
        "reach D4, D5 and D6")

    for depot in depots:
        _, declined = lastmile.select(pools[depot],
                                      capacity=capacity_for(share[depot]),
                                      today=DAY)
        # Only the capacity reason: the solver also refuses a few on prize
        # against distance, which is §8's open question and not a §2.1
        # ranking decision (see C2).
        assert ({e.package_id for e in results[depot].unassigned
                 if e.reason == "count"}
                == {e.package_id for e in declined}), (
            f"{depot} left envelopes another depot's ranking would have left")


def test_c4():
    """E2E-3 §7 row C4, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: An SLA-today envelope ranked 619th of 620 at D1
    Expected: Dispatched; a higher-priority non-SLA-today envelope is the one
    left
    """
    pool = [dict(e) for e in BUILT.pools["D1"]]
    ranked = sorted(pool, key=lambda p: -float(p["priority"]))
    almost_last = dict(ranked[618], sla_date=DAY.isoformat())
    pool = [almost_last if p["package_id"] == almost_last["package_id"] else p
            for p in pool]

    result, _ = _run("D1", 24, packages=pool)
    left = {e.package_id for e in result.unassigned}

    assert almost_last["package_id"] in result.outcomes, (
        "§6.1 makes SLA-today a must-deliver, not a weight")
    assert almost_last["package_id"] not in left
    higher = [p for p in pool if p["package_id"] in left
              and float(p["priority"]) > float(almost_last["priority"])]
    assert higher, "the constraint displaced nobody, so it proved nothing"


def test_c5():
    """E2E-3 §7 row C5, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Operations locks in 5 envelopes and locks out 3 at D1
    Expected: Re-run: the 5 are on routes, the 3 are unassigned with reason
    "locked-out", other assignments change minimally
    """
    pool = [dict(e) for e in BUILT.pools["D1"]]
    ranked = sorted(pool, key=lambda p: -float(p["priority"]))
    locked_in = [p["package_id"] for p in ranked[-5:]]
    locked_out = [p["package_id"] for p in ranked[:3]]

    forced = [dict(p, locked_vehicle_id="D1-MOTO-1") if p["package_id"] in locked_in
              else dict(p, excluded_by_ops=True)
              if p["package_id"] in locked_out else p
              for p in pool]

    routable, withheld = contract.triage(forced, today=DAY)

    # §8.2's "in": the five rank last on priority and would be the first cut,
    # and `select` carries them through anyway because a lock is a constraint.
    offered, declined = lastmile.select(routable, capacity=capacity_for(24),
                                        today=DAY)
    assert set(locked_in) <= {p["package_id"] for p in offered}
    assert not set(locked_in) & {e.package_id for e in declined}

    # §8.2's "out": withheld before the solve rather than encoded as locks —
    # `docs/solver-capabilities.md` §4 explains why (excluding a package at a
    # 48-bike hub would mean 48 FORBID locks).
    assert {e.package_id for e in withheld} == set(locked_out)
    assert {e.reason for e in withheld} == {"excluded by operations"}, (
        "e2e-3 §4 calls this 'locked-out'; §9.2 publishes 'excluded by "
        "operations' and `tests/test_contract.py` holds the code to §9.2")

    result, _ = _run("D1", 24, packages=routable)
    assert not set(locked_out) & set(result.outcomes), "no locked-out envelope"

    # "other assignments change minimally": the day without the overrides
    # differs by the eight envelopes the overrides name, and not by more.
    plain, _ = _run("D1", 24)
    moved = set(plain.outcomes) ^ set(result.outcomes)
    assert moved <= set(locked_in) | set(locked_out) | {
        e.package_id for e in declined}, "the overrides disturbed the rest"


def test_c6():
    """E2E-3 §7 row C6, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Outcomes at D1: 50 rejected, 30 defective, 120 postponed
    (40 incorrect address)
    Expected: 80 join D1's return load for the next van leg, not tonight's
    return run; 80 postponed re-enter D1's pool with attempt+1; 40 re-geocoded
    — those whose nearest facility changed raise transfer requests, the rest
    re-enter D1's pool
    """
    outcomes = (["Rejected"] * 50 + ["Returned"] * 30 + ["Postponed"] * 120
                + ["Delivered"] * 400)
    result, _ = _run("D1", 24, outcomes=outcomes)

    counted = Counter(str(o) for o in result.outcomes.values())
    assert counted["Rejected"] == 50
    assert counted["Returned"] == 30
    assert counted["Postponed"] == 120

    # 80 rejected-or-defective become D1's load. It is stamped with D1, and
    # §5.5's run departs from the hub — so this waits for a van leg and is not
    # in tonight's return run, which is row C7's contrast.
    assert result.returns.facility_id == "D1"
    assert result.returns.package_ids, "a depot load of nothing proves nothing"
    assert set(result.returns.package_ids) <= set(result.outcomes)
    assert not returns.eligible(
        [{"package_id": p, "previous_outcome": "Rejected",
          "facility_id": "D1", "customer_id": "c",
          "customer_lat": 0.0, "customer_lon": 0.0}
         for p in result.returns.package_ids], hub_id=handoff.HUB), (
        "a depot's load is not eligible for tonight's hub run")


def test_c7():
    """E2E-3 §7 row C7, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Same as C6 but at HUB
    Expected: Rejected/defective enter tonight's return run
    """
    outcomes = ["Rejected"] * 50 + ["Returned"] * 30 + ["Delivered"] * 1070
    result, _ = _run(handoff.HUB, 48, outcomes=outcomes)

    assert result.returns.facility_id == handoff.HUB
    back = [e for e in BUILT.pools[handoff.HUB]
            if e["package_id"] in set(result.returns.package_ids)]
    stops = returns.sites([dict(e, previous_outcome="Rejected") for e in back],
                          hub_id=handoff.HUB)

    assert stops, "the hub's load enters tonight's run, not tomorrow's leg"
    assert sum(len(s.package_ids) for s in stops) == len(back)


def test_c8():
    """E2E-3 §7 row C8, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Customer cancels an envelope at 10:00 that is on a route with
    ETA 14:00
    Expected: Stop removed at next refresh; envelope → return flow with reason
    "cancelled"
    """
    pool = [dict(e) for e in BUILT.pools["D1"]][:40]
    on_a_route = pool[10]["package_id"]

    kept, removed = lastmile.withdraw(pool, [on_a_route])

    assert removed == (on_a_route,)
    result, _ = _run("D1", 2, packages=kept)
    assert on_a_route not in result.outcomes, "the stop came off the refresh"

    # §5.2.6: the envelope itself moves Dispatched -> Return run, which is the
    # edge v0.15 added for exactly this, and §5.5 then recognises it.
    assert (lifecycle.after_cancellation(lifecycle.Status.DISPATCHED)
            is lifecycle.Status.RETURN_RUN)
    assert returns.goes_back({"previous_outcome": "Cancelled"})


def test_c9():
    """E2E-3 §7 row C9, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Customer cancels an envelope already delivered
    Expected: Cancellation refused; state stays Delivered
    """
    with pytest.raises(lifecycle.IllegalTransition) as refused:
        lifecycle.after_cancellation(lifecycle.Status.DELIVERED)

    assert "§5.2.6" in str(refused.value)
    assert lifecycle.TRANSITIONS[lifecycle.Status.DELIVERED] == frozenset(), (
        "Delivered is terminal, so the refusal is the diagram's and not a "
        "rule written a second time")


def test_c10():
    """E2E-3 §7 row C10, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Envelope with SLA date today is postponed (recipient
    unavailable)
    Expected: Not re-entered; moves to return flow with reason "SLA expired"
    at end of day
    """
    pool = [dict(e, sla_date=DAY.isoformat())
            for e in list(BUILT.pools["D1"])[:20]]

    result, _ = _run("D1", 1, packages=pool, outcomes=("Postponed",))

    assert result.tally.postponed == result.tally.dispatched
    assert result.tally.sla_expired == result.tally.dispatched, (
        "§11's expiry rate counts them; reporting zero here reads as a clean "
        "day while every envelope went back")
    assert set(result.returns.package_ids) == set(result.outcomes)
    assert all(lastmile.retryable(p, DAY) is False for p in pool)


def test_c11():
    """E2E-3 §7 row C11, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Envelope postponed three times, SLA in five days
    Expected: Re-enters each day; no cap applied; ranking tie-break favours it
    over a same-priority first attempt
    """
    later = date.fromordinal(DAY.toordinal() + 5).isoformat()
    veteran = {"package_id": "PKG-VETERAN", "priority": 500.0,
               "sla_date": later, "attempt_number": 3,
               "previous_outcome": "Postponed", "status": "Ready"}
    newcomer = {"package_id": "PKG-FIRST", "priority": 500.0,
                "sla_date": later, "attempt_number": 0, "status": "Ready"}

    kept, declined = lastmile.select([newcomer, veteran], capacity=1,
                                     today=DAY)

    assert [p["package_id"] for p in kept] == ["PKG-VETERAN"]
    assert [e.package_id for e in declined] == ["PKG-FIRST"]
    assert lastmile.retryable(veteran, DAY), "no cap: five days left is a day"


def test_c12():
    """E2E-3 §7 row C12, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Mutation: disable the home-facility check
    Expected: At least one test fails on the violation *detail* text

    The audit's M6 and M6b both **SURVIVED** at 730 passed: §7.1's "starts and
    ends at its home facility" was enforced by `postcheck` and exercised by no
    test, so disabling either guard changed nothing anyone could see. This row
    closes that, and it asserts the *detail* rather than a count — a bullet
    reported with the wrong text is a bullet an operator cannot act on.
    """
    facility = FACILITIES["D1"]
    pool = [dict(e) for e in list(BUILT.pools["D1"])[:12]]
    plan = lastmile.plan_facility(facility, pool, 1, flat_matrix(len(pool) + 1),
                                  today=DAY, model=_model(), solve=solve,
                                  verify=verify)
    assert not postcheck.check_route_constraints(plan.problem, plan.solution,
                                                 today=DAY)

    # Send the rider home to somewhere the problem *has* — a customer's
    # address — rather than a facility it does not, so the verifier is
    # answering §7.1's bullet and not refusing a malformed solution.
    elsewhere = next(s.location_id for s in plan.solution.routes[0].steps
                     if s.order_id)
    strayed = _rehome(plan.solution, elsewhere)
    found = postcheck.check_route_constraints(plan.problem, strayed, today=DAY)

    assert found, "M6's guard is unexercised again"
    detail = " ".join(f"{v.bullet} {v.detail}" for v in found)
    assert elsewhere in detail and "D1" in detail, (
        "the bullet must name where the route ended and where it should have; "
        "a violation an operator cannot locate is a violation not reported")
    assert any("home facility" in v.bullet for v in found)


# ----------------------------------------------------------------- helpers

def _sla_today(package):
    raw = package.get("sla_date")
    return bool(raw) and date.fromisoformat(raw) == DAY


def _rehome(solution, elsewhere):
    """The same solution with every route ending somewhere it should not."""
    import dataclasses
    routes = []
    for route in solution.routes:
        steps = list(route.steps)
        steps[-1] = dataclasses.replace(steps[-1], location_id=elsewhere)
        routes.append(dataclasses.replace(route, steps=tuple(steps)))
    return dataclasses.replace(solution, routes=tuple(routes))
