"""§9.1's records, as a `Problem` the platform can solve.

The operation's data contract and the platform's domain model are different
shapes, and the mapping between them is where an operation is most easily
misread. Two decisions are load-bearing enough to state here rather than leave
in the code.

**Priority is a class and a score, not one number.** §8.1 asks for a numeric
score with classes encoded as widely-spaced tiers, on the reasoning that
"strict lexicographic objectives over classes often are not" supported. This
platform does support them -- but it compiles them into prize bonuses that grow
multiplicatively in the number of *distinct* tiers, and measured against
`vrp.solve.pyvrp_adapter.tier_bonuses` they pass int64 somewhere between 50 and
55. §8.1's own encoding has 1,098 distinct values. So the band becomes
`priority_tier` (three of them) and the score within it becomes `prize`, which
is what §8.1 asks for in its second bullet and is safe by nine orders of
magnitude.

**The SLA clock is not the commercial priority.** §6.1 warns that the
prioritisation algorithm already folds SLA proximity into its score, so
weighting it again double-counts, and proposes treating "SLA date is today" as
a hard constraint instead -- "a constraint, not a weight". `FR-25` is that
distinction in the platform: `priority_source` separates COMMERCIAL from SLA,
and `priority_tier = 0` is must-serve whatever the prize. Nothing is counted
twice, and neither idea has to be weakened to fit.
"""

from __future__ import annotations

from datetime import date

import pytest
from vrp.model import TravelMatrix

from ddn import contract

TODAY = date(2026, 9, 16)

HUB = {"id": "HUB", "lat": 9.9400, "lon": -84.0500,
       "shift_start": 28800, "shift_end": 57600}


def package(pid: str, **over) -> dict:
    """A §9.1 package record, with the fields that section marks optional left
    out unless a test is about them."""
    return {"package_id": pid, "lat": 9.941, "lon": -84.051,
            "geocode_confidence": "high", "facility_id": "HUB",
            "priority": 150, "sla_date": "2026-09-20",
            "attempt_number": 1, **over}


def van(vid: str = "V1", **over) -> dict:
    return {"vehicle_id": vid, "type": "motorbike", "facility_id": "HUB",
            "role": "delivery", "capacity_count": 35,
            "capacity_weight_g": 35_000,
            "shift_start": 28800, "shift_end": 57600, **over}


def matrix_for(n: int) -> TravelMatrix:
    rows = tuple(tuple(0 if i == j else 240 for j in range(n)) for i in range(n))
    return TravelMatrix(version="ddn-test", durations=rows, distances=rows)


def build(packages, vehicles=None, **kw):
    routable, excluded = contract.triage(packages, today=TODAY)
    problem = contract.to_problem(
        HUB, routable, vehicles or [van()], matrix_for(len(routable) + 1),
        today=TODAY, **kw)
    return problem, excluded


# --------------------------------------------------------------------------
# Triage: what never reaches the solver, and why (§3.3, §6.1, §9.2)
# --------------------------------------------------------------------------

def test_a_low_confidence_address_is_flagged_rather_than_routed():
    """§3.3: "Packages with low confidence should be flagged rather than
    routed, since an incorrect address is a known cause of postponement." """
    routable, excluded = contract.triage(
        [package("P1"), package("P2", geocode_confidence="low")], today=TODAY)

    assert [p["package_id"] for p in routable] == ["P1"]
    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P2", contract.LOW_GEOCODE_CONFIDENCE)]


def test_a_package_past_its_sla_date_goes_to_the_return_run():
    """§6.1: "Once the SLA date has passed without delivery, the package is
    returned to the customer via the return run." It is not a delivery."""
    _, excluded = contract.triage(
        [package("P1", sla_date="2026-09-15")], today=TODAY)

    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P1", contract.SLA_EXPIRED)]


def test_a_reason_is_one_of_the_codes_the_output_contract_names():
    """§9.2's unassigned list has a fixed vocabulary. A reason invented here
    would be a reason no consumer of that output knows how to read."""
    assert contract.LOW_GEOCODE_CONFIDENCE in contract.REASONS
    assert contract.SLA_EXPIRED in contract.REASONS


# --------------------------------------------------------------------------
# Packages (§9.1) -> Orders
# --------------------------------------------------------------------------

def test_a_package_carries_both_capacity_dimensions():
    """§4.1 gives motorbikes a count limit and vans a weight limit, and the
    solver carries both, so an order has to answer both questions."""
    problem, _ = build([package("P1", weight_g=450)])
    order, = problem.orders

    assert order.quantities == {"envelopes": 1, "grams": 450}


def test_an_unrecorded_weight_is_the_stated_default():
    """§4.1: "Actual weight is used where recorded; otherwise a default of
    200 g applies." """
    problem, _ = build([package("P1")])

    assert problem.orders[0].quantities["grams"] == 200


def test_service_time_defaults_to_ten_minutes_and_is_carried_in_seconds():
    """§7.4's ten minutes, and the platform counts seconds."""
    problem, _ = build([package("P1"), package("P2", service_time_min=25)])

    assert problem.orders[0].delivery.service_fixed == 600
    assert problem.orders[1].delivery.service_fixed == 1500


def test_a_package_with_no_window_may_be_served_any_time_in_the_shift():
    """§7.3: "Deliveries are any time during the shift; per-package time
    windows are not required by default." """
    problem, _ = build([package("P1")])
    window, = problem.orders[0].delivery.time_windows

    assert (window.start, window.end) == (28800, 57600)


def test_the_rare_per_package_window_is_honoured_when_it_is_there():
    """§7.3 keeps the field "for exceptional cases"."""
    problem, _ = build([package("P1", time_window_start=32400,
                                time_window_end=39600)])
    window, = problem.orders[0].delivery.time_windows

    assert (window.start, window.end) == (32400, 39600)


# --------------------------------------------------------------------------
# Priority (§8.1) and the SLA clock (§6.1)
# --------------------------------------------------------------------------

def test_the_band_becomes_the_tier_and_the_score_becomes_the_prize():
    """The decision this module exists to make. Three tiers, not 1,098."""
    problem, _ = build([package("U", priority=1800),
                        package("S", priority=150),
                        package("L", priority=30)])
    urgent, standard, low = problem.orders

    assert (urgent.priority_tier, urgent.prize) == (1, 1800)
    assert (standard.priority_tier, standard.prize) == (2, 150)
    assert (low.priority_tier, low.prize) == (3, 30)


def test_the_tier_count_stays_far_inside_the_solver_s_ceiling():
    """`tier_bonuses` compounds multiplicatively in distinct tiers and passes
    int64 between 50 and 55 of them. Mapping the raw score would wrap."""
    problem, _ = build([package(f"P{i}", priority=1000 + i) for i in range(40)])
    tiers = {o.priority_tier for o in problem.orders}

    assert len(tiers) <= len(contract.BANDS) + 1  # +1 for the SLA tier


def test_an_sla_date_of_today_is_a_constraint_rather_than_a_weight():
    """§6.1's exception, and the reason it does not double-count: tier 0 is
    must-serve whatever the prize, so urgency is expressed once."""
    problem, _ = build([package("P1", sla_date="2026-09-16", priority=30)])
    order, = problem.orders

    assert order.priority_tier == 0
    assert order.priority_source == "SLA"


def test_an_ordinary_package_keeps_the_commercial_source():
    """`FR-25`: the three sources are ordered differently and expire
    differently, so a package that is merely valuable is not an obligation."""
    problem, _ = build([package("P1")])

    assert problem.orders[0].priority_source == "COMMERCIAL"


# --------------------------------------------------------------------------
# Vehicles (§9.1) -> Vehicles, and the override (§8.2)
# --------------------------------------------------------------------------

def test_a_vehicle_carries_both_limits_and_its_shift():
    problem, _ = build([package("P1")])
    vehicle, = problem.vehicles

    assert vehicle.capacities == {"envelopes": 35, "grams": 35_000}
    assert (vehicle.shift.start, vehicle.shift.end) == (28800, 57600)


def test_route_duration_is_a_hard_limit_and_not_merely_the_shift():
    """§7.4: "The shift duration, not the 35-envelope limit, is the binding
    constraint... The solver must enforce route duration as a hard
    constraint." `INV-6` makes `max_duration` hard."""
    problem, _ = build([package("P1")])

    assert problem.vehicles[0].max_duration == 57600 - 28800


def test_a_role_becomes_a_skill_so_earmarked_vehicles_stay_earmarked():
    """§7.1: "Earmarked pickup vehicles are not assigned delivery stops." """
    problem, _ = build([package("P1")], vehicles=[van(), van("V2", role="pickup")])
    delivery, pickup = problem.vehicles

    assert "delivery" in delivery.skills
    assert "pickup" in pickup.skills
    assert problem.orders[0].required_skills == {"delivery"}


def test_an_operations_override_becomes_a_lock():
    """§8.2: "The solver must support re-running with a set of locked
    assignments so that overrides are respected." """
    problem, _ = build([package("P1", locked_vehicle_id="V1")])

    assert [(l.kind, l.order_id, l.vehicle_id) for l in problem.locks] == [
        ("PIN_ORDER_TO_VEHICLE", "P1", "V1")]


def test_a_package_with_no_override_locks_nothing():
    problem, _ = build([package("P1")])

    assert problem.locks == ()


def test_a_matrix_that_does_not_span_the_stops_is_refused():
    """The quiet failure this mapping can produce.

    Indices are the only thing tying a package to its travel times. A matrix
    built over a different set still has numbers in every cell, so a plan comes
    back, verifies, and attributes every arc to the wrong stop. Nothing about
    the output looks wrong -- which is why this is a refusal rather than a
    warning.
    """
    routable, _ = contract.triage([package("P1"), package("P2")], today=TODAY)

    with pytest.raises(ValueError, match="must span exactly 3"):
        contract.to_problem(HUB, routable, [van()], matrix_for(5), today=TODAY)
