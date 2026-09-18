"""§10's day, simulated, and §11 measured over it.

**Reproducing §10 is a wiring check, not a validation.** The outcome rates are
read off §10's own end of day, so a delivered total is a restatement of the
document; what it demonstrates is that seven stages hand work to each other
without losing any. Nothing here measures the operation.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ddn import assumptions
from ddn.allocation import EFFECTIVE_PER_BIKE
from ddn.model import travel as road
from ddn.simulation import Rates, State, render, run_day, run_days
from ddn.simulation.capacity import check, hub_throughput
from ddn.simulation.metrics import Tally, measure
from tests.fixtures import peak_day
from tests.matrices import road_matrix

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

    # §5.1's legs run hub-to-site, so the matrix spans exactly those. A fake:
    # `tests/matrices.py` says why the suite has no gateway, and what that
    # costs — nothing here measures geography.
    points = [facilities[0], *requests]
    return {"facilities": facilities, "bikes": bikes, "vans": vans,
            "requests": requests, "inflow": inflow,
            "travel": road.over(road_matrix(points), road.index_of(points)),
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
