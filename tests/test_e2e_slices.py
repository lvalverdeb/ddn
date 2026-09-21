"""The slice runners of `ddn/e2e/`, each against its own slice document.

These test a slice in isolation: that it produces the hand-off its document's
outputs table describes, over §10's day. Whether the three *compose* into the
same day `simulation.run_day` answers is `tests/e2e/test_chain.py`'s question,
and it is deliberately not asked here — a slice that agrees with the simulator
for the wrong reason should still fail its own document first.
"""

from __future__ import annotations

import pytest

from ddn import assumptions
from ddn.e2e import handoff, pickups_to_hub
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
