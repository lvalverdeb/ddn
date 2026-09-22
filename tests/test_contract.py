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
from pathlib import Path

import pytest
from vrp.model import TravelMatrix

from ddn import assumptions, contract

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
            "role": "delivery", "capacity_envelopes": 35,
            "capacity_weight_g": 35_000,
            "shift_start": 28800, "shift_end": 57600, **over}


def contract_fields(heading: str, until: str) -> list[str]:
    """The field names one §9.1 table actually declares.

    Read from the document rather than copied out of it, because a name copied
    into a fixture stops tracking the contract the moment the contract moves.
    """
    doc = (Path(__file__).resolve().parents[1]
           / "docs" / "vrp-problem-definition.md").read_text(encoding="utf-8")
    block = doc.split(heading, 1)[1].split(until, 1)[0]
    names = [line.strip("|").split("|")[0].strip()
             for line in block.splitlines() if line.startswith("|")]
    return [n for n in names
            if n and n != "Field" and set(n) - set("-: ")]


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
# Triage: what never reaches the solver, and why (§3.2, §6.1, §9.2)
# --------------------------------------------------------------------------

def test_a_low_confidence_address_is_flagged_rather_than_routed():
    """§3.2: "Where geocoding fails or returns low confidence, the envelope
    is held and flagged rather than routed, since an incorrect location is a
    leading cause of postponement." """
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


@pytest.mark.parametrize("status", [
    # §5.2.6's lifecycle, every state that is not Ready. The first five are
    # short of readiness; the rest are past dispatch.
    "Requested", "Collected", "Received at hub", "Reconciled", "Assembled",
    "Sorted", "Dispatched", "Delivered", "Rejected", "Returned",
])
def test_an_envelope_that_is_not_ready_is_not_routed(status):
    """§7.1: "Only ready envelopes are assigned to line-haul or delivery."

    §5.2.6 is narrower still -- "only envelopes in Ready (at the hub or at a
    depot) are solver inputs for delivery routing" -- so readiness is a state
    and not a guess about one.
    """
    routable, excluded = contract.triage(
        [package("P1", status="Ready"), package("P2", status=status)],
        today=TODAY)

    assert [p["package_id"] for p in routable] == ["P1"]
    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P2", contract.NOT_READY)]


def test_a_postponed_envelope_is_ready_again_and_is_retried():
    """§6: a postponed envelope is "held at facility in Ready state", so it
    comes back as Ready rather than as a state of its own, and is retried while
    its SLA date has not passed."""
    retry = package("P1", status="Ready", attempt_number=2,
                    previous_outcome="Postponed")

    routable, excluded = contract.triage([retry], today=TODAY)

    assert [p["package_id"] for p in routable] == ["P1"]
    assert excluded == []


def test_a_pool_that_predates_the_status_field_is_left_alone():
    """The field is how a caller opts into §7.1's gate. Refusing a record that
    omits it would refuse every pool written before v0.10 named it."""
    routable, excluded = contract.triage([package("P1")], today=TODAY)

    assert [p["package_id"] for p in routable] == ["P1"]
    assert excluded == []


def test_an_envelope_both_stuck_and_expired_is_reported_as_expired():
    """Both are true and only one is actionable: §6.1 sends an expired envelope
    back to the customer on the return run, while an envelope short of Ready is
    waiting on the hub. The reason a dispatcher can act on wins."""
    stuck = package("P1", status="Reconciled", sla_date="2026-09-15")

    _, excluded = contract.triage([stuck], today=TODAY)

    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P1", contract.SLA_EXPIRED)]


def test_an_envelope_operations_pulled_out_is_withheld_and_reported():
    """§8.2: "Operations may force an envelope in or out of the plan."

    Out is not a lock. §8.2 says why: no single lock kind expresses "this
    envelope, on no vehicle", and the one-forbid-per-vehicle encoding would be
    forty-eight locks at the hub. So the envelope is withheld before the solve
    and appears in §9.2's unassigned list with its own reason — an override the
    operator can see in the output, rather than an envelope that silently is
    not there.
    """
    routable, excluded = contract.triage(
        [package("P1"), package("P2", excluded_by_ops=True)], today=TODAY)

    assert [p["package_id"] for p in routable] == ["P1"]
    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P2", contract.EXCLUDED_BY_OPS)]


def test_an_operations_exclusion_outranks_the_pipelines_own_reasons():
    """An operator who pulled an envelope out has said something about *this*
    envelope; "not ready by cut-off" would send them to the hub to chase a
    state nobody is waiting on. The actionable reason wins, as it does between
    `SLA_EXPIRED` and `NOT_READY` above."""
    withheld = package("P1", status="Reconciled", excluded_by_ops=True)

    _, excluded = contract.triage([withheld], today=TODAY)

    assert [(e.package_id, e.reason) for e in excluded] == [
        ("P1", contract.EXCLUDED_BY_OPS)]


def test_the_two_reason_vocabularies_are_one_vocabulary():
    """§9.2 has one list, and this repository holds it in two frozensets.

    `contract.REASONS` is what triage produces before any routing;
    `solver_adapter.REASONS` is what §9.2's unassigned list may carry. Every
    reason the first can produce must be sayable in the second, or an envelope
    reaches the output with a code the output does not admit.

    That is not hypothetical: §8.2's "excluded by operations" was added to
    `contract` and not to `output`, and both sets are separately checked
    against §9.2's text — so each was a subset of the document and neither was
    a subset of the other, and nothing compared them.
    """
    from ddn import solver_adapter as sa

    assert contract.REASONS <= sa.REASONS, (
        f"triage can produce {sorted(contract.REASONS - sa.REASONS)}, which "
        "§9.2's output vocabulary does not contain")


def test_a_reason_is_one_of_the_codes_the_output_contract_names():
    """§9.2's unassigned list has a fixed vocabulary. A reason invented here
    would be a reason no consumer of that output knows how to read.

    Read out of §9.2 rather than restated, for the reason the vehicle fields
    are: a constant compared against itself proves only that it was typed
    twice the same way.
    """
    doc = (Path(__file__).resolve().parents[1]
           / "docs" / "vrp-problem-definition.md").read_text(encoding="utf-8")
    line = next(l for l in doc.splitlines()
                if l.startswith("- **Unassigned envelopes:**"))
    published = {part.strip()
                 for part in line.split("(", 1)[1].rstrip(").").split("/")}

    assert contract.REASONS <= published, (
        f"reasons no consumer can read: {sorted(contract.REASONS - published)}")
    assert {contract.LOW_GEOCODE_CONFIDENCE, contract.SLA_EXPIRED,
            contract.NOT_READY} <= contract.REASONS


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

    S = contract.PRIZE_SCALE
    assert (urgent.priority_tier, urgent.prize) == (1, 1800 * S)
    assert (standard.priority_tier, standard.prize) == (2, 150 * S)
    assert (low.priority_tier, low.prize) == (3, 30 * S)


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


def test_the_shipped_model_prices_the_vehicles_it_builds():
    """§8's objective has to reach the solver, not merely be declared.

    Every path here replaces the vehicles `servicemodel.build` generates with
    its own, so a cost declared in the model file only arrives if the builder
    carries it across. For a long time none did: the three cost fields left the
    factory at zero, and `pyvrp_adapter` omits a zero cost, so PyVRP fell back
    to its own `unit_distance_cost=1` and the plans looked reasonable. Distance
    was being minimised at a rate nobody had chosen.

    This asserts the rate is chosen. It is the check that distinguishes a wired
    objective from an unwired one -- a test that merely watched the solver
    prefer a shorter route would have passed throughout.
    """
    problem, _ = build([package("P1")])
    vehicle, = problem.vehicles

    assert vehicle.cost_per_metre == assumptions.COST_PER_METRE
    assert vehicle.fixed_cost == assumptions.VEHICLE_FIXED_COST
    assert vehicle.cost_per_second == assumptions.COST_PER_SECOND
    assert vehicle.cost_per_metre, (
        "a zero here is not 'free', it is PyVRP's default of 1 applied "
        "silently -- see pyvrp_adapter.py:600")


def test_between_two_equal_priority_routings_the_shorter_one_wins():
    """§8's third objective: "Minimise total route time / distance".

    Four stops on a line out from the hub, all the same priority, one bike with
    room for all of them. Visiting them in order and coming back is 6 km; every
    other order is longer, so the solver has an unambiguous right answer and
    distance is the only thing separating the candidates.

    **This test would have passed before the objective was wired**, and that is
    worth stating rather than leaving for someone to discover. `pyvrp_adapter`
    omits a zero cost and PyVRP defaults `unit_distance_cost` to 1, so distance
    was already being minimised at a rate this repository had not chosen. What
    pins the rate is
    `test_the_shipped_model_prices_the_vehicles_it_builds`; this pins the
    behaviour that rate is supposed to produce.
    """
    from vrp.solve.pyvrp_adapter import solve

    km = 1000
    # Urgent, so that deploying the bike is unambiguously worth its
    # `VEHICLE_FIXED_COST` and the only question left is the visit order. At
    # §8.1's standard band three stops are worth 45,000 against a 50,000 bike
    # and the solver correctly declines all three -- which is the cost term
    # doing its job, not a failure, but it is not what this test is about.
    stops = [package(f"P{n}", lat=9.94 + n / 1000, priority=1500)
             for n in range(1, 4)]
    # Hub, then three stops at 1, 2 and 3 km along one road.
    places = [0, 1 * km, 2 * km, 3 * km]
    rows = tuple(tuple(abs(a - b) for b in places) for a in places)
    matrix = TravelMatrix(version="ddn-line", distances=rows,
                          durations=tuple(tuple(d // 10 for d in row)
                                          for row in rows))

    routable, _ = contract.triage(stops, today=TODAY)
    problem = contract.to_problem(HUB, routable, [van()], matrix, today=TODAY)
    solution = solve(problem, 400, 0)

    assert len(solution.routes) == 1, "one bike is enough for three stops"
    assert solution.objective_breakdown["distance"] == 2 * 3 * km, (
        "out to the far stop and back is 6 km; any other visit order is "
        "longer, so a longer answer means distance stopped being priced")


def test_route_duration_is_a_hard_limit_and_not_merely_the_shift():
    """§7.4: "The shift duration, not the 35-envelope limit, is the binding
    constraint... The solver must enforce route duration as a hard
    constraint." `INV-6` makes `max_duration` hard."""
    problem, _ = build([package("P1")])

    assert problem.vehicles[0].max_duration == 57600 - 28800


def test_the_reader_accepts_a_vehicle_written_to_the_document_s_field_names():
    """§9.1's Vehicles table is the contract, so the reader must accept it.

    `capacity_count` became `capacity_envelopes` when the definition went to
    v0.10, and this reader kept reading the old name. Every test passed, because
    every fixture carried the old name too -- the suite was checking the reader
    against itself. Building the record from the table rather than from memory
    is what makes the next rename fail here instead of in production.
    """
    fields = contract_fields("**Vehicles**", "**Facilities**")
    assert "capacity_envelopes" in fields, (
        "§9.1 no longer declares capacity_envelopes; this test is reading the "
        "wrong table or the contract has moved again")

    record: dict = {name: "X" for name in fields}
    record.update(vehicle_id="BIKE-1", type="motorbike", role="delivery",
                  capacity_envelopes=35, capacity_mailbags=0,
                  capacity_weight_g=35_000,
                  shift_start=28_800, shift_end=57_600)

    # `type` now carries weight: §4.1 gives capacity per type, so the model is
    # what the reader consults and the row is what cross-checks it.
    vehicle = contract._vehicle(record, "HUB", contract.load_model())

    assert vehicle.id == "BIKE-1"
    assert vehicle.capacities[contract.COUNT] == 35

    # And the cross-check has to be reading the name the document publishes.
    # Capacity moved to the model, so a renamed column no longer breaks the
    # call -- `_capacities` reads it with `.get`, a miss skips silently, and
    # the model's number wins unchallenged. That is the "one silently wins"
    # the cross-check exists to prevent, so the contradiction is raised here
    # from §9.1's own field name rather than from a literal.
    contradicting = dict(record, capacity_envelopes=34)
    with pytest.raises(ValueError, match="capacity_envelopes"):
        contract._vehicle(contradicting, "HUB", contract.load_model())


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


# --------------------------------------------------------------------------
# The model file is authoritative, not decorative
# --------------------------------------------------------------------------
# `models/ddn-lastmile.json` and this mapping both used to state what a stop
# costs and what an envelope weighs. Two sources for one fact is the failure
# this repository's platform refuses everywhere else: edit the model, and
# nothing happened. The structural facts now come from the model through
# `servicemodel.build`, and the mapping overlays only what a model file cannot
# say -- which is the priority decision, the role, and the override.


def test_the_model_decides_what_a_stop_costs():
    """The test that makes the model real. Change it, and the Problem changes;
    if this passes while the model is ignored, it is measuring nothing."""
    model = dict(contract.load_model(), service={"fixed_seconds": 999})
    routable, _ = contract.triage([package("P1")], today=TODAY)
    problem = contract.to_problem(HUB, routable, [van()], matrix_for(2),
                                  today=TODAY, model=model)

    assert problem.orders[0].delivery.service_fixed == 999


def test_the_model_decides_what_an_envelope_counts_as():
    """§4.1 needs a count *and* a weight, and `quantity` carries a list."""
    model = contract.load_model()
    dimensions = {q["dimension"] for q in model["quantity"]}

    assert dimensions == {contract.COUNT, contract.WEIGHT}


def test_the_overlay_touches_only_the_fields_it_declares():
    """The discipline that stops two builders drifting apart.

    `servicemodel.build` owns the order; this mapping changes a named few
    fields on top. Anything else it changed would be a fact stated twice
    again, so the set is declared and checked rather than remembered.
    """
    from vrp import servicemodel

    model = contract.load_model()
    routable, _ = contract.triage([package("P1", priority=1800)], today=TODAY)
    records = [contract.as_record(p) for p in routable]
    before = servicemodel.build(model, [contract.as_depot(HUB)], records,
                                matrix_for(2)).orders[0]
    after = contract.to_problem(HUB, routable, [van()], matrix_for(2),
                                today=TODAY, model=model).orders[0]

    changed = {f for f in vars(before) if getattr(before, f) != getattr(after, f)}
    assert changed <= contract.OVERLAID, (
        f"the overlay changed {sorted(changed - contract.OVERLAID)}, which "
        "`servicemodel.build` already decided from the model")


def test_the_declared_overlay_is_not_wider_than_the_work():
    """A declared set that lists fields nobody touches would pass the test
    above while meaning nothing."""
    assert contract.OVERLAID == {"priority_tier", "prize", "priority_source",
                                 "required_skills"}


def test_a_package_with_no_window_still_leaves_the_service_time_to_the_model():
    """The narrow half of the overlay.

    `delivery` carries the service time as well as the window, and they are
    owned by different things: §7.4's ten minutes is an operation fact the
    model states, §7.3's "any time during the shift" is per-facility data it
    cannot know. Touching the whole field would let this mapping re-decide the
    first while fixing the second.

    HUB's shift equals the model's declared window, so this fixture cannot tell
    the two apart on the window alone -- which is why it asserts on
    `service_fixed` and the shifted case below carries the window.
    """
    from vrp import servicemodel

    model = contract.load_model()
    routable, _ = contract.triage([package("P1")], today=TODAY)
    records = [contract.as_record(p) for p in routable]
    before = servicemodel.build(model, [contract.as_depot(HUB)], records,
                                matrix_for(2)).orders[0]
    after = contract.to_problem(HUB, routable, [van()], matrix_for(2),
                                today=TODAY, model=model).orders[0]

    assert after.delivery.service_fixed == before.delivery.service_fixed
    assert after.delivery.location_id == before.delivery.location_id


def test_a_package_with_a_window_changes_the_window_and_nothing_else():
    """§7.3's exceptional case, kept to the one field it is about."""
    from vrp import servicemodel

    model = contract.load_model()
    windowed = package("P1", time_window_start=32400, time_window_end=39600)
    routable, _ = contract.triage([windowed], today=TODAY)
    records = [contract.as_record(p) for p in routable]
    before = servicemodel.build(model, [contract.as_depot(HUB)], records,
                                matrix_for(2)).orders[0].delivery
    after = contract.to_problem(HUB, routable, [van()], matrix_for(2),
                                today=TODAY, model=model).orders[0].delivery

    changed = {f for f in vars(before) if getattr(before, f) != getattr(after, f)}
    assert changed == contract.OVERLAID_STOP
    assert after.service_fixed == before.service_fixed


# --------------------------------------------------------------------------
# Capacity belongs to the vehicle type (§4.1), so the model owns it
# --------------------------------------------------------------------------
# The model's `fleet` section carried 35 envelopes / 35,000 g and so did every
# §9.1 vehicle record, and the mapping read the record. The model's numbers
# were therefore decoration that looked authoritative -- the same silent no-op
# the last refactor removed from the order side, still present on the fleet
# side and harder to see because the two agreed.
#
# §4.1 settles it: "Motorbike | 35 envelopes", "Van | 500 kg". Capacity is a
# fact about the *type*. The record repeats it per row because the published
# contract has a column for it, which makes the record a cross-check rather
# than a second source.


def test_the_model_decides_what_a_motorbike_carries():
    """Change the model, and the fleet changes. If this passes while the
    mapping reads the record, it is measuring nothing."""
    model = contract.load_model()
    model["fleet"] = [dict(model["fleet"][0],
                           capacities={"envelopes": 20, "grams": 20_000})]
    routable, _ = contract.triage([package("P1")], today=TODAY)
    problem = contract.to_problem(
        HUB, routable,
        [van(capacity_envelopes=20, capacity_weight_g=20_000)],
        matrix_for(2), today=TODAY, model=model)

    assert problem.vehicles[0].capacities == {"envelopes": 20, "grams": 20_000}


def test_a_record_that_contradicts_the_model_is_refused_naming_both():
    """The reason the record is not simply ignored.

    Silently preferring either one turns a data error into a plan: a bike
    loaded to 35 when its box holds 20, or vice versa. §4.1 gives one answer
    per type, so a row that disagrees is wrong somewhere and a planner needs
    to know where rather than have it chosen for them.
    """
    with pytest.raises(ValueError) as refusal:
        build([package("P1")], vehicles=[van(capacity_envelopes=20)])

    message = str(refusal.value)
    assert "M1" in message or "V1" in message
    assert "20" in message and "35" in message


def test_a_record_that_agrees_with_the_model_is_accepted():
    problem, _ = build([package("P1")])

    assert problem.vehicles[0].capacities == {"envelopes": 35, "grams": 35_000}


def test_a_vehicle_type_the_model_does_not_describe_is_refused():
    """§4.1 has motorbikes doing last mile exclusively, so a van here is a
    mistake worth naming rather than a capacity lookup that returns nothing."""
    with pytest.raises(ValueError, match="van"):
        build([package("P1")], vehicles=[van(type="van")])


def test_route_duration_comes_from_the_vehicle_s_own_shift():
    """Unlike capacity, a shift is per vehicle -- §9.1 gives every row its own
    start and end -- so §7.4's hard route duration is the record's to decide
    and the model says nothing about it."""
    problem, _ = build([package("P1")],
                       vehicles=[van(shift_start=28800, shift_end=43200)])

    assert problem.vehicles[0].max_duration == 43200 - 28800
    assert "max_duration" not in contract.load_model()["fleet"][0]


def test_a_vehicle_missing_a_capacity_the_contract_declares_is_refused():
    """The hole the other session's rename exposed, closed at its source.

    The cross-check read the row with `.get`, so a column that was absent --
    or renamed, which is indistinguishable from absent at this level -- was a
    silent skip, and the model's number won unchallenged. That is precisely
    the "one of the two silently wins" the cross-check exists to prevent.

    §9.1 is explicit about optionality: `linehaul_release_at` is marked
    "datetime, optional" and the capacity columns are not. So a vehicle row
    without one is malformed against the published contract, and saying so is
    better than planning around it.
    """
    record = {k: v for k, v in van().items() if k != "capacity_envelopes"}

    with pytest.raises(ValueError, match="capacity_envelopes"):
        build([package("P1")], vehicles=[record])


def test_the_refusal_points_at_the_contract_rather_than_at_the_reader():
    """A field name in an error is only actionable if the reader knows where
    it was supposed to come from."""
    record = {k: v for k, v in van().items() if k != "capacity_weight_g"}

    with pytest.raises(ValueError) as refusal:
        build([package("P1")], vehicles=[record])

    assert "§9.1" in str(refusal.value)


def test_the_lowest_priority_envelope_outweighs_a_long_detour():
    """Why `PRIZE_SCALE` exists, as a property rather than a number.

    Bounded above as well as below: scaled against the p99 instead of the
    median, a prize outweighs the shift too, and the solver returns routes
    running past the end of the day. See the constant for the measurement.

    A priority score and a metre are different units and the objective adds
    them. Measured over real Costa Rica road distances, serving one more
    envelope costs about 12,400 at p99. Unscaled, §8.1's lowest band loses to
    almost any detour and the solver declines work with bikes standing idle --
    the opposite of §8's first objective. Scaled, §7.4's shift time is what
    binds, which is what §7.4 says should bind.
    """
    MEDIAN_MARGINAL_COST = 2_349    # 2 x median nearest-neighbour road metres
    P99_MARGINAL_COST = 12_400      # the exceptional detour, from the constant

    problem, _ = build([package("L", priority=40)])
    prize = problem.orders[0].prize

    assert prize > MEDIAN_MARGINAL_COST, (
        "§8's first objective is maximising delivered; an envelope that loses "
        "to a typical detour is one the solver declines with bikes idle")

    # The upper bound this docstring has claimed since it was written, and did
    # not assert. Scaled to the p99 the prize outweighs the shift as well, and
    # the measurement in `assumptions.PRIZE_SCALE` records what that does:
    # at scale 1,000 the D5 solve goes INFEASIBLE and serves 0 of 186.
    assert prize < P99_MARGINAL_COST, (
        "a prize large enough to outweigh any detour is large enough to "
        "outweigh the shift, and §7.4 makes the shift a hard bound")


def test_the_delivery_window_follows_the_facility_shift():
    """§7.3: "Deliveries are any time during the shift."

    The shift is per-facility data (§9.1 gives every vehicle its own, and §3.1
    every depot its own release), so a model cannot know it. Moving the
    structural facts onto `servicemodel.build` let the model's declared
    `windows` win instead, and a facility working 06:00-14:00 against a model
    window of 08:00-16:00 lost two hours at each end — six usable hours of an
    eight-hour shift, silently, in every route.

    Found by measuring where a route's time went: spans clustered at 6.2 h and
    the arithmetic said 8 h, which is also why capacity looked half what §7.4
    expects.
    """
    early = dict(HUB, shift_start=21600, shift_end=50400)     # 06:00-14:00
    routable, _ = contract.triage([package("P1")], today=TODAY)
    problem = contract.to_problem(early, routable,
                                  [van(shift_start=21600, shift_end=50400)],
                                  matrix_for(2), today=TODAY)
    window, = problem.orders[0].delivery.time_windows

    assert (window.start, window.end) == (21600, 50400)
