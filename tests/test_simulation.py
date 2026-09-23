"""§10's day, simulated, and §11 measured over it.

**Reproducing §10 is a wiring check, not a validation.** The outcome rates are
read off §10's own end of day, so a delivered total is a restatement of the
document; what it demonstrates is that seven stages hand work to each other
without losing any. Nothing here measures the operation.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta

import pytest

from ddn import assumptions, linehaul, returns
from ddn.allocation import EFFECTIVE_PER_BIKE
from ddn.linehaul import circuit
from ddn.linehaul.circuit import Declined
from ddn.model import Status, TransferReason, TransferRequest
from ddn.simulation import Rates, State, render, run_day, run_days
from ddn.simulation.capacity import check, hub_throughput
from ddn.simulation.metrics import Metrics, Tally, measure
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

    # And 130 is itself §10's floor, not its answer: it pools five depots'
    # bikes. Per depot the shortfall is 170, because D2's 38 envelopes of
    # slack and D3's 2 cannot reach D4, D5 and D6 — §4.2 fixes a bike's depot
    # before the solve, which is the whole reason it is two-stage.
    # `docs/spec-proposals/v0.16-depot-capacity.md` proposes the correction.
    per_depot = sum(max(peak_day.MORNING_BY_FACILITY[d]
                        - peak_day.BIKE_ALLOCATION[d] * EFFECTIVE_PER_BIKE, 0)
                    for d in depots)
    assert per_depot == 170

    report, _ = simulated
    assert report.tally.unassigned > 20, "so more than D1's twenty fall out"


def test_the_day_delivers_about_section_10s_total(simulated):
    """§10 v0.14: 2,750 delivered, 2,950 dispatched.

    **This said 2,880 within 6% for four revisions after §10 stopped saying
    it.** v0.13 restated the end of day and the band was wide enough to hold
    both figures, so nothing moved — and by the time §6.1's end-of-day clock
    landed, 2,711 was 5.9% from the old target and one rounding from red
    against a number the document no longer carried.

    Retargeted to what §10 states, and tightened to 2% because the day is now
    within 1.4% of it. The remaining gap is the 170 above: §10 dispatches
    2,950 and this day dispatches 2,910, because the depots §10 says clear
    their pools cannot.
    """
    report, _ = simulated
    assert report.tally.delivered == pytest.approx(2750, rel=0.02)
    assert report.tally.dispatched == pytest.approx(2950, abs=50)
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
    """§6: held at the facility in Ready state, retried **while the SLA holds**.

    "While" is the whole of it, and this test used to ignore it: every
    postponement came back, including the ones whose SLA date was today and
    which therefore had no next attempt. They sat in tomorrow's pool until the
    following morning's sweep found them, a day late, in a pool they could not
    be dispatched from. §6.1 returns them tonight instead, so the count here is
    the postponements that still have a day, not all of them.
    """
    report, tomorrow = simulated
    retried = [e for pool in tomorrow.pools.values() for e in pool
               if e.get("previous_outcome") == "Postponed"]

    assert len(retried) == report.tally.postponed - _spent(report)
    assert retried, "a day with no retry at all would make the rest vacuous"
    assert all(e["status"] == "Ready" for e in retried)
    assert all(e["attempt_number"] >= 1 for e in retried)


def _spent(report):
    """Postponements §6.1 gave no further day, counted off the report.

    `tally.sla_expired` carries both halves — the sweep at dispatch and this —
    and on the seeded day the first is zero, so the difference is exactly the
    postponements that ran out tonight.
    """
    return report.tally.sla_expired


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
    assert report.transfers_offered == 0
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
    assert report.transfers_offered == 10


def test_transfer_van_hours_are_the_added_leg_and_not_the_requests(inputs):
    """§8.3: transfers "**add to it**" -- the legs they add, once.

    The plan-level property is pinned in `tests/test_transfers.py`; this is
    the same claim through `run_day`, because the subtraction that produces it
    happens there and a per-request charge would be invisible from the plan.
    One correction to D2 buys VAN-02 the detour through D2; the tenth rides
    the leg the first one bought. A charge of c seconds per request reports
    ten times what it reports for one, and a charge for the whole circuit
    reports the trip rather than the difference.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}

    def run_with_at_most(cap: int):
        moved: list[str] = []

        def regeocode(envelope):
            if envelope["facility_id"] != "D1" or len(moved) >= cap:
                return None
            moved.append(envelope["package_id"])
            return "D2"

        report, _ = run_day(State(day=day.delivery_day, pools=inputs["_pools"]),
                            seed=7, regeocode=regeocode, **kwargs)
        return report

    one, ten = run_with_at_most(1), run_with_at_most(10)

    assert (one.transfers_carried, ten.transfers_carried) == (1, 10), (
        "both nights carried what they raised, or the equality below is only "
        "saying the planner refused the extra nine")
    assert one.tally.transfer_van_seconds > 0, (
        "the first correction to D2 added a leg VAN-02 would not have flown")
    assert ten.tally.transfer_van_seconds == one.tally.transfer_van_seconds, (
        f"ten transfers added {ten.tally.transfer_van_seconds}s and one added "
        f"{one.tally.transfer_van_seconds}s; both nights added the same leg")
    assert ten.tally.transfer_van_seconds < ten.tally.van_seconds, (
        "the marginal is a part of the night's van-hours, not the night")


def test_every_transfer_is_carried_or_declined(corrected):
    """§9.2 accounts for each one; none may simply vanish between stages."""
    report, _, _ = corrected
    assert (report.transfers_carried + len(report.transfers_declined)
            == report.transfers_offered)


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


def test_a_correction_that_cannot_arrive_in_time_sends_the_envelope_back():
    """§7.1: "A transfer is not raised for an envelope that cannot reach the
    destination before its SLA date; **it goes to the return run instead**."

    The second clause is the one this used to skip. `_raise_transfers`
    `continue`d, so the envelope stayed in tomorrow's pool at a depot its own
    corrected address says is the wrong one — and §5.3.2's sentence is a pair:
    "transferred if it can reach the correct depot before its SLA date;
    otherwise it is returned to the customer via the hub".

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
               "sla_date": "2026-09-16", "lat": 9.99, "lon": -84.11,
               "status": "Postponed"}

    raised, refused, staying = _raise_transfers(
        _Doorstep({}, {}, [expired], [], 0, 0),
        [{"id": "D3", "route_release_time": 7 * 3600}],
        today=date(2026, 9, 16), regeocode=lambda _envelope: "D3")

    assert raised == [], "its deadline is already behind it"
    assert [e["package_id"] for e in refused] == ["PKG-1"]
    assert refused[0]["sla_expired"] is True, (
        "§5.5's gate reads this; without it the envelope reaches the return "
        "run and is filtered straight back out")
    assert returns.goes_back(refused[0])
    assert staying == [], (
        "an envelope going back must leave the pool; it used to be in both")


def test_the_sla_bound_is_the_end_of_the_date_and_not_its_start():
    """§6.1's date is a day an envelope may be delivered on, not a moment.

    The test above is the pair to this one and neither is redundant. An
    envelope due *tomorrow* arrives at the corrected depot at tomorrow's 07:00
    release and is delivered during tomorrow, inside its SLA. Read `sla_date`
    as the instant the date *begins* and its expiry falls at tomorrow 00:00 --
    seven hours before the arrival -- and §5.3.2 returns it to the customer
    instead. Every other envelope gets the same verdict under both readings,
    which is why `tests/fixtures/peak_day_transfers.py:187` took the end of the
    date and `simulation/day.py` took the start for two whole tasks without a
    single test going red. This is the one draw that tells them apart.
    """
    from ddn.simulation.day import _Doorstep, _raise_transfers

    due_tomorrow = {"package_id": "PKG-2", "facility_id": "D1",
                    "previous_outcome": "Postponed",
                    "postponed_reason": "incorrect address",
                    "sla_date": "2026-09-17", "lat": 9.99, "lon": -84.11,
                    "status": "Postponed"}

    raised, refused, staying = _raise_transfers(
        _Doorstep({}, {}, [due_tomorrow], [], 0, 0),
        [{"id": "D3", "route_release_time": 7 * 3600}],
        today=date(2026, 9, 16), regeocode=lambda _envelope: "D3")

    assert [t.package_id for t in raised] == ["PKG-2"], (
        "due tomorrow, arriving tomorrow morning: inside its SLA")
    assert refused == []
    assert [e["status"] for e in staying] == [str(Status.TRANSFER_REQUESTED)]
    assert raised[0].deadline == datetime.combine(date(2026, 9, 17), time(7)), (
        "§9.1's min resolves to the release: the SLA runs to the end of the "
        "17th and the arrival is 07:00 on the 17th, so the release binds")


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
            == report.transfers_offered)


def test_the_report_renders_the_transfer_block(corrected):
    """§5.3.2 is a stage of the day, so it reports with the others.

    §8.3's two lines are read off the page rather than off `Metrics`, because
    the renderer is where a share can be printed against the wrong figure and
    still look right: 0.93 h is what the night added, and 0.8% is that against
    the van-hours the day *used*. A share against hours available would print
    0.8% of a different denominator and read identically.
    """
    report, _, _ = corrected
    page = render(report)
    assert "TRANSFERS (§5.3.2)" in page
    assert "raised" in page and "carried" in page

    hours = report.tally.transfer_van_seconds / 3600
    assert f"{hours:.2f}" == "0.93"
    assert "van-hours added (§8.3)" in page and "0.93" in page
    share = report.metrics.transfer_van_hour_share
    assert share == pytest.approx(report.tally.transfer_van_seconds
                                  / report.tally.van_seconds)
    assert f"{share * 100:.1f}%" == "0.8%"


def test_a_declined_transfers_reason_is_printed_whole(inputs):
    """§9.2 asks for the reason, and the reason is the whole sentence.

    "no inter-depot transit supplied; §3.1 gives none" is a gap in this run's
    *inputs*; "no van circuit reaches the destination depot tonight" is an
    operational outcome. Truncated to a column width they begin to look alike,
    and the day below reports both at once.
    """
    report, _ = _day_without_transit(
        inputs, lambda e: "D3" if e["facility_id"] != "D3" else None)
    page = render(report)

    # Unwrapped: the renderer breaks long reasons across lines with a hanging
    # indent, so the page is compared with its line breaks and their following
    # indent collapsed back to single spaces.
    flat = " ".join(page.split())
    for reason in set(report.transfers_declined.values()):
        assert reason in flat, f"{reason!r} is not on the page in full"
    assert "15 ×" in page and "9 ×" in page, (
        "each reason is reported with how many requests it refused")


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

    The breach is built from §5.5's return queue: depot rejects waiting at D1,
    a kilo each, riding home on tonight's circuit. It used to be built by
    inflating the day's inflow, but §5.3.2's competition now holds hub-origin
    load to the 500 kg itself, so that route yields a capped leg and a rolled
    remainder rather than a violation. Returns are what the planner cannot
    refuse -- §5.3.2 ranks hub-origin loads against transfers and says nothing
    about returns -- so they are the honest way to a §7.1 breach.
    """
    kwargs = dict(inputs)
    for key in [k for k in kwargs if k.startswith("_")]:
        del kwargs[key]
    day = inputs["_day"]
    # The customer site rides along: §5.5 returns to the sender, and `_return_run`
    # refuses to guess it for an envelope that arrives home without one.
    waiting = tuple({"package_id": f"RET-{n}", "facility_id": "D1",
                     "weight_g": 1000, "customer_id": "C-RET",
                     "customer_lat": 40.4, "customer_lon": -3.7}
                    for n in range(600))

    report, _ = run_day(State(day=day.delivery_day, pools=inputs["_pools"],
                              returns_queue=waiting),
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
             "sla_date": "2026-09-30", "lat": 9.99, "lon": -84.11,
             "status": "Postponed"}
    doorstep = _Doorstep({}, {}, [moved], [], 0, 0)

    raised, _, _ = _raise_transfers(
        doorstep, [{"id": "D3"}], today=date(2026, 9, 16),
        regeocode=lambda _envelope: "D3")

    assert len(raised) == 1
    assert raised[0].deadline.time() == assumptions.ROUTE_RELEASE


# ----------------------------------- the oracle `tests/e2e/test_chain.py` uses

# §10's day threaded from an *empty* start: day D collects and positions,
# day D+1 delivers what day D left. Every other test in this file seeds
# `State.pools` from the fixture and gets a day that does both at once, which
# is the right shape for asking what a day does and the wrong one for asking
# whether three slices reproduce it — `run_day` is `_attempt` -> `_doorstep` ->
# `_raise_transfers` -> `_collect`, so a single call delivers a pool that the
# same call is still collecting.
#
# The chain compares against these two calls, so the figures below are pinned
# *here*, in the simulator's own suite, and the chain reads them off the report
# at runtime instead of restating them. A chain that disagrees is wrong; a
# refactor that moves one of these is also wrong. Do not re-pin to match.

EMPTY_START_POSITIONED = {"HUB": 1368, "D1": 767, "D2": 605,
                          "D3": 500, "D4": 400, "D5": 320, "D6": 180}


def _threaded(inputs, **kwargs):
    """§10's day as two `run_day` calls, never `run_days(2, ...)`.

    `run_days` returns only the reports and drops the `State` between them
    (`day.py:600-606`), and the chain needs the intermediate night.
    """
    day = inputs["_day"]
    forward = {k: v for k, v in inputs.items() if not k.startswith("_")}
    collected, night = run_day(State(day=day.collection_day, pools={}),
                               seed=7, **forward)
    delivered, tomorrow = run_day(night, seed=7, **kwargs, **forward)
    return collected, night, delivered, tomorrow


def test_the_peak_day_threaded_from_an_empty_start(inputs):
    """What the two days answer, as literals, so the chain need not guess.

    These are §10's day *as this simulator answers it* — not §10's own
    published figures, which `tests/fixtures/peak_day.py` carries and which
    this run does not reproduce (see the deviations noted below). Moving one of
    these numbers is a statement about the simulator or the fixture and belongs
    in a commit that says which.
    """
    day = inputs["_day"]
    collected, night, delivered, tomorrow = _threaded(inputs)

    # Day D: nothing to deliver, because nothing was positioned before it.
    # Two equalities rather than `< something`: an unbounded guard is how this
    # repository last shipped a test that zero would have passed.
    assert collected.day == day.collection_day
    assert collected.tally.dispatched == 0
    assert collected.tally.ready_pool == 0
    assert collected.positioned == EMPTY_START_POSITIONED
    assert sum(collected.positioned.values()) == 4140
    assert collected.tally.received == 4550
    assert collected.uncollected == 410          # §5.1.6: bags no van reached
    assert collected.carried_into_tomorrow == 4140

    # The night carries exactly what was positioned, in the same buckets.
    assert {f: len(p) for f, p in night.pools.items()} == EMPTY_START_POSITIONED
    assert night.returns_queue == ()

    # Day D+1 delivers it. 3,000 is the fleet's own ceiling — 120 bikes at
    # `EFFECTIVE_PER_BIKE` — not a figure about the pool, which is 4,140.
    assert delivered.day == day.delivery_day
    assert delivered.tally.ready_pool == 4140
    assert delivered.tally.dispatched == FLEET * EFFECTIVE_PER_BIKE == 3000
    assert delivered.outcomes == {"Delivered": 2794, "Postponed": 121,
                                  "Rejected": 51, "Returned": 34}
    assert delivered.postponed_reasons == {"recipient unavailable": 56,
                                           "driver out of time": 38,
                                           "incorrect address": 27}
    assert len(delivered.unassigned) == 1140
    assert {u.reason for u in delivered.unassigned} == {"count"}
    assert delivered.return_stops == 15
    assert delivered.violations == ()

    # Two deviations from §10, recorded rather than smoothed over.
    #
    # First: day D+1 is handed the same `inflow` again, so it collects the same
    # bags a second time. `carried_into_tomorrow` is therefore the re-collected
    # 4,140 *plus* the 1,261 the delivery left behind, not the 1,261 alone.
    assert delivered.carried_into_tomorrow == 5392
    assert sum(delivered.positioned.values()) == 4140
    assert (delivered.carried_into_tomorrow - sum(delivered.positioned.values())
            == len(delivered.unassigned) + delivered.outcomes["Postponed"]
               - delivered.tally.sla_expired
            == 1252)

    # Second: every envelope here is on its first attempt, so three tally
    # fields that look independent carry no information.
    #
    # §6.1's expiry does bite, in one place: nine envelopes whose SLA date was
    # today were postponed, which leaves them no day to be retried on, so they
    # go back tonight rather than sitting in tomorrow's pool. Eight are at
    # depots and reach `returns_queue`; the ninth is hub-resident, which
    # `day.py` filters out of it.
    assert delivered.tally.sla_expired == 9
    assert (delivered.tally.delivered
            == delivered.tally.delivered_first_attempt
            == delivered.tally.delivered_within_sla == 2794)
    assert sum(len(p) for p in tomorrow.pools.values()) == 5392
    assert len(tomorrow.returns_queue) == 60


def test_a_recording_deliver_and_rates_change_nothing_about_the_day(inputs):
    """`deliver` and `rates` are public, so the chain can watch without altering.

    `tests/e2e/test_chain.py` needs per-package outcomes, which `DayReport`
    does not publish — it publishes counts. Rather than add an accessor to the
    simulator, which Task 14 forbids ("do not adjust the simulator to match"),
    the chain passes a `deliver` that records what it was offered and returns
    it unchanged, and a `Rates` that records what it drew and returns it
    unchanged.

    This test is what makes that safe. It compares two independent `run_day`
    runs — plain against instrumented — and is not a value compared against
    itself.

    **What it could not see, and now can.** `DayReport` publishes counts, and
    `day.py` filters hub rejects out of `returns_queue`, so a `deliver` that
    merely *reordered* the pool left `report` and `next_state` byte-identical
    while changing which envelope got which outcome — measured, by swapping
    two hub envelopes. The chain reds on that (A2, A3, A6) and this test did
    not, so it reds second, not first, and the chain's expected side rested on
    a recording nothing had checked. So the sequence itself is now pinned: a
    second instrumented run must reproduce the same ordered list of
    (package_id, facility), which an instrument that reorders cannot do.
    """
    _, _, plain_d, plain_t = _threaded(inputs)

    drawn: list[str] = []
    attempted: list[tuple[dict, str]] = []

    class Recording:
        """Everything `Rates` does, plus a note of what `draw` returned."""

        def __init__(self, inner):
            self._inner = inner

        def draw(self, rng):
            outcome = self._inner.draw(rng)
            drawn.append(outcome)
            return outcome

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def spy(offered, facility, bikes):
        attempted.extend((envelope, facility) for envelope in offered)
        return list(offered), []

    _, _, watched_d, watched_t = _threaded(
        inputs, deliver=spy, rates=Recording(Rates()))

    assert watched_d == plain_d, "a recording deliver changed the day's report"
    assert watched_t == plain_t, "a recording deliver changed tomorrow's state"

    # The sequence, not just its size. Two instrumented runs of the same day
    # must offer the same envelopes to the same facilities in the same order;
    # an instrument that reorders passes every cardinality check and fails
    # this one.
    seen = list(attempted)
    attempted.clear()
    drawn.clear()
    _threaded(inputs, deliver=spy, rates=Recording(Rates()))

    assert [(e["package_id"], f) for e, f in attempted] == [
        (e["package_id"], f) for e, f in seen]

    # And the instruments saw the whole day, not a sample of it.
    assert len(attempted) == plain_d.tally.dispatched == len(drawn)
    assert len({e["package_id"] for e, _ in attempted}) == len(attempted)
    assert Counter(drawn) == plain_d.outcomes
    assert Counter(f for _, f in attempted)["HUB"] == 1200


# ------------------------------- §5.4 on real geography, statically checked

def test_the_on_road_runner_calls_only_names_that_exist():
    """`on_road.py` needs a gateway and a graph, so no test runs it — and it
    called `lastmile.allocate`, which has never existed.

    §4.2's allocation lives in `ddn.allocation`; the day-one runner would have
    raised `AttributeError` after spawning OSRM and building a 2.5-million-cell
    matrix. Nothing caught it because nothing imports the module.

    So this resolves every `module.attribute(...)` the file calls, statically.
    It needs no routing data, and it is the whole class of error rather than
    the one instance: a module the suite cannot execute still has names that
    must exist.
    """
    import ast
    import importlib
    import pathlib

    source = pathlib.Path("ddn/simulation/on_road.py").read_text()
    tree = ast.parse(source)

    imported = {alias.asname or alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import | ast.ImportFrom)
                for alias in node.names}
    packages = {name: importlib.import_module(f"ddn.{name}")
                for name in imported & {"allocation", "assumptions", "contract",
                                        "lastmile", "linehaul", "model",
                                        "pickups", "processing", "returns"}}
    assert packages, "no ddn package imported; this test would prove nothing"

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in packages):
            module = packages[node.func.value.id]
            assert hasattr(module, node.func.attr), (
                f"on_road.py:{node.lineno} calls "
                f"{node.func.value.id}.{node.func.attr}(), which does not "
                f"exist in {module.__name__}")


def test_every_row_of_section_11_is_a_field_of_metrics():
    """The document's table and the dataclass, held to each other.

    §11 gained a twelfth row when §6 gained the Cancelled outcome in v0.15,
    and `Metrics` was not given the field — so it answered eleven of twelve
    and three prose copies of "eleven" went on agreeing with each other.
    Counting the rows rather than restating the number is what stops the next
    row going the same way.

    `NOT_SECTION_11` is named rather than subtracted by count: a field that
    answers a different section is allowed, but it has to be written down
    here, so the next §11 row cannot arrive by hiding behind it.
    """
    import pathlib
    from dataclasses import fields

    #: §8.3 asks for van-hours "including inter-depot legs for transfers".
    #: That is a ratio, and `metrics.py` is where ratios are derived from
    #: summed counts -- but it is not one of §11's rows.
    not_section_11 = {"transfer_van_hour_share"}

    spec = pathlib.Path("docs/vrp-problem-definition.md").read_text()
    table = spec.split("## 11.")[1].split("## 12.")[0]
    rows = [line for line in table.splitlines()
            if line.startswith("|") and "---" not in line][1:]

    named = {f.name for f in fields(Metrics)}
    assert not_section_11 <= named, (
        f"{not_section_11 - named} is excused from §11's count but is not a "
        "field of Metrics; delete it here rather than leaving a hole")
    answering = named - not_section_11
    assert len(rows) == len(answering), (
        f"§11 has {len(rows)} rows and Metrics answers {len(answering)} of "
        f"them ({sorted(answering)})")
    assert len(rows) == 12


def test_a_metric_nothing_counted_is_none_and_not_zero():
    """`metrics.py`'s own rule, applied to the one that broke it.

    "A metric whose inputs the day did not produce is `None` rather than
    zero." Nothing sets `Tally.disputed`, so the reconciliation discrepancy
    rate was 0.0 on every day — §10 has ten discrepancies, and 0.0 reads as a
    clean day rather than as an uncounted one.
    """
    assert measure(Tally(received=100)).reconciliation_discrepancy_rate is None
    assert measure(Tally(received=100, disputed=10)
                   ).reconciliation_discrepancy_rate == 0.1


def test_a_transfer_no_circuit_could_carry_waits_for_tomorrow(inputs):
    """§5.3.2: "Requests arising after departures wait for the next day's plan."

    `DayReport.transfers_declined` counted them and `State` had nowhere to put
    them, so a declined request evaporated: the envelope stayed where it was —
    correctly — but the correction had to be rediscovered by the envelope
    being postponed again for the same reason, which is a different envelope's
    worth of luck each night.
    """
    from ddn.simulation.day import RETRIABLE_DECLINES, _still_waiting

    day = inputs["_day"]
    waiting = TransferRequest(
        transfer_id="TR-WAIT", package_id="PKG-W",
        from_facility_id="D1", to_facility_id="D2",
        reason=TransferReason.ADDRESS_CORRECTION,
        created_at=datetime.combine(day.collection_day, time()),
        deadline=datetime.combine(day.delivery_day, time()),
        priority=100.0)

    class _Night:
        declined = (Declined("TR-WAIT", linehaul.NO_VAN),)

    assert linehaul.NO_VAN not in RETRIABLE_DECLINES, (
        "NO_VAN is the depot's envelopes, not a transfer's; the transfer "
        "reasons are circuit.NO_VAN_LEG and circuit.OVER_CAPACITY")

    class _Reached:
        declined = (Declined("TR-WAIT", linehaul.NO_VAN_LEG),)

    holding = {"D1": ({"package_id": "PKG-W"},)}

    assert _still_waiting([waiting], _Reached(), holding) == (waiting,)
    assert _still_waiting([waiting], _Night(), holding) == ()
    assert _still_waiting([waiting], _Reached(), {"D1": ()}) == (), (
        "§6.1's clock reached the envelope while it waited for a van, so the "
        "request is spent: asking tomorrow to move something that has left "
        "the operation is a queue entry nothing can ever clear")


def test_a_transfer_that_cannot_meet_its_deadline_is_not_queued_for_ever():
    """§9.1 fixes a transfer's deadline when it is raised.

    So one that cannot arrive tonight cannot arrive tomorrow either, and
    queueing it would build a queue that never empties. Those envelopes go
    back by `_raise_transfers`' refusal instead — the same sentence of §5.3.2
    answering both halves.
    """
    from ddn.simulation.day import RETRIABLE_DECLINES

    assert circuit.MISSES_DEADLINE not in RETRIABLE_DECLINES
    assert RETRIABLE_DECLINES == {circuit.NO_VAN_LEG, circuit.OVER_CAPACITY}


def test_a_transfer_no_night_can_carry_goes_back_to_the_customer(inputs):
    """§5.3.2: "otherwise it is returned to the customer via the hub".

    The rule was applied at raising and not at declining, so a request the
    plan refused for `MISSES_DEADLINE` dropped out of the queue and left its
    envelope at the depot it does not belong to — for ever, since §9.1 fixes
    the deadline at raising and no later night can beat one tonight missed.
    """
    from ddn.simulation.day import _refused_transfers, _still_waiting

    day = inputs["_day"]

    def request(suffix: str, package: str) -> TransferRequest:
        return TransferRequest(
            transfer_id=f"TR-{suffix}", package_id=package,
            from_facility_id="D1", to_facility_id="D2",
            reason=TransferReason.ADDRESS_CORRECTION,
            created_at=datetime.combine(day.collection_day, time()),
            deadline=datetime.combine(day.delivery_day, time()),
            priority=100.0)

    late, waiting = request("LATE", "PKG-L"), request("WAIT", "PKG-W")
    tomorrow = {"D1": ({"package_id": "PKG-L"}, {"package_id": "PKG-W"}),
                "D2": ({"package_id": "PKG-O"},)}

    class _Night:
        declined = (Declined("TR-LATE", circuit.MISSES_DEADLINE),
                    Declined("TR-WAIT", circuit.NO_VAN_LEG))

    kept, going_back = _refused_transfers(tomorrow, [late, waiting], _Night())

    assert [e["package_id"] for e in going_back] == ["PKG-L"]
    assert going_back[0]["sla_expired"] is True, (
        "§6.1's flag is what `returns.goes_back` reads; without it the "
        "envelope reaches the return run and is filtered straight back out")
    assert kept == {"D1": ({"package_id": "PKG-W"},),
                    "D2": ({"package_id": "PKG-O"},)}, (
        "it left tomorrow's pool and nothing else moved — in tonight's return "
        "load and in tomorrow's pool at once is the defect `_handover` "
        "already records having had once"
    )
    assert _still_waiting([late, waiting], _Night(), kept) == (waiting,), (
        "the two halves of §5.3.2's sentence partition tonight's declines: "
        "what tomorrow can answer waits, the rest goes home")


@pytest.fixture(scope="module")
def refused(inputs):
    """§10's day run twice, differing only in whether §3.1's table exists.

    `circuit.choose` refuses every transfer when no inter-depot transit is
    supplied, and that refusal is not retriable: tomorrow has the same table,
    which is none. So this is the decline §5.3.2's "otherwise" is for, and it
    is reachable without bending the fixture -- `run_day`'s `transit` defaults
    to `None`, so a deployment that has not wired the gateway is *already*
    here, raising transfers every night and refusing every one of them.

    The run *with* the table is the control, not decoration: it carries the
    same envelopes to the same depot, so the difference between the two runs
    is the decline and nothing else about the day.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}

    def moved(envelope):
        """One depot's corrections, so the transfers are the day's own."""
        return "D2" if envelope["facility_id"] == "D1" else None

    def go(**override):
        return run_day(State(day=day.delivery_day, pools=inputs["_pools"]),
                       seed=7, **{**kwargs, **override}, regeocode=moved)

    return go(transit=None), go()


def test_a_refused_transfer_leaves_the_pool_and_queues_for_the_return_run(
        refused):
    """Both halves, because either alone is the bug the other one is.

    §5.5 puts it in the queue and not on tonight's stops: it is at a depot,
    and `returns.sites` returns what is at the hub. It rides in tomorrow.
    """
    from ddn.simulation.day import RETRIABLE_DECLINES

    (report, tomorrow), (control, carried_on) = refused

    assert report.transfers_offered and not report.transfers_carried
    assert len(report.transfers_declined) == report.transfers_offered
    assert not set(report.transfers_declined.values()) & RETRIABLE_DECLINES, (
        "the property this run turns on is that tomorrow cannot answer the "
        "decline; the wording of the reason is circuit.py's business")
    assert control.transfers_carried == control.transfers_offered, (
        "the control must differ by the decline alone; if the table-less run "
        "is not the only one refusing, this test is measuring something else")

    stranded = {e["package_id"] for pool in carried_on.pools.values()
                for e in pool} - {e["package_id"]
                                  for pool in tomorrow.pools.values()
                                  for e in pool}
    assert len(stranded) == report.transfers_offered, (
        "every refused envelope left tomorrow's pool, and only those")

    going_home = {e["package_id"] for e in tomorrow.returns_queue}
    assert stranded <= going_home, (
        "absent from the pool and absent from the queue is an envelope "
        "destroyed rather than returned")
    assert all(e["sla_expired"] is True for e in tomorrow.returns_queue
               if e["package_id"] in stranded), (
        "§6.1's flag is what `returns.goes_back` reads; without it the "
        "envelope reaches the return run and is filtered straight back out")
    assert not stranded & {t.package_id for t in tomorrow.transfers}, (
        "§9.1 fixes the deadline at raising; requeueing it builds a queue "
        "that never empties")

    assert carried_on.pool_size - tomorrow.pool_size == len(stranded), (
        "exactly those envelopes left the operation by this route")
    assert (len(going_home)
            - len({e["package_id"] for e in carried_on.returns_queue})
            == len(stranded)), "and exactly those joined the return queue"


def test_a_waiting_transfer_is_offered_to_the_next_nights_plan(inputs):
    """The queue is seeded into tomorrow, ahead of what tomorrow raises.

    Yesterday's requests have already waited a day, so they are offered first
    when van-hours are short — which is the only thing "wait for the next
    day's plan" can mean beyond being remembered.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    held = TransferRequest(
        transfer_id="TR-HELD", package_id="PKG-H",
        from_facility_id="D1", to_facility_id="D2",
        reason=TransferReason.ADDRESS_CORRECTION,
        created_at=datetime.combine(day.collection_day, time()),
        deadline=datetime.combine(day.delivery_day, time(23)),
        priority=100.0)

    report, _ = run_day(State(day=day.delivery_day, pools=inputs["_pools"],
                              transfers=(held,)),
                        seed=7, transit=kwargs.pop("transit"), **kwargs)

    assert (report.transfers_carried
            + len(report.transfers_declined)) >= 1, (
        "the held request reached tonight's plan as carried or declined; "
        "neither means State.transfers is not being seeded")


# ---------------------- §5.2.6's transfer edges, taken where the diagram draws

@pytest.fixture(scope="module")
def corrected_day(inputs):
    """§10's day with every postponed address re-geocoded to D3.

    `regeocode` is the caller's — the simulator has no ground truth to correct
    *to* — so this is what a day with corrections looks like, and it is the
    only path on which §5.3.2's first trigger fires at all.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    return run_day(State(day=day.delivery_day, pools=inputs["_pools"]), seed=7,
                   regeocode=lambda e: "D3" if e["facility_id"] != "D3" else None,
                   **kwargs)


def test_a_declined_transfer_leaves_its_envelope_not_routable(corrected_day):
    """§7.1: "an envelope in transfer is not routable until it arrives".

    That is a statement about status, and until §5.2.6's edge was taken there
    was nothing for a check to read — the envelope sat in tomorrow's pool
    stamped `Ready`, indistinguishable from one whose depot is correct. Now a
    transfer no circuit carried leaves it `Transfer requested`, which
    `postcheck._across_the_day` refuses on sight.
    """
    report, tomorrow = corrected_day
    pool = [e for facility in tomorrow.pools.values() for e in facility]
    waiting = [e for e in pool if e.get("status") == "Transfer requested"]

    assert report.transfers_offered > report.transfers_carried, (
        "every transfer was carried, so nothing is left waiting to check")
    assert waiting, "a declined transfer left its envelope routable"
    assert len(waiting) == report.transfers_offered - report.transfers_carried


def test_a_carried_transfer_arrives_ready_at_the_new_depot(corrected_day):
    """§5.2.6 ends a transfer at "Ready (at new depot)", and §7.1 makes it
    routable the moment it arrives — so the facility and the status move
    together, never the facility alone."""
    _, tomorrow = corrected_day
    arrived = [e for e in tomorrow.pools.get("D3", ())
               if e.get("previous_outcome") == "Postponed"
               and e.get("status") == "Ready"]

    assert arrived, "nothing reached D3"
    assert all("Transfer requested" != e.get("status") for e in arrived)


def test_an_envelope_going_back_is_not_also_in_tomorrows_pool(inputs):
    """The double-count the branch closed.

    `_raise_transfers` returned the envelopes it refused *and* left them in
    `doorstep.postponed`, which `_handover` then placed — so one envelope was
    in tonight's return load and tomorrow's pool at once. §10's day never hits
    the branch, because `regeocode` is `None` there, so nothing caught it.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}

    _, tomorrow = run_day(
        State(day=day.delivery_day, pools=inputs["_pools"]), seed=7,
        regeocode=lambda e: "D3" if e["facility_id"] != "D3" else None,
        **kwargs)

    in_pool = {e["package_id"] for facility in tomorrow.pools.values()
               for e in facility}
    going_back = {e["package_id"] for e in tomorrow.returns_queue}

    assert not in_pool & going_back


# --------- §7.1's "not routable", at the offering and not only at the check


def _held_over(inputs, *, weight_g: int, sla_date: str | None = None):
    """§10's day cut down to one depot holding an envelope with a transfer out.

    Seeded through `State` rather than by reaching into `_attempt`, because
    `State` is the hand-over itself: a pool holding the envelope *and* the
    request no circuit could carry, in one object, is precisely what §5.3.2's
    "requests arising after departures wait for the next day's plan" leaves on
    the table. Yesterday is the thing under test, so yesterday is what the test
    supplies.

    The envelope is copied off §10's own D1 pool so its coordinates, priority
    and customer are a real row rather than invented ones, and three of its
    neighbours ride along -- an empty round would satisfy "PKG-WAIT was not
    offered" for the wrong reason.

    The collection side is left whole. §5.3.2 raises a transfer "onto an
    existing or added van leg", so a day with no hub-to-depot load has no
    circuit for one to ride and every request is declined `NO_VAN_LEG` --
    which would leave the carried case untestable and the declined ones
    passing for a reason that is not the one under test.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}

    at_d1 = inputs["_pools"]["D1"]
    held = dict(at_d1[0], package_id="PKG-WAIT", facility_id="D1",
                status=Status.TRANSFER_REQUESTED.value,
                sla_date=sla_date or at_d1[0]["sla_date"])
    pending = TransferRequest(
        transfer_id="TR-PKG-WAIT", package_id="PKG-WAIT",
        from_facility_id="D1", to_facility_id="D2",
        reason=TransferReason.ADDRESS_CORRECTION,
        created_at=datetime.combine(day.collection_day, time()),
        deadline=datetime.combine(day.delivery_day, time(23)),
        weight_g=weight_g, priority=float(held["priority"]))

    offered: list[str] = []

    def spy(pool, _facility, _bikes):
        offered.extend(e["package_id"] for e in pool)
        return list(pool), []

    state = State(day=day.delivery_day, transfers=(pending,),
                  pools={"D1": (held, *at_d1[1:4])})
    report, tomorrow = run_day(state, seed=7, deliver=spy, **kwargs)
    return report, tomorrow, offered


def test_an_envelope_awaiting_a_transfer_is_never_offered_to_the_round(inputs):
    """§7.1: "an envelope in transfer is not routable until it arrives at the
    destination depot".

    That is a rule about what the day offers, not only about what the
    post-check refuses afterwards, and §6's table starts the state at the
    raise: "a transfer request is raised (§5.3.2) and the envelope enters *In
    transfer* until it arrives". Sending a rider to the address the correction
    already ruled wrong is the operational reading of getting this wrong.

    **And it must still be there tomorrow.** Holding it back without a bucket
    would have destroyed it: `_handover` builds tomorrow out of what was
    positioned, what stayed after a doorstep attempt, and what capacity
    declined, and an envelope in none of those three is in none of them.
    """
    report, tomorrow, offered = _held_over(inputs, weight_g=600_000)

    assert offered, "nothing was offered at all, so the round did not run"
    assert "PKG-WAIT" not in offered, (
        "§7.1 says this envelope is not routable from D1 today")
    assert "PKG-WAIT" in {e["package_id"] for pool in tomorrow.pools.values()
                          for e in pool}, "held off the round, then dropped"
    assert not any(u.package_id == "PKG-WAIT" for u in report.unassigned), (
        "§10 pins the unassigned count, and §7.1's hold is not §8's "
        "'the bikes could not take it'")


def test_a_waiting_transfer_never_outlives_the_envelope_it_moves(inputs):
    """A request whose envelope has left the operation is a queue entry nothing
    can ever clear.

    `RETRIABLE_DECLINES` excludes `MISSES_DEADLINE` to avoid exactly this, and
    the everyday route to the same place was one level up: the envelope was
    offered to the round, delivered from the depot its own correction says is
    wrong, and its request went on being re-offered every night against nothing.
    `transfer_id` is `TR-{package_id}`, so the other end of the same draw --
    postponed again -- raised a second request the first could not be told from.

    Two days, because one cannot show a queue failing to empty.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}

    at_d1 = inputs["_pools"]["D1"]
    held = dict(at_d1[0], package_id="PKG-WAIT", facility_id="D1",
                status=Status.TRANSFER_REQUESTED.value)
    pending = TransferRequest(
        transfer_id="TR-PKG-WAIT", package_id="PKG-WAIT",
        from_facility_id="D1", to_facility_id="D2",
        reason=TransferReason.ADDRESS_CORRECTION,
        created_at=datetime.combine(day.collection_day, time()),
        deadline=datetime.combine(day.delivery_day, time(23)),
        weight_g=600_000, priority=float(held["priority"]))

    state = State(day=day.delivery_day, transfers=(pending,),
                  pools={"D1": (held, *at_d1[1:4])})
    for _ in range(2):
        _, state = run_day(state, seed=7, **kwargs)
        assert state.transfers, (
            "the queue emptied, so the next day would assert about nothing")
        holds = {e["package_id"] for pool in state.pools.values() for e in pool}
        orphans = [t.transfer_id for t in state.transfers
                   if t.package_id not in holds]
        assert not orphans, (
            f"{orphans} ask tomorrow to move envelopes tomorrow does not hold")
        assert len({t.transfer_id for t in state.transfers}) == len(
            state.transfers), "two requests under one id, and §5.3.2 has one"


def test_a_carried_transfer_takes_the_envelope_it_held_back(inputs):
    """§5.2.6 ends the transfer at "Ready (at new depot)".

    The envelope held off today's round is the *common* case for this: a
    retriable decline is a request that waits and is then carried, so the
    hand-over has to route a waiting envelope by the same rule as one that
    simply stayed. Reading only the stayers leaves this one at D1 with its
    circuit already run -- and it would leave no other trace, because the count
    of transfers carried would be right.
    """
    _, tomorrow, offered = _held_over(inputs, weight_g=200)

    assert "PKG-WAIT" not in offered
    moved = [e for e in tomorrow.pools.get("D2", ())
             if e["package_id"] == "PKG-WAIT"]
    assert moved, "the circuit carried it and the pool did not"
    assert moved[0]["status"] == Status.READY.value, (
        "§7.1 makes it routable on arrival, so the status moves with it")
    assert not any(t.package_id == "PKG-WAIT" for t in tomorrow.transfers), (
        "it has arrived; there is nothing left for tomorrow's plan to do")


def test_an_expired_envelope_takes_its_transfer_request_with_it(inputs):
    """§6.1's clock reaches a waiting envelope too, and the request is spent.

    An envelope can wait for a van until its SLA passes; then it goes back
    whatever the transfer wanted. That is a real outcome and §5.5 already has
    the envelope, so the request is dropped rather than carried -- the decline
    reason is still retriable, which is why filtering on the reason alone is
    not enough.
    """
    yesterday = (inputs["_day"].delivery_day - timedelta(days=1)).isoformat()
    _, tomorrow, _ = _held_over(inputs, weight_g=600_000, sla_date=yesterday)

    assert "PKG-WAIT" in {e["package_id"] for e in tomorrow.returns_queue}, (
        "§6.1 sweeps it whether or not a transfer was waiting on it")
    assert not any(t.package_id == "PKG-WAIT" for t in tomorrow.transfers), (
        "a request to move an envelope that has left the operation is a "
        "queue entry nothing can ever clear")


# ------------------- §9.2's ledger: the three ways a decline ends, counted apart

def _day_without_transit(inputs, regeocode):
    """§10's day with §3.1's inter-depot table withheld.

    The one shape that declines for two reasons at once: origins off the
    destination's circuit are refused retriably, and the rest are refused
    because nothing can say how far apart two depots are. A day with a single
    reason cannot tell a ledger that defers everything from one that returns
    everything.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items()
              if not k.startswith("_") and k != "transit"}
    return run_day(State(day=day.delivery_day, pools=inputs["_pools"]),
                   seed=7, regeocode=regeocode, **kwargs)


def test_every_decline_is_deferred_or_returned_or_spent_exactly_once(inputs):
    """§9.2 accounts for each declined request once, and `DayReport` counts
    the three outcomes separately rather than deriving one by subtraction.

    The sum is a *check* here, not a definition: `transfers_deferred` reads
    the queue handed to tomorrow, `transfers_returned` reads what §5.5 took,
    and `transfers_spent` is computed on its own. A field defined as "the
    declines the other two did not claim" would satisfy this equality however
    wrong the other two were.

    Both arms have to fire on the same day for that to mean anything, which
    is why the transit table is withheld: 9 requests wait for tomorrow and 15
    go home, on one night, under two different reasons.
    """
    report, tomorrow = _day_without_transit(
        inputs, lambda e: "D3" if e["facility_id"] != "D3" else None)

    assert (report.transfers_deferred, report.transfers_returned,
            report.transfers_spent) == (9, 15, 0)
    assert len(report.transfers_declined) == (
        report.transfers_deferred + report.transfers_returned
        + report.transfers_spent)
    assert len(set(report.transfers_declined.values())) == 2, (
        "both refusal reasons fired; with one reason the split below could be "
        "produced by keying on the reason alone")

    # Disjointness by identity and not by the arithmetic above: an envelope
    # cannot both be waiting for tomorrow's van and be riding today's return
    # run, and two counts that summed correctly could still name one envelope
    # twice.
    waiting = {t.package_id for t in tomorrow.transfers}
    going_back = {e["package_id"] for e in tomorrow.returns_queue}
    assert len(waiting) == report.transfers_deferred
    assert not waiting & going_back


def test_a_decline_is_deferred_or_spent_by_whether_the_envelope_survived(inputs):
    """§6.1 reaches a waiting envelope, and the reason cannot tell you it did.

    Both runs decline one request for "the circuit is already at 500 kg" --
    retriable, so a ledger keyed on the reason defers both. The only
    difference is the SLA date on the envelope underneath: one is still in the
    operation and waits, the other has gone back on §5.5's run and the request
    is spent. That is why the sixth field exists rather than folding into
    `transfers_returned`: nothing on the *request* distinguishes these two.
    """
    yesterday = (inputs["_day"].delivery_day - timedelta(days=1)).isoformat()
    alive, _, _ = _held_over(inputs, weight_g=600_000)
    expired, gone, _ = _held_over(inputs, weight_g=600_000, sla_date=yesterday)

    assert (set(alive.transfers_declined.values())
            == set(expired.transfers_declined.values())), (
        "the two days must be told apart by the envelope and not the reason")
    assert (alive.transfers_deferred, alive.transfers_spent) == (1, 0)
    assert (expired.transfers_deferred, expired.transfers_spent) == (0, 1)
    assert "PKG-WAIT" in {e["package_id"] for e in gone.returns_queue}


def test_todays_deferred_transfers_are_tomorrows_backlog(inputs):
    """§5.3.2: requests that miss tonight "wait for the next day's plan".

    Offered is what the night was asked to carry and raised is what the day
    newly raised, so their difference is the backlog -- and it has to equal
    what yesterday deferred, or the queue is either dropping requests or
    re-raising them. Two days of the same fixture, chained through `State`.
    """
    day = inputs["_day"]
    kwargs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    def regeocode(envelope):
        return "D3" if envelope["facility_id"] != "D3" else None

    first, tomorrow = run_day(State(day=day.delivery_day, pools=inputs["_pools"]),
                              seed=7, regeocode=regeocode, **kwargs)
    second, _ = run_day(tomorrow, seed=7, regeocode=regeocode, **kwargs)

    assert first.transfers_deferred == 9
    assert len(tomorrow.transfers) == first.transfers_deferred
    assert second.transfers_offered - second.transfers_raised == (
        first.transfers_deferred), (
        f"{second.transfers_offered} offered less {second.transfers_raised} "
        f"raised is not yesterday's {first.transfers_deferred} deferred")
