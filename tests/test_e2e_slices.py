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
from ddn.e2e import depot_delivery, handoff, hub_to_depots, pickups_to_hub
from ddn.e2e.depot_delivery.scenario import Scenario as Scenario3
from ddn.e2e.hub_to_depots.scenario import Scenario as Scenario2
from ddn.e2e.pickups_to_hub.scenario import Scenario
from ddn.model import lifecycle
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


def test_slice_2_carries_the_sender_site_through_to_e2e_3(positioned):
    """The invariant `handoff.py`'s docstring spends three paragraphs on, and
    which nothing tested on *this* boundary.

    §5.5 returns an envelope to the sender; `returns.sites` reads
    `customer_lat` off the record and `simulation.day._return_run` raises
    rather than guess. Slice 1's hand-off was checked for it; slice 2's was
    not — so stripping the field here left the chain green field-for-field on
    a pool `run_day` itself raises a `ValueError` on. Agreement with an oracle
    that cannot process the input is not agreement.
    """
    pool, _ = positioned
    every = [e for envelopes in pool.envelopes.values() for e in envelopes]

    assert every, "an empty pool would make this vacuous"
    assert all("customer_lat" in e and "customer_lon" in e for e in every)


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


# --------------------------------------- E2E-3, depot delivery and outcomes

def _canned(sequence):
    """An outcome source that answers from a fixed list, in order.

    A real day is told what happened (e2e-3 §3, "Driver app"); a simulation
    draws it. A test does neither — it says so outright, which is what makes
    the assertions below about §6's *consequences* and not about a rate.
    """
    remaining = list(sequence)
    return lambda envelope: remaining.pop(0)


def _slice_3(positioned_pool, facility, bikes, outcomes_in, reason="recipient unavailable"):
    return depot_delivery.run(
        Scenario3(facility_id=facility,
                  delivery_day=positioned_pool.delivery_day,
                  bikes=bikes,
                  deliver=lambda offered, _f, _b: (list(offered), []),
                  outcome=_canned(outcomes_in),
                  reason=lambda envelope: reason),
        positioned_pool)


@pytest.fixture(scope="module")
def d1_pool(positioned):
    pool, _ = positioned
    return pool


def test_slice_3_accounts_for_every_envelope_it_was_positioned(d1_pool):
    """Attempted, declined for capacity, or swept as expired — no fourth place.

    e2e-3 §1 ends when "every envelope dispatched that day has an outcome
    recorded and its next-state assigned". An envelope in the pool and in none
    of these three is one the facility lost, which no count would show.
    """
    facility = "D1"
    offered = d1_pool.envelopes[facility]
    result = _slice_3(d1_pool, facility, bikes=2,
                      outcomes_in=["Delivered"] * len(offered))

    seen = (set(result.outcomes)
            | {u.package_id for u in result.unassigned}
            | set(result.returns.package_ids))

    assert seen == {e["package_id"] for e in offered}
    assert result.tally.ready_pool == len(offered)


def test_slice_3_assigns_the_next_state_section_5_2_6_draws(d1_pool):
    """§6's outcome decides, §5.2.6 validates, and the pair is asserted here.

    Not `Status(outcome)`: the two spellings coincide today and the point of
    going through `lifecycle.after_attempt` is that they need not.
    """
    result = _slice_3(d1_pool, "D1", bikes=1,
                      outcomes_in=["Delivered", "Postponed", "Rejected",
                                   "Returned"] * 10)

    assert all(result.next_state[pid] == lifecycle.after_attempt(outcome)
               for pid, outcome in result.outcomes.items())
    assert set(result.next_state.values()) == {
        lifecycle.Status.DELIVERED, lifecycle.Status.POSTPONED,
        lifecycle.Status.REJECTED, lifecycle.Status.RETURNED}


def test_slice_3_keeps_the_order_it_attempted_them_in(d1_pool):
    """The outcome source is asked in attempt order, and the map records it.

    This is what lets the chain compare per-package rather than per-count:
    reordering a pool leaves every total identical and changes hundreds of
    individual outcomes, so the sequence is the evidence.

    It is a *subsequence* of the pool, not a prefix of it. `lastmile.select`
    chooses by (priority desc, package_id) and then returns the survivors in
    **pool** order — so which envelopes go is a §8 decision and the order they
    are attempted in is the pool's. Asserting a prefix here failed, correctly.
    """
    result = _slice_3(d1_pool, "D1", bikes=2, outcomes_in=["Delivered"] * 999)
    attempted = list(result.outcomes)
    pool_order = list(d1_pool.positioned["D1"])

    assert set(attempted) < set(pool_order), "capacity should bind here"
    assert attempted == [pid for pid in pool_order if pid in set(attempted)]


def test_slice_3_sends_back_only_what_section_5_5_recognises(d1_pool):
    """A delivery does not go back; a rejection and a defect do.

    Gated through `returns.goes_back` rather than by the outcome name, because
    that predicate reads the stamps `model.outcomes` writes and §5.3.2 asks the
    same question at a depot. Two copies of it would be two places to forget
    §6.1's expiry clock.
    """
    result = _slice_3(d1_pool, "D1", bikes=1,
                      outcomes_in=["Rejected", "Returned", "Delivered",
                                   "Postponed"] * 10)

    going_back = set(result.returns.package_ids)
    by_outcome = {str(o) for pid, o in result.outcomes.items() if pid in going_back}

    assert by_outcome == {"Rejected", "Returned"}
    assert result.returns.weight_g > 0


def test_slice_3_hands_its_returns_to_the_facility_that_made_them(d1_pool):
    """e2e-3 §2.5: the depot's load waits for a van; the hub's goes tonight.

    The slice does not choose between them — it stamps the facility and E2E-2
    reads it. Stamping the hub at outcome time is what once made every
    rejection hub-resident the instant it happened, so it joined *tonight's*
    return run, which is the one thing §5.5 says it must not do.
    """
    depot = _slice_3(d1_pool, "D1", bikes=1, outcomes_in=["Rejected"] * 99)
    hub = _slice_3(d1_pool, handoff.HUB, bikes=1, outcomes_in=["Rejected"] * 99)

    assert depot.returns.facility_id == "D1"
    assert hub.returns.facility_id == handoff.HUB


def test_slice_3_postpones_back_to_ready_with_the_attempt_counted(d1_pool):
    """§5.2.6: "Postponed: back to Ready for next attempt".

    The count is what stops a postponement being retried for ever, and §11
    reads it as the first-attempt rate — so a retry that forgot to increment
    would look like a day of first attempts that never end.
    """
    result = _slice_3(d1_pool, "D1", bikes=1, outcomes_in=["Postponed"] * 99,
                      reason="driver out of time")

    assert set(result.next_state.values()) == {lifecycle.Status.POSTPONED}
    assert result.returns.package_ids == ()
    assert result.tally.postponed == result.tally.dispatched
    assert result.tally.delivered_first_attempt == 0


def test_slice_3_sweeps_an_expired_envelope_instead_of_dispatching_it(d1_pool):
    """§6.1: "not dispatched at all" — and it still goes back, not away.

    Nothing has expired on §10's day, so deleting the sweep from the runner
    leaves the entire suite green — measured. This is the scenario that makes
    it bite: a pool where every envelope's SLA date is behind us.

    The sweep runs *before* §8's capacity cut, which is the part worth pinning.
    An expired envelope reaching `select` would compete for a bike it may not
    board, and could push a live envelope out to make room for a journey it
    cannot take.
    """
    stale = handoff.PositionedPool(
        delivery_day=d1_pool.delivery_day,
        positioned={"D1": ("P-1", "P-2")},
        envelopes={"D1": ({"package_id": "P-1", "sla_date": "2026-09-01",
                           "weight_g": 20, "priority": 900.0},
                          {"package_id": "P-2", "sla_date": "2026-09-01",
                           "weight_g": 20, "priority": 900.0})})

    result = _slice_3(stale, "D1", bikes=1, outcomes_in=[])

    assert result.outcomes == {}, "an expired envelope is not attempted"
    assert result.tally.dispatched == 0
    assert result.tally.sla_expired == 2
    assert set(result.returns.package_ids) == {"P-1", "P-2"}, (
        "§6.1 returns them to the customer; they are not discarded")


def test_slice_3_claims_nothing_about_transfers_or_violations(d1_pool):
    """A vacuity ledger, not a result.

    e2e-3 §4 asks for transfer requests (row 5) and a §7.1 violation list
    (row 1). §5.3.2 raises a transfer from a *re-geocode* the pool does not
    carry, and the violation list needs the facility's `Solution`, which the
    injected `deliver` does not produce — it returns envelopes. Rows C7 and
    C12 own them.

    If this fails, the slice gained a producer and the chain's comparison of
    these two fields stops being an empty-against-empty.
    """
    result = _slice_3(d1_pool, "D1", bikes=1, outcomes_in=["Delivered"] * 99)

    assert result.transfers == ()
    assert result.violations == ()


def test_slice_3_hands_off_through_a_file(d1_pool):
    """Tomorrow's line-haul can be run from the file without running today."""
    result = _slice_3(d1_pool, "D1", bikes=1,
                      outcomes_in=["Delivered", "Rejected", "Postponed"] * 33)

    reread = handoff.DayOutcomes.model_validate_json(result.model_dump_json())

    assert reread == result
    assert list(reread.outcomes) == list(result.outcomes), "attempt order"
