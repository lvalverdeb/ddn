"""§10's day, simulated, and §11 measured over it.

**Reproducing §10 is a wiring check, not a validation.** The outcome rates are
read off §10's own end of day, so a delivered total is a restatement of the
document; what it demonstrates is that seven stages hand work to each other
without losing any. Nothing here measures the operation.
"""

from __future__ import annotations

from datetime import date

import pytest

from ddn import assumptions
from ddn.allocation import EFFECTIVE_PER_BIKE
from ddn.simulation import Rates, State, render, run_day, run_days
from ddn.simulation.capacity import check, hub_throughput
from ddn.simulation.metrics import Tally, measure
from ddn.solver_adapter import postcheck
from tests import peak_day_inputs
from tests.fixtures import peak_day

FLEET = peak_day.MOTORBIKES


@pytest.fixture(scope="module")
def inputs():
    """§10 as `run_day` arguments — built by `tests/peak_day_inputs.py`, which
    `tests/e2e/` shares so the chain and the simulator get the identical day.

    The `_`-prefixed keys are this file's own convention for "not a `run_day`
    argument"; `simulated` below strips them.
    """
    built = peak_day_inputs.build()
    return {**built.kwargs, "_pools": built.pools, "_day": built.day}


@pytest.fixture(scope="module")
def simulated(inputs):
    day = inputs["_day"]
    state = State(day=day.delivery_day, pools=inputs["_pools"])
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    return run_day(state, seed=7, **kwargs)


# ------------------------------------------------------------ reproducing §10

def test_d1_leaves_exactly_twenty_unassigned(simulated):
    """§10: "D1 24 × 25 = 600 vs 620 → 20 unassigned"."""
    report, _ = simulated
    dropped = {u.package_id for u in report.unassigned}
    at_d1 = sum(1 for e in peak_day.morning_pool()
                if e.facility_id == "D1" and e.package_id in dropped)
    assert at_d1 == 20


def test_section_10_cannot_clear_its_other_depots(simulated):
    """A second place §10 does not close, found by running it.

    §10 allocates 18 + 14 + 8 + 6 + 2 = 48 bikes to D2–D6, which is 1,200
    envelopes at ~25 each. Its morning pool is 3,100 and it puts 1,150 at the
    hub and 620 at D1, leaving 1,330 for those five depots — 130 more than
    their bikes can carry. So "other depots clear their pools" and the stated
    3,100 cannot both be true, whatever the fixture does with the split.
    """
    depots = ("D2", "D3", "D4", "D5", "D6")
    bikes = sum(peak_day.BIKE_ALLOCATION[d] for d in depots)
    capacity = bikes * EFFECTIVE_PER_BIKE
    share = peak_day.MORNING_POOL - sum(peak_day.MORNING_BY_FACILITY[f]
                                        for f in ("HUB", "D1"))
    assert bikes == 48
    assert capacity == 1200
    assert share == 1330
    assert share - capacity == 130, "§10 is 130 envelopes over its own fleet"

    report, _ = simulated
    assert report.tally.unassigned > 20, "so more than D1's twenty fall out"


def test_the_day_delivers_about_section_10s_total(simulated):
    """§10: 2,880 delivered. Within 6%, and low for a reason it names itself.

    The shortfall is the 130 above: §10 dispatches 3,080 and this day can only
    dispatch 2,910, because the depots §10 says clear their pools cannot. At
    §10's own delivery rate that is about 160 fewer envelopes, which is most of
    the gap.
    """
    report, _ = simulated
    assert report.tally.delivered == pytest.approx(2880, rel=0.06)
    assert report.tally.dispatched == peak_day.MORNING_POOL - report.tally.unassigned


def test_section_10s_ceiling_holds_and_names_where_the_rest_went(simulated):
    """§10 positions all 4,550. On real roads it cannot, and the test says why.

    This used to pin the figure: `positioned == 4140` and `READY - positioned
    == 410`. Two assertions of one fact — given READY is 4,550 they are the
    same equation — and both were the repository's own output, sensitive above
    all to `PROCESSING_CUTOFF`, which `docs/assumptions.md` labels **invented**.
    Moving that placeholder by half an hour reddened five tests and needed two
    edit-and-rerun rounds to settle, which is a test measuring its own inputs.

    What the spec actually supports is a ceiling and a conservation law. §10's
    4,550 bounds it. Everything under that ceiling is either positioned
    tonight, or on a bag §5.1.6 refused, or at the hub with no van — there is
    no fourth place. The identity holds at any cut-off; the *split* moves, and
    the split is what the placeholder owns.

    The shortfall itself is §5.1.6's working rule biting, which v0.13 states
    and flags "confirm — Open Question 12". §10's equality assumes travel that
    understates by about 40%: `tests/matrices.py` records hub→D1 as 11,551 m by
    road against 8,235 straight-line.
    """
    report, _ = simulated
    positioned = sum(report.positioned.values())

    assert positioned <= peak_day.READY == 4550, "§10's 4,550 is the ceiling"
    assert positioned + report.uncollected + report.rolled_envelopes == (
        peak_day.READY), (
        "every envelope §10 made Ready is positioned tonight, or on a bag "
        "§5.1.6 refused, or at the hub with no van — there is no fourth place")
    assert report.uncollected, (
        "on the recorded road table some bags cannot be collected and returned "
        "before the cut-off; zero here means the day is being routed on "
        "straight lines again (see 9a71a90)")


def test_every_facility_gets_no_more_than_section_10_allots_it(simulated):
    """What does arrive is still sorted where §10 sorts it."""
    report, _ = simulated
    for facility, count in report.positioned.items():
        assert count <= peak_day.READY_BY_FACILITY[facility]


def test_tomorrows_pool_exceeds_the_fleet_by_about_eighteen_hundred(simulated):
    """§10's closing line: 4,820 against 120 × 25 = 3,000, leaving ~1,800.

    v0.13 restated it — 4,690 and ~1,700 were v0.12's, before §10 accounted
    for D2–D6's 130 unassigned.
    """
    report, tomorrow = simulated
    capacity = FLEET * EFFECTIVE_PER_BIKE
    assert capacity == 3000
    # Short of §10's 4,820 by whatever §5.1.6 refused today, and still half
    # again what the fleet can serve. The gap is asserted as a gap rather
    # than as a total, so an invented cut-off moving does not red this.
    assert tomorrow.pool_size < peak_day.TOMORROW_POOL
    # And bounded below, because `<` alone would pass a day that lost the
    # pool entirely. §10 builds tomorrow's pool as positioned + unassigned
    # + postponed, so it cannot be under what was positioned today.
    assert tomorrow.pool_size > sum(report.positioned.values())
    assert tomorrow.pool_size > capacity * 1.4


def test_van_hours_are_the_gap_section_8_3_predicts(simulated):
    """§8.3: vans are "the most likely operational bottleneck".

    On straight-line travel this check read 108 hours wanted against 110
    available and passed, and `delivery` looked like the binding constraint. On
    road distances it wants 121 against the same 110 and binds hardest — which
    is what §8.3 predicts and what `docs/capacity-finding.md` found from three
    stages. The old answer was not a smaller version of this one; it pointed at
    a different bottleneck.
    """
    report, _ = simulated
    assert report.checks.vans.binds
    assert report.checks.delivery.binds
    assert report.checks.binding.name == "van-hours"
    assert report.checks.vans.required > report.checks.vans.available


# ------------------------------------------------------- the cycle, not one day

def test_a_chained_week_shows_the_backlog_growing(inputs):
    """§8.3's gap is structural, so it should accumulate rather than average."""
    day = inputs["_day"]
    state = State(day=day.delivery_day, pools=inputs["_pools"])
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    reports = run_days(3, state, seed=7, **kwargs)

    pools = [r.tally.ready_pool for r in reports]
    assert pools == sorted(pools), "each morning starts with more than the last"
    assert pools[-1] > pools[0]
    unassigned = [r.tally.unassigned for r in reports]
    assert unassigned[-1] > unassigned[0], "and leaves more behind"


def test_a_postponed_envelope_comes_back_tomorrow(simulated):
    """§6: held at the facility in Ready state, retried while the SLA holds."""
    report, tomorrow = simulated
    retried = [e for pool in tomorrow.pools.values() for e in pool
               if e.get("previous_outcome") == "Postponed"]
    assert len(retried) == report.tally.postponed
    assert all(e["status"] == "Ready" for e in retried)
    assert all(e["attempt_number"] >= 1 for e in retried)


def test_every_postponement_carries_one_of_section_6s_reasons(simulated):
    report, _ = simulated
    assert set(report.postponed_reasons) <= {
        "recipient unavailable", "incorrect address", "driver out of time"}
    assert sum(report.postponed_reasons.values()) == report.tally.postponed


def test_rejected_and_defective_envelopes_reach_the_return_run(simulated):
    """§5.5, fed by §6's outcomes rather than by a separate list."""
    report, _ = simulated
    assert report.return_stops > 0
    assert report.tally.rejected + report.tally.defective > 0


def test_the_same_seed_replays_the_same_day(inputs):
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    state = State(day=day.delivery_day, pools=inputs["_pools"])
    first, _ = run_day(state, seed=3, **kwargs)
    again, _ = run_day(state, seed=3, **kwargs)
    assert first.tally == again.tally
    assert first.outcomes == again.outcomes


def test_an_unchanged_allocation_moves_no_rider(simulated):
    """§7.2, reported rather than hidden."""
    report, _ = simulated
    assert report.allocation.moves == 0


# ------------------------------------------------------------------- the parts

def test_outcome_rates_must_account_for_every_envelope():
    with pytest.raises(ValueError, match="1000"):
        Rates(delivered=900, rejected=10, defective=10, postponed=10)


def test_the_default_rates_are_section_10s():
    rates = Rates()
    assert rates.delivered == assumptions.DELIVERED_PER_MILLE == 932
    assert (rates.delivered + rates.rejected + rates.defective
            + rates.postponed) == 1000


def test_a_metric_with_no_input_is_none_not_zero():
    """A day that never solved has no distance; 0 m would read as efficiency."""
    metrics = measure(Tally(delivered=10, bikes_deployed=2))
    assert metrics.distance_per_envelope_m is None
    assert metrics.solver_seconds is None
    assert metrics.envelopes_per_bike == 5


def test_tallies_add_so_a_week_is_not_an_average_of_averages():
    monday = Tally(dispatched=100, delivered=90, bikes_deployed=10)
    tuesday = Tally(dispatched=200, delivered=120, bikes_deployed=10)
    week = monday + tuesday
    assert week.delivered == 210
    assert measure(week).envelopes_per_bike == 10.5


def test_the_hub_is_capped_by_its_slowest_step():
    """§5.2 runs in series, and the clean room sees only part of the inflow."""
    all_assembly = hub_throughput(10, assembly_share=1.0)
    none_assembly = hub_throughput(10, assembly_share=0.0)
    assert all_assembly == assumptions.ASSEMBLY_PER_HOUR * 10
    assert none_assembly == assumptions.RECONCILE_PER_HOUR * 10
    assert all_assembly < none_assembly


def test_a_check_that_binds_is_named():
    checks = check(pool=5000, motorbikes=100, inflow=1000,
                   van_hours_available=100, pickup_hours=10,
                   linehaul_hours=10, processing_hours=10, assembly_share=0.2)
    assert checks.delivery.binds
    assert not checks.vans.binds
    assert checks.binding.name == "delivery"


def test_nothing_binds_when_everything_fits():
    checks = check(pool=100, motorbikes=100, inflow=100,
                   van_hours_available=100, pickup_hours=1,
                   linehaul_hours=1, processing_hours=10, assembly_share=0.1)
    assert checks.binding is None
    assert checks.tightest.load < 1


# -------------------------------------------------------------- the day report

def test_the_report_is_one_page_and_says_what_is_provisional(simulated, capsys):
    report, _ = simulated
    page = render(report)
    print(page)

    assert len(page.splitlines()) < 60, "one page"
    for heading in ("DELIVERY", "COLLECTION", "§11 METRICS",
                    "§8.3 CAPACITY CHECKS"):
        assert heading in page
    assert "provisional" in page
    assert "docs/capacity-finding.md" in page
    assert "no targets" in page, "§11's targets are [TBD] and stay that way"
    assert capsys.readouterr().out


def test_van_hours_available_are_the_fleet_s_own_shifts(simulated):
    """Supply is the vans' §9.1 shifts, not a number invented in the module.

    It was `len(routes) * 11 + len(trips) * 12` — two constants — and demand
    was summed from `returned_at`, a second of the day, so a van finishing at
    18:00 counted as eighteen hours worked. Both errors fed one comparison.
    """
    report, _ = simulated
    vans = 10
    longest_shift = 11  # §5.6: 07:00 to the processing cut-off
    assert report.checks.vans.available == pytest.approx(vans * longest_shift)


def test_the_van_check_is_over_capacity_on_real_roads(simulated):
    """The whole point of routing on roads rather than on a straight line.

    Demand was 108 hours against 110 available — two hours of slack — when the
    legs were computed as straight lines. On the recorded road table it is 121
    against 110. The fleet is not nearly enough; it was never nearly enough,
    and the arithmetic said otherwise because the distances were short.
    """
    report, _ = simulated
    assert report.checks.vans.load > 1.0
    assert report.checks.vans.required == pytest.approx(121, abs=1)
    assert -report.checks.vans.headroom == pytest.approx(11, abs=1)


# ---------------------------------------------- §5.3.2 wired through the day

@pytest.fixture(scope="module")
def corrected(inputs):
    """§6's "incorrect address" postponements at D1, re-geocoded to D2.

    The correction is the caller's: `run_day` has no ground truth to correct
    *to*, so it raises nothing without a `regeocode`. Restricting it to D1
    keeps the set deterministic — every transfer below is one envelope whose
    real address belongs to another depot.
    """
    day = inputs["_day"]
    state = State(day=day.delivery_day, pools=inputs["_pools"])
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    seen: list[str] = []

    def regeocode(envelope):
        if envelope["facility_id"] != "D1":
            return None
        seen.append(envelope["package_id"])
        return "D2"

    report, tomorrow = run_day(state, seed=7, regeocode=regeocode, **kwargs)
    return report, tomorrow, seen


def test_the_day_is_timed_on_real_inter_depot_road_travel(inputs):
    """§5.3.2's circuits read the gateway's table, not a constant.

    It was a 40-minute fake, and the two depots it stood for happen to be 40
    minutes apart — so the transfer figures below did not move when the real
    table replaced it. What the fake was hiding is the spread: D5 to D6 is six
    and a half hours on this network, and a circuit that thought it was forty
    minutes would promise an arrival before a release it cannot make.
    """
    transit = inputs["transit"]
    assert transit("D1", "D2") // 60 == 40
    assert transit("D2", "D1") // 60 == 43, "road travel is not symmetric"
    assert transit("D5", "D6") // 60 == 381


def test_a_day_with_no_regeocode_raises_no_transfer(simulated):
    """§5.3.2's trigger is an address *correction*, and inventing one would
    invent the trigger. The `simulated` run passes no `regeocode`."""
    report, _ = simulated
    assert report.transfers_raised == 0
    assert report.transfers_carried == 0
    assert report.transfers_declined == {}


def test_a_correction_that_changes_depot_raises_a_transfer(corrected):
    """Every correction that moves the facility raises a transfer.

    The count moves with §6's postponement rate, which v0.13 changed: ten
    corrections now where there were nine. What does not move is the rule —
    a correction whose deadline has already passed raises nothing, because
    §7.1 forbids a transfer that cannot arrive in time, and
    `test_a_transfer_that_cannot_make_its_deadline_goes_back_instead` in
    `test_transfers.py` pins that without depending on the draw.
    """
    report, _, seen = corrected
    assert len(seen) == 10
    assert report.transfers_raised == 10


def test_every_transfer_is_carried_or_declined(corrected):
    """§9.2 accounts for each one; none may simply vanish between stages."""
    report, _, _ = corrected
    assert (report.transfers_carried + len(report.transfers_declined)
            == report.transfers_raised)


def test_a_carried_transfer_starts_tomorrow_at_its_new_depot(corrected):
    """§5.2.6 ends a transfer at "Ready (at new depot)", and §7.1 keeps the
    envelope unroutable until it arrives — so the move shows up in tomorrow's
    pools, not today's."""
    report, tomorrow, seen = corrected
    assert report.transfers_carried == 10
    at = {e["package_id"]: (facility, e["facility_id"])
          for facility, pool in tomorrow.pools.items() for e in pool}
    moved = {p: at[p] for p in seen if at.get(p) == ("D2", "D2")}
    assert len(moved) == 10, "carried transfers are pooled at the destination"


def test_a_correction_that_cannot_arrive_in_time_raises_nothing():
    """§7.1: "A transfer is not raised for an envelope that cannot reach the
    destination before its SLA date; it goes to the return run instead."

    This used to be covered by the peak day happening to contain one such
    envelope — MRN-01761, whose SLA was the delivery day itself. v0.13's
    outcome rates moved the draw and that envelope stopped being postponed, so
    the rule's only test evaporated without failing. A rule worth §7.1 stating
    should not depend on which envelopes a sample happens to produce.
    """
    from ddn.simulation.day import _Doorstep, _raise_transfers

    expired = {"package_id": "PKG-1", "facility_id": "D1",
               "previous_outcome": "Postponed",
               "postponed_reason": "incorrect address",
               "sla_date": "2026-09-16", "lat": 9.99, "lon": -84.11}

    raised = _raise_transfers(
        _Doorstep({}, {}, [expired], [], 0, 0),
        [{"id": "D3", "route_release_time": 7 * 3600}],
        today=date(2026, 9, 16), regeocode=lambda _envelope: "D3")

    assert raised == [], "its deadline is already behind it"


def test_a_transfer_no_circuit_reaches_is_declined_with_a_reason(inputs):
    """§5.3.2 rides the *existing* line-haul; it does not add a van run.

    Sending every depot's corrections to D6 raises transfers whose origin is
    not on a circuit that goes on to reach D6, and those are refused rather
    than carried. The reason is reported, because a silent drop reads as a
    delivery that simply never happened.
    """
    day = inputs["_day"]
    state = State(day=day.delivery_day, pools=inputs["_pools"])
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    report, _ = run_day(state, seed=7, **kwargs,
                        regeocode=lambda e: "D6" if e["facility_id"] != "D6"
                        else None)

    assert report.transfers_declined, "not every origin is on a D6 circuit"
    assert set(report.transfers_declined.values()) == {
        "no van circuit reaches the destination depot tonight"}
    assert (report.transfers_carried + len(report.transfers_declined)
            == report.transfers_raised)


def test_the_report_renders_the_transfer_block(corrected):
    """§5.3.2 is a stage of the day, so it reports with the others."""
    report, _, _ = corrected
    page = render(report)
    assert "TRANSFERS (§5.3.2)" in page
    assert "raised" in page and "carried" in page


# ------------------------------------------- §5.5's day boundary for returns


def test_a_depot_rejection_waits_for_the_following_evening(inputs):
    """§5.5: "envelopes rejected at a secondary depot travel back to the hub on
    the van's return leg and join the *following* evening's return run".

    Every rejection used to be stamped `facility_id=hub_id` the instant the
    outcome was drawn, so a D3 refusal was at the hub before the van was, and
    went out on the same night's run. The envelope was never at D3 in the data
    at all, so `returns.eligible`'s own rule -- which this repository has always
    had a passing test for -- could never fire in the pipeline that feeds it.

    Two days, one seed. The D3 rejects are absent from day one's stops and
    present on day two's.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    only_d3 = {"D3": inputs["_pools"]["D3"]}

    first, tomorrow = run_day(State(day=day.delivery_day, pools=only_d3),
                              seed=7, **kwargs)

    queued = {e["package_id"] for e in tomorrow.returns_queue}
    assert queued, "a day of D3 deliveries produces some rejections"
    assert all(e["facility_id"] == "D3" for e in tomorrow.returns_queue), (
        "they are where they were refused, not at the hub")
    assert first.return_stops == 0, (
        "nothing was at the hub tonight, so nothing goes out tonight")

    second, _ = run_day(tomorrow, seed=7, **kwargs)

    assert second.return_stops > 0, "home on tonight's van, out on tonight's run"


def test_an_expired_envelope_is_returned_rather_than_dropped(inputs):
    """§6.1: past its SLA date it "is returned to the customer via the return
    run".

    `returns.eligible` reads `sla_expired`, and nothing set it — so an expired
    envelope was put on the pile going back and filtered straight out of it
    again. Not returned, not retried, not counted anywhere: destroyed.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    stale = [dict(e, sla_date="2000-01-01", facility_id="HUB")
             for e in inputs["_pools"]["HUB"][:5]]

    report, _ = run_day(State(day=day.delivery_day, pools={"HUB": stale}),
                        seed=7, **kwargs)

    assert report.tally.sla_expired == 5
    assert report.return_stops > 0, "§6.1 sends them back, so they are stops"


def test_the_day_reports_the_seven_one_breach_it_causes(inputs):
    """§13.1: "Every routing result carries its §7.1 violation list".

    `check_day_constraints` existed, was tested in isolation, and had no
    production caller at all — so the four bullets that span stages were never
    asked about a real plan. The simulator is where they can be asked: it is
    the only place the night's circuits, the vans' return times and the day's
    transfers exist together rather than as counts on a report.

    A kilo an envelope puts D1's share over 500 kg on its first leg, which is
    §7.1's combined-load bullet and nothing else.
    """
    kwargs = dict(inputs)
    for key in [k for k in kwargs if k.startswith("_")]:
        del kwargs[key]
    kwargs["inflow"] = [dict(e, weight_g=1000) for e in inputs["inflow"]]
    day = inputs["_day"]

    report, _ = run_day(State(day=day.delivery_day, pools=inputs["_pools"]),
                        seed=7, **kwargs)

    bullets = {v.bullet for v in report.violations}
    assert postcheck.COMBINED_LOAD in bullets, (
        f"a van over 500 kg on a leg is a §7.1 breach; reported {bullets}")


def test_a_day_within_its_limits_reports_nothing(simulated):
    """The other half: empty must mean checked and clean, not unchecked."""
    report, _ = simulated
    assert report.violations == ()


def test_a_facility_that_states_no_release_falls_back_to_the_registry():
    """§9.1's deadline is the receiving depot's next morning release.

    Where a facility row omits one, the fallback used to be a literal `7 *
    HOUR` sitting next to a registered `ROUTE_RELEASE` of exactly 07:00 — the
    audit's "registered, and then not used". Every facility in the peak-day
    fixture carries the registered value, so the literal and the registry were
    indistinguishable and the duplication could not fail a test. This one
    hands `_raise_transfers` a facility with no release at all, which is the
    only shape that tells them apart.

    The duplication guard could not have caught it either: `7 * HOUR` is a
    BinOp inside a comprehension, and `ROUTE_RELEASE` is a `time`, so neither
    side of it is something `_planted_literals` inspects.
    """
    from ddn.simulation.day import _Doorstep, _raise_transfers

    moved = {"package_id": "PKG-1", "facility_id": "D1",
             "previous_outcome": "Postponed",
             "postponed_reason": "incorrect address",
             "sla_date": "2026-09-30", "lat": 9.99, "lon": -84.11}
    doorstep = _Doorstep({}, {}, [moved], [], 0, 0)

    raised = _raise_transfers(
        doorstep, [{"id": "D3"}], today=date(2026, 9, 16),
        regeocode=lambda _envelope: "D3")

    assert len(raised) == 1
    assert raised[0].deadline.time() == assumptions.ROUTE_RELEASE
