"""§10's day, pinned number by number, before Task 9 moves allocation.

T9's fourth bullet asks that "the original peak-day fixture must produce
exactly the same numbers as before this task set". Nothing in this repository
could have answered that: the peak day is asserted in twenty places and every
one of them asserts a *fact about* it -- D1 leaves twenty unassigned, the van
check binds, a postponed envelope comes back -- so a change that moved the
delivered total by three and left every named fact standing would pass.

This file is the other kind of test. It asserts the whole output, and it is
written **before** `allocation/` gains a rebalancing decision, because after
that there is no way back: a baseline taken afterwards pins whatever the change
did rather than what was there before.

Three guards make it complete rather than merely long. `Tally`, `Metrics` and
`DayReport` are each checked field-for-field against their own `fields()`, so a
field added later fails here until someone states its peak-day value. That is
the point of the guard, and it has already done its work: T9 added six
transfer fields to `DayReport`, two van-hour counts to `Tally` and one share
to `Metrics`, and every one of them failed here first. A pin that quietly
ignored them would report "same numbers" about a smaller set of numbers than
it started with.

The figures are this repository's, not §10's. `tests/test_peak_day.py` holds
the fixture to the document; this holds the simulator to itself.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from ddn.simulation import State, run_day
from ddn.simulation.day import DayReport
from ddn.simulation.metrics import Metrics, Tally
from tests import peak_day_inputs

#: `tests/test_simulation.py` runs the same day at the same seed. Changing it
#: here changes nothing there -- they are separate runs of one fixture -- but
#: two seeds would make this file's numbers unrelatable to that file's.
SEED = 7

TALLY = {
    "ready_pool": 3100,
    "dispatched": 2910,
    "delivered": 2711,
    "delivered_first_attempt": 2361,
    "delivered_within_sla": 2711,
    "postponed": 116,
    "rejected": 49,
    "defective": 34,
    "sla_expired": 2,
    "cancelled": 0,
    "unassigned": 190,
    "received": 4550,
    "received_before_cut_off": 4550,
    "ready_by_cut_off": 4140,
    "disputed": 0,
    "pickups": 164,
    "pickup_wait_seconds": 770552,
    "bikes_deployed": 120,
    # No solver runs in this day, so §11's two solver-fed rows are zero here
    # and `measure` turns them into `None`. A non-zero distance would mean a
    # routed day, which is a different fixture.
    "distance_m": 0,
    "solver_seconds": 0.0,
    # §8.3's used side, in seconds: 121.29 van-hours of pickups and line-haul
    # against the 110 the fleet offers, which is why the van check binds here.
    # `_checks` derives the line-haul half from the same
    # `LinehaulPlan.van_seconds` the numerator below is measured against, so
    # the two are the same accounting.
    "van_seconds": 436643,
    # §5.3.2 raises nothing on this day, so no leg was flown for a transfer
    # and §8.3's "transfers **add to it**" adds nothing.
    "transfer_van_seconds": 0,
}

METRICS = {
    "pickup_responsiveness_s": 770552 / 164,
    "same_day_readiness": 4140 / 4550,
    "reconciliation_discrepancy_rate": None,
    "first_attempt_delivery_rate": 2361 / 2910,
    "postponement_rate": 116 / 2910,
    "unassigned_rate": 190 / 3100,
    "sla_compliance": 1.0,
    "sla_expiry_rate": 2 / (2711 + 49 + 34 + 2),
    "distance_per_envelope_m": None,
    "envelopes_per_bike": 2711 / 120,
    "solver_seconds": None,
    "cancellation_rate": 0.0,
    # 0.0 and not `None`: the denominator is 436,643 seconds of real van work,
    # so the day did answer the question -- the answer is that transfers cost
    # none of it. `None` would mean nobody asked.
    "transfer_van_hour_share": 0.0,
}

REPORT = {
    "return_stops": 28,
    "carried_into_tomorrow": 4444,
    "uncollected": 410,
    "rolled_envelopes": 0,
    # §5.3.2 never fires on this day: §10 has no re-geocoding, so no
    # correction changes a depot. The zeroes are what make this fixture the
    # control against which `peak_day_transfers` is read.
    "transfers_offered": 0,
    "transfers_raised": 0,
    "transfers_carried": 0,
    "transfers_deferred": 0,
    "transfers_returned": 0,
    "transfers_spent": 0,
    "outcomes": {"Delivered": 2711, "Postponed": 116,
                 "Rejected": 49, "Returned": 34},
    "postponed_reasons": {"driver out of time": 38,
                          "recipient unavailable": 53,
                          "incorrect address": 25},
    "positioned": {"HUB": 1368, "D1": 767, "D2": 605, "D3": 500,
                   "D4": 400, "D5": 320, "D6": 180},
    "rolled": {},
    "transfers_declined": {},
    "violations": (),
}

#: Not in `REPORT` because they are not compared by value: `unassigned` is a
#: tuple of records and `allocation` a tuple of 120, and pinning either whole
#: would fail on a field rename that moved no envelope.
UNASSIGNED = 190
BIKES_BY_FACILITY = {"HUB": 48, "D1": 24, "D2": 18, "D3": 14,
                     "D4": 8, "D5": 6, "D6": 2}
MOVES = 0

TOMORROW = {"HUB": 1414, "D1": 811, "D2": 617, "D3": 513,
            "D4": 460, "D5": 381, "D6": 248}
RETURNS_QUEUE = 53
WAITING_TRANSFERS = 0


@pytest.fixture(scope="module")
def baseline():
    built = peak_day_inputs.build()
    state = State(day=built.day.delivery_day, pools=built.pools)
    return run_day(state, seed=SEED, **built.kwargs)


# ------------------------------------------------- the guards against a hole

@pytest.mark.parametrize("record, pinned", [
    (Tally, TALLY), (Metrics, METRICS)])
def test_every_field_of_the_record_is_pinned(record, pinned):
    """A field added later is unpinned until someone gives it a value here."""
    assert {f.name for f in fields(record)} == set(pinned)


def test_every_reported_count_is_pinned():
    """`DayReport`'s fields, less the five this file compares another way."""
    compared_elsewhere = {"day", "tally", "metrics", "checks",
                          "allocation", "unassigned"}
    assert {f.name for f in fields(DayReport)} - compared_elsewhere == set(REPORT)


# ------------------------------------------------------------- the pin itself

def test_the_days_counts_are_unchanged(baseline):
    report, _ = baseline
    assert {f.name: getattr(report.tally, f.name)
            for f in fields(Tally)} == TALLY


def test_the_days_metrics_are_unchanged(baseline):
    report, _ = baseline
    assert {f.name: getattr(report.metrics, f.name)
            for f in fields(Metrics)} == METRICS


def test_the_days_report_is_unchanged(baseline):
    report, _ = baseline
    assert {name: getattr(report, name) for name in REPORT} == REPORT
    assert len(report.unassigned) == UNASSIGNED


def test_the_allocation_is_unchanged(baseline):
    """§4.2's split -- what Step 2 of T9 could disturb.

    Counted per facility rather than compared vehicle by vehicle: which bike
    goes where is `place`'s business and `tests/test_allocation.py` holds it
    to that. What must not move is how many each facility gets.

    Two things this does not pin, said here because the assertion below looks
    like it pins them:

    1. `allocate`. `peak_day_inputs.build` passes `allocation=BIKE_ALLOCATION`,
       so `run_day` takes the supplied split (`simulation/day.py:568`) and
       never derives one. Mutating `allocate` leaves all nine tests green.
       That is not a hole: T9 asks that existing allocation output be
       *unchanged*, and this pin fails the moment Step 2 perturbs what was
       supplied -- moving one bike from D6 to HUB after that branch fails this
       test and four others.
    2. `moves`. Day one has no previous placement, so the counter is
       structurally zero and `moves += 2` cannot be seen from here.
       `tests/test_allocation.py:52` is what holds it.
    """
    report, _ = baseline
    placed = report.allocation.by_facility()
    assert {f: len(ids) for f, ids in placed.items()} == BIKES_BY_FACILITY
    assert report.allocation.moves == MOVES


def test_the_checks_are_unchanged(baseline):
    """§8.3's three comparisons, to the figures `docs/capacity-finding.md` cites."""
    report, _ = baseline
    assert report.checks.delivery.available == 3000
    assert report.checks.delivery.required == 3100
    assert report.checks.vans.available == 110.0
    assert report.checks.vans.required == pytest.approx(121.28972222222222)
    assert report.checks.processing.required == 4550
    assert [c.name for c in report.checks.all if c.binds] == [
        "delivery", "van-hours"]


def test_what_the_day_hands_tomorrow_is_unchanged(baseline):
    """The state is half the output: a change that moved envelopes between
    depots without changing today's totals would pass every test above."""
    _, tomorrow = baseline
    assert {f: len(pool) for f, pool in tomorrow.pools.items()} == TOMORROW
    assert tomorrow.pool_size == sum(TOMORROW.values())
    assert len(tomorrow.returns_queue) == RETURNS_QUEUE
    assert len(tomorrow.transfers) == WAITING_TRANSFERS
