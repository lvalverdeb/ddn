"""§10's day, simulated, and §11 measured over it.

**Reproducing §10 is a wiring check, not a validation.** The outcome rates are
read off §10's own end of day, so a delivered total is a restatement of the
document; what it demonstrates is that seven stages hand work to each other
without losing any. Nothing here measures the operation.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from ddn import assumptions
from ddn.allocation import EFFECTIVE_PER_BIKE
from ddn.model import travel as road
from ddn.simulation import Rates, State, render, run_day, run_days
from ddn.simulation.capacity import check, hub_throughput
from ddn.simulation.metrics import Tally, measure
from ddn.solver_adapter import postcheck
from tests.fixtures import peak_day
from tests.matrices import road_matrix, rows

HOUR = 3600
FLEET = peak_day.MOTORBIKES





@pytest.fixture(scope="module")
def inputs():
    day = peak_day.load()
    midnight = datetime.combine(day.collection_day, datetime.min.time())

    def secs(moment):
        return int((moment - midnight).total_seconds())

    facilities = [{"id": f.facility_id, "lat": f.lat, "lon": f.lon,
                   "route_release_time": f.route_release_time.hour * HOUR,
                   "transit_from_hub_min": f.transit_from_hub_min,
                   "shift_start": 7 * HOUR, "shift_end": 15 * HOUR}
                  for f in day.facilities]

    pools: dict[str, list] = {}
    for e in peak_day.morning_pool():
        pools.setdefault(e.facility_id, []).append(
            {"package_id": e.package_id, "customer_id": e.customer_id,
             "lat": e.lat, "lon": e.lon, "status": "Ready",
             "geocode_confidence": "high", "priority": float(e.priority),
             "sla_date": e.sla_date.isoformat(), "facility_id": e.facility_id,
             "attempt_number": e.attempt_number,
             "customer_lat": e.lat, "customer_lon": e.lon})

    requests = [{"mailbag_id": b.mailbag_id, "customer_id": b.customer_id,
                 "lat": r.lat, "lon": r.lon,
                 "requested_at": secs(r.requested_at),
                 "expected_weight_g": b.expected_weight_g,
                 "envelope_count": b.envelope_count}
                for r in day.requests for b in r.mailbags]
    inflow = [{"package_id": e.package_id, "mailbag_id": e.mailbag_id,
               "package_type": e.package_type, "facility_id": e.facility_id,
               "lat": e.lat, "lon": e.lon, "status": "Ready",
               "geocode_confidence": "high", "priority": float(e.priority),
               "sla_date": e.sla_date.isoformat(),
               "customer_id": e.customer_id} for e in day.ready()]
    vans = [{"vehicle_id": v.vehicle_id, "type": "van", "role": v.role.value,
             "capacity_mailbags": v.capacity_mailbags,
             "capacity_weight_g": v.capacity_weight_g,
             "shift_start": secs(v.shift_start),
             "linehaul_release_at": (None if v.linehaul_release_at is None
                                     else secs(v.linehaul_release_at))}
            for v in day.vehicles if v.type.value == "van"]
    bikes = [v.vehicle_id for v in day.vehicles if v.type.value == "motorbike"]

    # Two road tables over the same recording, because the day asks two
    # different questions of it. §5.1's legs run hub-to-site and are looked up
    # by coordinate — a pickup van's position is wherever the last bag was.
    # §5.3.2's circuits run depot-to-depot and are looked up by id. Both come
    # from the gateway (`tests/matrices.py`); neither is computed here.
    #
    # The network is real and the places are not, so nothing below measures
    # this operation's geography — §3.1 still does not supply it.
    points = [facilities[0], *requests]
    return {"facilities": facilities, "bikes": bikes, "vans": vans,
            "requests": requests, "inflow": inflow,
            "travel": road.over(road_matrix(points), road.index_of(points)),
            "transit": road.between(road_matrix(facilities), rows(facilities)),
            "allocation": peak_day.BIKE_ALLOCATION,
            "_pools": {f: tuple(p) for f, p in pools.items()},
            "_day": day}


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


#: §10 says all 4,550 are positioned. On real road travel 4,140 are: the
#: pickup vans cannot reach every site and return before §5.1.6's cut-off when
#: the legs are the length roads actually make them. The gap is the document's
#: to reconcile, and it is recorded rather than smoothed over.
POSITIONED_ON_ROADS = 4140


def test_not_everything_section_10_positions_reaches_the_hub(simulated):
    """§10 positions 4,550; real roads position 4,140.

    The difference is §5.1.6's cut-off meeting travel that is 1.4x the
    crow-flies distance — the same factor `tests/matrices.py` records between
    the hub and D1. §10's figure is reachable only on travel that understates
    by forty per cent, which is what this repository used to compute.
    """
    report, _ = simulated
    positioned = sum(report.positioned.values())
    assert positioned == POSITIONED_ON_ROADS
    assert positioned < peak_day.READY == 4550
    assert peak_day.READY - positioned == 410, "bags that missed the cut-off"
    assert report.rolled == {}, "and nothing that reached the hub missed a van"


def test_every_facility_gets_no_more_than_section_10_allots_it(simulated):
    """What does arrive is still sorted where §10 sorts it."""
    report, _ = simulated
    for facility, count in report.positioned.items():
        assert count <= peak_day.READY_BY_FACILITY[facility]


def test_tomorrows_pool_exceeds_the_fleet_by_about_seventeen_hundred(simulated):
    """§10's closing line: 4,690 against 120 × 25 = 3,000."""
    _, tomorrow = simulated
    capacity = FLEET * EFFECTIVE_PER_BIKE
    assert capacity == 3000
    # Smaller than §10's 4,690, because 410 bags never reached the hub — and
    # still half again what the fleet can serve.
    assert tomorrow.pool_size == 4440
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
    assert rates.delivered == assumptions.DELIVERED_PER_MILLE == 935
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
    """Nine corrections, eight transfers — §7.1 refuses the ninth.

    MRN-01761's SLA is the delivery day itself, so §9.1's
    min(receiving depot's next morning release, SLA date) lands on a deadline
    that has already passed. §7.1 forbids raising a transfer that cannot make
    it, so that envelope goes back through §5.5 instead of onto a van.
    """
    report, _, seen = corrected
    assert len(seen) == 9
    assert report.transfers_raised == 8


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
    assert report.transfers_carried == 8
    at = {e["package_id"]: (facility, e["facility_id"])
          for facility, pool in tomorrow.pools.items() for e in pool}
    moved = {p: at[p] for p in seen if at.get(p) == ("D2", "D2")}
    assert len(moved) == 8, "carried transfers are pooled at the destination"
    assert at["MRN-01761"] == ("D1", "D1"), "the refused one never left"


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
