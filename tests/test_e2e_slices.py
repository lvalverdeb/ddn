"""The slice runners of `ddn/e2e/`, each against its own slice document.

These test a slice in isolation: that it produces the hand-off its document's
outputs table describes, over §10's day. Whether the three *compose* into the
same day `simulation.run_day` answers is `tests/e2e/test_chain.py`'s question,
and it is deliberately not asked here — a slice that agrees with the simulator
for the wrong reason should still fail its own document first.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ddn import assumptions, linehaul
from ddn.e2e import handoff, hub_to_depots, pickups_to_hub
from ddn.e2e.hub_to_depots.scenario import Scenario as Scenario2
from ddn.e2e.pickups_to_hub.scenario import Scenario
from tests import peak_day_inputs

CUT_OFF = (assumptions.PROCESSING_CUTOFF.hour * 3600
           + assumptions.PROCESSING_CUTOFF.minute * 60)


@pytest.fixture(scope="module")
def built():
    return peak_day_inputs.build()


@pytest.fixture(scope="module")
def scenario(built):
    """§10's day as e2e-1 §3's inputs table."""
    return Scenario(
        hub=next(f for f in built.kwargs["facilities"] if f["id"] == "HUB"),
        facilities=built.kwargs["facilities"],
        requests=built.kwargs["requests"],
        inflow=built.kwargs["inflow"],
        vans=built.kwargs["vans"],
        travel=built.kwargs["travel"],
        collection_day=built.day.collection_day,
        cut_off=CUT_OFF)


@pytest.fixture(scope="module")
def ready_pool(scenario):
    return pickups_to_hub.run(scenario)


def test_slice_1_positions_every_facility_including_an_empty_one(ready_pool,
                                                                 scenario):
    """e2e-1 §4: the pool is *by facility*, so a facility is a key, not a count.

    A facility whose pool is empty still has to appear. Dropping it loses its
    envelopes silently on any day where it has some, and on §10's day — where
    all seven are busy — nothing would show. The order is asserted too: §5.4
    selects in pool order, so a mapping built by first-appearance delivers a
    different set with every total identical.
    """
    assert list(ready_pool.ready) == [f["id"] for f in scenario.facilities]


def test_slice_1_accounts_for_every_envelope_of_the_inflow(ready_pool, scenario):
    """Ready + uncollected == the whole inflow, with no third place to hide.

    §5.1.6's uncollected envelopes are not held and not rolled — they never
    arrived. Without the count they would simply be missing, and the day would
    look smaller rather than look wrong.
    """
    positioned = sum(len(pool) for pool in ready_pool.ready.values())

    assert positioned == 4140
    assert ready_pool.uncollected == 410
    assert positioned + ready_pool.uncollected == len(scenario.inflow) == 4550


def test_slice_1_carries_the_sender_site_on_every_positioned_envelope(ready_pool):
    """§5.5 returns to the sender, and only the bag's request knows where.

    Checked over the whole pool rather than a sample: the fallback in
    `processing.position` means a missing request degrades quietly to the
    recipient's address, so a systematic loss would not raise anywhere.
    """
    every = [e for pool in ready_pool.ready.values() for e in pool]

    assert all("customer_lat" in e and "customer_lon" in e for e in every)
    assert all("expected_ready_at" in e for e in every)


def test_slice_1_releases_one_van_per_van_that_came_back(ready_pool, scenario):
    """e2e-1 §4: "van_id, time back at hub, unloaded" — E2E-2 reads these."""
    assert {r.vehicle_id for r in ready_pool.released} <= {
        v["vehicle_id"] for v in scenario.vans}
    assert all(r.back_at_hub.date() == scenario.collection_day
               for r in ready_pool.released)


def test_slice_1_claims_nothing_about_held_or_rolled_assembly(ready_pool):
    """A vacuity ledger, not a result. These are empty because nothing fills them.

    e2e-1 §4 asks for held envelopes by reason and the rolled assembly set.
    Nothing in `ddn/` produces either: `processing.schedule` applies no cut-off,
    and `Sorted.straddles` is a bool nobody converts to a reason. Rows A3, A4
    and A6 own them.

    If this test ever fails, the fixture or the modules gained coverage — and
    every assertion above that quietly assumed an empty held set needs looking
    at again, starting with the conservation identity.
    """
    assert ready_pool.held == ()
    assert ready_pool.rolled_assembly == ()


def test_slice_1_hands_off_through_a_file(ready_pool):
    """The point of the hand-off: E2E-2 can be run without running E2E-1."""
    reread = handoff.ReadyPool.model_validate_json(ready_pool.model_dump_json())

    assert list(reread.ready) == list(ready_pool.ready)
    assert reread == ready_pool


# ------------------------------------------------- E2E-2, hub to depots

@pytest.fixture(scope="module")
def night_scenario(built):
    """§10's night as e2e-2 §4's inputs table, minus the pool E2E-1 hands over."""
    return Scenario2(facilities=built.kwargs["facilities"],
                     vans=built.kwargs["vans"],
                     transit=built.kwargs["transit"],
                     collection_day=built.day.collection_day,
                     delivery_day=built.day.delivery_day)


@pytest.fixture(scope="module")
def positioned(night_scenario, ready_pool):
    return hub_to_depots.run(night_scenario, ready_pool)


def test_slice_2_positions_or_rolls_every_ready_envelope(positioned, ready_pool):
    """e2e-2 §1's boundary, as a partition with no third place to be.

    "every Ready envelope is either **positioned** at its dispatching facility
    ... or explicitly **rolled** ... with a reason". An envelope in neither is
    the failure this slice exists to make visible, and a set equality is what
    makes "neither" impossible to pass off as "fine".
    """
    pool, _ = positioned
    ready = {e["package_id"] for facility in ready_pool.ready
             for e in ready_pool.ready[facility]}
    landed = {pid for ids in pool.positioned.values() for pid in ids}
    rolled = {excluded.package_id for excluded in pool.rolled}

    assert landed | rolled == ready
    assert not landed & rolled, "an envelope both positioned and rolled"


def test_slice_2_keeps_the_pool_order_it_was_handed(positioned, ready_pool):
    """§5.4 selects under capacity in pool order, so the order is the contract.

    Same ids in another sequence deliver a different set tomorrow with every
    aggregate count identical — which is why this is asserted as a list and
    not as a set, and why `positioned` and `envelopes` are checked to agree.
    """
    pool, _ = positioned

    assert list(pool.positioned) == list(ready_pool.ready)
    assert all(pool.positioned[f]
               == tuple(e["package_id"] for e in pool.envelopes[f])
               for f in pool.positioned)


def _night(scenario, ready):
    """The trips slice 2 plans, for a test that needs to reach past the hand-off.

    `PositionedPool` carries no circuits — e2e-2 §5's line-haul plan goes to
    drivers, not to E2E-3 — so a test about departure times has to re-plan.
    Same calls in the same order as `hub_to_depots.run`.
    """
    return linehaul.plan(
        linehaul.depots(scenario.facilities, hub_id=scenario.hub_id),
        linehaul.depot_bound(ready.ready, hub_id=scenario.hub_id),
        scenario.vans, transfers=scenario.transfers,
        returning=scenario.returning, transit=scenario.transit,
        unload_seconds=scenario.unload_seconds).trips


def test_slice_2_runs_the_seven_one_day_check_not_an_empty_literal(
        night_scenario, ready_pool):
    """§13.1's list is empty here because nothing broke, not because nothing ran.

    The audit found three endpoints answering `"violations": []` as a literal,
    which reads as a clean bill and is a different fact. So this breaks §7.1's
    line-haul bullet on purpose — every van recorded as still out collecting
    long after its circuit departs — and requires the breach to come back.

    The return time has to beat the *latest* departure, and §5.3's clock runs
    past midnight: circuits on §10's day leave at around 95,400 seconds, which
    is half past two the following morning. A plausible-looking 23:00 is
    earlier than that and breaches nothing.
    """
    clean, _ = hub_to_depots.run(night_scenario, ready_pool)
    assert clean.violations == ()

    latest = max(trip.departure for trip in _night(night_scenario, ready_pool))
    still_out = latest + 1
    late = replace(night_scenario,
                   van_back_at={v["vehicle_id"]: still_out
                                for v in night_scenario.vans})
    breached, _ = hub_to_depots.run(late, ready_pool)

    assert breached.violations, (
        "a van departing before it was back at the hub is §7.1's line-haul "
        "bullet; an empty list here means the check is not wired")
    assert breached.positioned == clean.positioned, (
        "the breach is reported, not routed around")


def test_slice_2_takes_back_what_no_circuit_carried(night_scenario, ready_pool):
    """A rolled envelope is at the hub in the morning, not at its depot.

    Nothing rolls on §10's day, so every other assertion in this file passes
    with `strip_rolled` deleted outright — measured. This is the scenario that
    makes it bite: no vans, so every depot-bound envelope rolls with §9.2's
    reason, and only the hub is left holding anything.

    Leaving a rolled envelope in a depot's pool would offer §5.4 an envelope
    that is not there, which is the one error §7.1's depot bullet exists to
    prevent — and tomorrow it would be counted as delivered from a facility it
    never reached.
    """
    grounded = replace(night_scenario, vans=())
    pool, _ = hub_to_depots.run(grounded, ready_pool)

    assert all(not ids for facility, ids in pool.positioned.items()
               if facility != handoff.HUB), "a depot kept an envelope no van took"
    assert pool.positioned[handoff.HUB] == tuple(
        e["package_id"] for e in ready_pool.ready[handoff.HUB]), (
        "the hub's own pool never travelled, so nothing there rolls")
    assert len(pool.rolled) == sum(
        len(p) for f, p in ready_pool.ready.items() if f != handoff.HUB)
    assert {excluded.reason for excluded in pool.rolled} == {linehaul.NO_VAN}


def test_slice_2_claims_nothing_about_transfers_or_rolling(positioned):
    """A vacuity ledger, not a result.

    Nothing rolls on §10's day and no transfer is raised, so `rolled`,
    `carried`, `deferred` and `to_returns` are all empty here — and slice 2
    could contain no transfer code whatsoever and still pass. Rows B3, B4 and
    B6 own that gap. (Rolling itself is covered by the grounded-fleet test
    above; transfers have no such cheap scenario, because raising one needs a
    regeocode that E2E-3 supplies.)

    If this fails, the fixture gained coverage and the partition test above
    needs re-reading: it has been asserting a set equality with one side empty.
    """
    pool, transfers = positioned

    assert pool.rolled == ()
    assert (transfers.carried, transfers.deferred, transfers.to_returns) == ((), (), ())


def test_slice_2_hands_off_through_a_file(positioned):
    """E2E-3 can be run from the file without running E2E-2."""
    pool, transfers = positioned

    assert handoff.PositionedPool.model_validate_json(
        pool.model_dump_json()) == pool
    assert handoff.TransferOutcomes.model_validate_json(
        transfers.model_dump_json()) == transfers
