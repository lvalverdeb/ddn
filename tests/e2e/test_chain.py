"""The three slices, composed, against `simulation.run_day` on §10's day.

Slices: `docs/e2e/e2e-1-pickups-to-hub.md`, `e2e-2-hub-to-depots.md`,
`e2e-3-depot-delivery-and-outcomes.md`.

**What a failure here means.** The slice decomposition is wrong. Do not adjust
`ddn/simulation/` to make this agree — the simulator's answer to §10's day is
pinned by name in `tests/test_simulation.py`
(`test_the_peak_day_threaded_from_an_empty_start`), and a genuine disagreement
about what the day *means* is a spec change to `docs/vrp-problem-definition.md`,
made there first.

**What this can and cannot prove.** It proves the slices are wired, cohorted
and ordered the way the simulator is. It does **not** prove they are right, and
it structurally cannot prove routing: matching field for field forces
`run_day`'s default non-routing `deliver`, so a chain that routes cannot match
and a chain that matches does not route. Worse and more usefully, each slice
document's headline rule would *break* this test — e2e-3 §2.1 breaks ties by
SLA date then attempt number where `lastmile.plan` sorts by (-priority,
package_id), and 1,140 envelopes are declined here, so the ties are live. Green
therefore means "no slice has yet implemented a rule the simulator lacks", and
Task 15's pool-ordering bullet will turn it red on its first commit. That is
the test working.

**No `pytest.approx` anywhere, and every *comparison* is read off the oracle's
own report at runtime.** `tests/fixtures/peak_day.py` carries §10's published
figures, which this run does not reproduce; the deviations are recorded in the
simulator's pin rather than smoothed over here.

Three literals do appear, and none of them is an expectation. `3100` and
`4140` are asserted **against each other** in the section at the foot of this
file, whose entire point is that the two cohorts differ — a comparison that
needs both sides named, or it says nothing. `differing > 300` is a floor on
the negative control, deliberately loose: the measured figure is 386 and
pinning it exactly would make the control fail on any `Rates` change for a
reason unrelated to what it guards.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, time

import pytest

from ddn import assumptions, lastmile
from ddn.allocation import place
from ddn.e2e import depot_delivery, handoff, hub_to_depots, pickups_to_hub
from ddn.e2e.depot_delivery.scenario import Scenario as DeliveryScenario
from ddn.e2e.hub_to_depots.scenario import Scenario as NightScenario
from ddn.e2e.pickups_to_hub.scenario import Scenario as PickupScenario
from ddn.model.records import DEFAULT_WEIGHT_G
from ddn.simulation import Rates, State, run_day
from ddn.simulation.day import sub_reason
from tests import peak_day_inputs

SEED = 7
BUILT = peak_day_inputs.build()
CUT_OFF = (assumptions.PROCESSING_CUTOFF.hour * 3600
           + assumptions.PROCESSING_CUTOFF.minute * 60)


@pytest.fixture(scope="module")
def built():
    return peak_day_inputs.build()


@pytest.fixture(scope="module")
def oracle(built):
    """§10's day as two threaded `run_day` calls, with recorders attached.

    Never `run_days(2, ...)`: it drops the intermediate `State`, and day D's
    night is what day D+1 is run on. The recording `deliver` and `rates` are
    proved inert in `tests/test_simulation.py`, which is where that claim
    belongs — this file cannot mark its own homework.
    """
    forward = built.kwargs
    attempted: list[tuple[dict, str]] = []
    drawn: list[str] = []

    class Recording:
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

    collected, night = run_day(State(day=built.day.collection_day, pools={}),
                               seed=SEED, **forward)
    delivered, tomorrow = run_day(night, seed=SEED, deliver=spy,
                                  rates=Recording(Rates()), **forward)
    return collected, night, delivered, tomorrow, attempted, drawn


@pytest.fixture(scope="module")
def chained(built):
    """Slices 1 -> 2 -> 3, each fed the previous one's hand-off.

    Slice 3 runs once per facility, in the pool's own key order, drawing from
    one `random.Random` seeded exactly as `run_day` seeds its own — the date
    folded in, because `day.py` folds it.
    """
    kwargs = built.kwargs
    hub = next(f for f in kwargs["facilities"] if f["id"] == handoff.HUB)

    ready = pickups_to_hub.run(PickupScenario(
        hub=hub, facilities=kwargs["facilities"], requests=kwargs["requests"],
        inflow=kwargs["inflow"], vans=kwargs["vans"], travel=kwargs["travel"],
        collection_day=built.day.collection_day, cut_off=CUT_OFF))

    # e2e-1 §4 row 5 names E2E-2 as the consumer of the van releases, and
    # until this line nothing consumed them: `van_back_at` defaulted to `{}`,
    # so `postcheck._van_release_bullets` hit `if back is None: continue` for
    # every trip and §7.1's line-haul bullet was checked against 0 of 6.
    midnight = datetime.combine(built.day.collection_day, time())
    positioned, transfers = hub_to_depots.run(NightScenario(
        facilities=kwargs["facilities"], vans=kwargs["vans"],
        transit=kwargs["transit"], collection_day=built.day.collection_day,
        delivery_day=built.day.delivery_day,
        van_back_at={r.vehicle_id: int((r.back_at_hub - midnight).total_seconds())
                     for r in ready.released}), ready)

    rng = random.Random(SEED + built.day.delivery_day.toordinal())
    rates = Rates()
    fleet = place(dict(kwargs["allocation"]), kwargs["bikes"], previous={})
    per_facility = {f: len(v) for f, v in fleet.by_facility().items()}

    days = [depot_delivery.run(DeliveryScenario(
        facility_id=facility, delivery_day=built.day.delivery_day,
        bikes=per_facility.get(facility, 0),
        deliver=lambda offered, _f, _b: (list(offered), []),
        outcome=lambda _envelope: rates.draw(rng),
        reason=lambda _envelope: sub_reason(rng)), positioned)
        for facility in positioned.positioned]

    return ready, positioned, transfers, days


# ------------------------------------------- guards, before any comparison

def test_g1_day_d_delivers_nothing_so_the_two_halves_are_separable(oracle):
    """Trap D1. `run_day` is `_attempt` -> `_doorstep` -> `_collect`, i.e.
    E2E-3, E2E-1, E2E-2 — so one call would deliver a pool it is still
    collecting. From an empty start the halves separate, and these are two
    equalities rather than an inequality: the last unbounded guard this
    repository shipped would have passed on zero."""
    collected, _, _, _, _, _ = oracle

    assert collected.tally.dispatched == 0
    assert collected.tally.ready_pool == 0


def test_g2_both_days_are_the_days_the_fixture_names(oracle, built):
    """Trap D4. The planning date is passed, never read from a clock, and
    `day.py` folds it into the seed — so the wrong date is a different day
    *and* a different random stream."""
    collected, _, delivered, _, _, _ = oracle

    assert collected.day == built.day.collection_day
    assert delivered.day == built.day.delivery_day


def test_g3_the_night_carries_exactly_what_the_day_positioned(oracle):
    """Trap D2, and this test's largest blind spot.

    From an empty start `_handover` is the identity — there are no held
    postponements and no capacity-declined envelopes from yesterday — so the
    chain never exercises it. On any non-empty start these two differ."""
    collected, night, _, _, _, _ = oracle

    assert night.pool_size == sum(collected.positioned.values())


def test_g4_the_instruments_saw_the_whole_day(oracle):
    """An instrument check, not an oracle check: it says the recorders did not
    miss anything, and says nothing about the slices."""
    _, _, delivered, _, attempted, drawn = oracle

    assert len(attempted) == delivered.tally.dispatched == len(drawn)
    assert len({e["package_id"] for e, _ in attempted}) == len(attempted)
    assert Counter(drawn) == delivered.outcomes


# ------------------------------------------------------------- the chain

def test_a1_the_positioned_pool_matches_facility_for_facility_in_order(
        oracle, chained):
    """Trap D3's first half, and it needs no instrumentation at all.

    `day.py` rebuilds the pools keyed by the `facilities` *argument*, so both
    the facility sequence and each facility's own sequence have to match.
    Reordering the facilities leaves every aggregate count byte-identical and
    changes 386 of 3,000 outcomes; sorting within a facility changes 392.
    """
    _, night, _, _, _, _ = oracle
    _, positioned, _, _ = chained

    assert list(positioned.positioned) == list(night.pools)
    assert all(positioned.positioned[facility]
               == tuple(e["package_id"] for e in night.pools[facility])
               for facility in night.pools)


def test_a2_the_flat_attempt_sequence_matches(oracle, chained):
    """The load-bearing assertion. Order is what tallies cannot see.

    Sorting each facility's pool by `package_id` leaves the selected *set*
    identical, the whole `Tally` identical and `DayReport.unassigned`
    identical, while changing 392 of 3,000 per-envelope outcomes. Only the
    sequence catches that.
    """
    _, _, _, _, attempted, _ = oracle
    _, _, _, days = chained

    assert ([package_id for day in days for package_id in day.outcomes]
            == [envelope["package_id"] for envelope, _ in attempted])


def test_a3_every_envelope_got_the_same_outcome(oracle, chained):
    """Per-package, because `DayReport` publishes counts and counts agree on
    days that are not the same day.

    Given A2 this is largely mechanical — identical ids in identical order,
    one draw each off one generator. Its independent power is over the draw
    *count* and the interleaving: a slice that drew a sub-reason for every
    envelope, or drew them in a second pass, walks the same generator
    differently and lands here.
    """
    _, _, _, _, attempted, drawn = oracle
    _, _, _, days = chained

    expected = {envelope["package_id"]: outcome
                for (envelope, _), outcome in zip(attempted, drawn, strict=True)}
    got = {package_id: str(outcome)
           for day in days for package_id, outcome in day.outcomes.items()}

    assert got == expected


def test_a4_the_unassigned_match_with_their_reasons(oracle, chained):
    """§9.2's list, as a multiset of (package_id, reason) — not a count.

    A count is satisfied by any 1,140 envelopes with any reasons.
    """
    _, _, delivered, _, _, _ = oracle
    _, _, _, days = chained

    assert (Counter((e.package_id, e.reason)
                    for day in days for e in day.unassigned)
            == Counter((e.package_id, e.reason) for e in delivered.unassigned))


def test_a5_the_tallies_add_up_to_the_days(oracle, chained):
    """`Tally` adds, so seven facilities sum to one day — for the fields a
    delivery slice can see. The rest are asserted at day D or ledgered.

    The weakest assertion in this file, and labelled so: D3 proves tallies
    agree on days that differ in hundreds of envelopes.
    """
    _, _, delivered, _, _, _ = oracle
    _, _, _, days = chained
    total = sum((day.tally for day in days), type(days[0].tally)())

    assert total.dispatched == delivered.tally.dispatched
    assert total.delivered == delivered.tally.delivered
    assert total.postponed == delivered.tally.postponed
    assert total.rejected == delivered.tally.rejected
    assert total.defective == delivered.tally.defective
    assert total.sla_expired == delivered.tally.sla_expired
    assert total.unassigned == delivered.tally.unassigned
    assert total.delivered_first_attempt == delivered.tally.delivered_first_attempt
    # Both of these were being dropped by the hand-off — `bikes_deployed` set
    # to a literal 0 and `delivered_within_sla` never set. `metrics.measure`
    # then answered `envelopes_per_bike: None` and `sla_compliance: 0.0`, and
    # 0.0 reads as a total SLA failure rather than as a missing input, which
    # `simulation/metrics.py`'s own docstring says it must never do.
    assert total.delivered_within_sla == delivered.tally.delivered_within_sla
    assert total.bikes_deployed == delivered.tally.bikes_deployed


def test_a6_the_return_load_matches_per_package_and_per_facility(oracle,
                                                                 chained):
    """§5.5's load, and where it is standing.

    `DayReport` publishes `return_stops`, a count of *hub* stops — so a
    depot's returns appear in it not at all, and dropping one of the 33
    hub-resident envelopes still yields 15 stops for most of them. The spy
    gives each attempted envelope its facility, so both halves are compared.
    """
    _, _, _, _, attempted, drawn = oracle
    _, _, _, days = chained

    facility_of = {e["package_id"]: f for e, f in attempted}
    # A postponement whose SLA date is today has no next attempt, so §6.1
    # returns it tonight rather than holding it — it is in the return load
    # alongside the rejections, and counting only those would miss it.
    going_back = {package_id
                  for (envelope, _), outcome in zip(attempted, drawn,
                                                    strict=True)
                  for package_id in [envelope["package_id"]]
                  if outcome in {"Rejected", "Returned"}
                  or (outcome == "Postponed"
                      and not lastmile.retryable(envelope, BUILT.day.delivery_day))}

    for day in days:
        expected = [package_id for envelope, _ in attempted
                    for package_id in [envelope["package_id"]]
                    if package_id in going_back
                    and facility_of[package_id] == day.facility_id]
        # A multiset, not a set: with `set(...)` on both sides, a load that
        # sent every returned envelope back *twice* was byte-identical to
        # baseline across the whole suite. And the weight rides on the same
        # record, so it is asserted here rather than nowhere.
        assert Counter(day.returns.package_ids) == Counter(expected)
        assert day.returns.weight_g == sum(
            int(envelope.get("weight_g") or DEFAULT_WEIGHT_G)
            for envelope, _ in attempted
            if envelope["package_id"] in set(expected))


def test_the_van_release_bullet_was_actually_examined(oracle, chained):
    """§13.1's empty list must mean "checked and clean", not "not checked".

    The chain fed slice 2 no van return times at all, so `check_day_constraints`
    skipped every trip and `positioned.violations == ()` was the same sentence
    as a clean bill with a different meaning — the exact defect the audit found
    three endpoints shipping. The releases E2E-1 produces are now wired in, and
    this asserts the check had something to look at.
    """
    ready, positioned, _, _ = chained

    assert ready.released, "E2E-1 produced no releases to hand over"
    assert positioned.violations == ()

    # The proof that the empty list above means "checked and clean": the same
    # leg, with every van recorded as still out collecting after its circuit
    # departs, must report the breach. If this comes back empty too, the
    # release times are not reaching the check and neither result means
    # anything.
    midnight = datetime.combine(ready.collection_day, time())
    breaching = {r.vehicle_id: 40 * 3600 for r in ready.released}
    broken, _ = hub_to_depots.run(NightScenario(
        facilities=BUILT.kwargs["facilities"], vans=BUILT.kwargs["vans"],
        transit=BUILT.kwargs["transit"],
        collection_day=ready.collection_day,
        delivery_day=positioned.delivery_day,
        van_back_at=breaching), ready)

    assert broken.violations, (
        "§7.1's line-haul bullet reported nothing against vans that had not "
        "returned; the chain's clean list is not evidence")
    assert all(r.back_at_hub >= midnight for r in ready.released), (
        "a release stamped before the day it belongs to")


def test_a7_tomorrow_holds_what_the_slices_said_it_would(oracle, chained):
    """§5.2.6's consequences, checked against what `run_day` actually publishes.

    Postponed envelopes come back Ready with the attempt counted; depot
    rejects wait in the returns queue for the following evening. Hub residents
    appear in neither (`day.py` filters them out), which is why A6 exists.
    """
    _, _, _, tomorrow, _, _ = oracle
    _, _, _, days = chained

    postponed = {package_id for day in days
                 for package_id, status in day.next_state.items()
                 if str(status) == "Postponed"}
    carried = {e["package_id"] for pool in tomorrow.pools.values() for e in pool}

    assert postponed <= carried
    assert {e["package_id"] for e in tomorrow.returns_queue} <= {
        package_id for day in days if day.facility_id != handoff.HUB
        for package_id in day.returns.package_ids}


# ------------------------------------- the vacuity ledger, and the control

def test_what_this_day_proves_nothing_about(oracle, chained):
    """Assert the empty fields *are* empty, so the day they stop being empty
    the comparisons above get re-read rather than quietly widening.

    None of this makes the covered paths covered. No `() == ()` discriminates.
    """
    collected, night, delivered, _, _, _ = oracle
    _, positioned, transfers, days = chained

    assert (collected.rolled, collected.rolled_envelopes) == ({}, 0)
    assert (collected.transfers_raised, collected.transfers_carried) == (0, 0)
    assert collected.violations == () and delivered.violations == ()
    # §6.1 *does* bite here, on nine envelopes postponed on their SLA date —
    # so this is a live path, not an empty one, and it is the one thing in
    # this ledger that is asserted as non-zero.
    assert delivered.tally.sla_expired == 9
    assert night.returns_queue == ()
    # The chain's `deliver` refuses nobody, so every one of the 1,140
    # unassigned carries "count" and the `NO_TIME` branch of `DayOutcomes.of`
    # is never taken. A4 compares reasons and would catch a wrong one; it
    # cannot catch a reason that is never produced.
    assert {e.reason for e in delivered.unassigned} == {"count"}
    assert positioned.rolled == ()
    assert (transfers.carried, transfers.deferred, transfers.to_returns) == (
        (), (), ())
    assert all(day.transfers == () and day.violations == () for day in days)
    # `ReturnLoad.of` gates through `returns.goes_back`, but everything
    # `outcomes.record` puts in that list already satisfies the predicate —
    # so dropping the gate changes nothing here and its docstring's claim that
    # it is load-bearing is untested on this fixture.
    # `DayOutcomes.of` likewise hard-codes §9.2's "time" for a refusal, and
    # `solver_adapter.output` chooses between that and "count" at runtime; the
    # branch is dead because nothing refuses.
    assert all(day.tally.unassigned == len(day.unassigned) for day in days)


def test_reordering_the_facilities_changes_hundreds_of_envelopes_and_no_tally(
        oracle, chained, built):
    """The negative control: proof that A1-A3 are load-bearing.

    If this ever goes green in the "identical" direction, the assertions above
    are decorative. The aggregate clause is a *fixture-dependent coincidence*
    pinned on purpose: it will red on any `Rates` or cohort change, and the
    message says so rather than leaving someone to rediscover it.
    """
    _, night, _, _, attempted, drawn = oracle
    _, positioned, _, _ = chained

    ordered = [e["package_id"] for e, _ in attempted]
    sorted_pools = {f: night.pools[f] for f in sorted(night.pools)}

    assert set(sorted_pools) == set(night.pools), "a facility went missing"
    assert list(sorted_pools) != list(night.pools), "the orders must differ"

    rng = random.Random(SEED + built.day.delivery_day.toordinal())
    rates = Rates()
    fleet = place(dict(built.kwargs["allocation"]), built.kwargs["bikes"],
                  previous={})
    per_facility = {f: len(v) for f, v in fleet.by_facility().items()}
    shuffled = handoff.PositionedPool(
        delivery_day=positioned.delivery_day,
        positioned={f: tuple(e["package_id"] for e in sorted_pools[f])
                    for f in sorted_pools},
        envelopes={f: tuple(sorted_pools[f]) for f in sorted_pools})

    other = [depot_delivery.run(DeliveryScenario(
        facility_id=f, delivery_day=built.day.delivery_day,
        bikes=per_facility.get(f, 0),
        deliver=lambda offered, _f, _b: (list(offered), []),
        outcome=lambda _e: rates.draw(rng),
        reason=lambda _e: sub_reason(rng)), shuffled) for f in shuffled.positioned]

    moved = [package_id for day in other for package_id in day.outcomes]
    outcomes_now = {p: str(o) for day in other for p, o in day.outcomes.items()}
    before = {e["package_id"]: o
              for (e, _), o in zip(attempted, drawn, strict=True)}

    assert set(moved) == set(ordered), "the same envelopes, in another order"
    assert moved != ordered
    differing = sum(1 for p in outcomes_now if outcomes_now[p] != before[p])
    assert differing > 300, f"only {differing} envelopes moved"
    assert Counter(outcomes_now.values()) == Counter(before.values()), (
        "aggregate counts are identical on THIS fixture — a coincidence pinned "
        "deliberately; if this fires, the Rates or the cohort changed")


# ------------------------------- §10's own morning, which is a different day

@pytest.fixture(scope="module")
def seeded(built):
    """§10's stated morning — 3,100 positioned — run both ways.

    Not the chain: slice 3 alone, against `run_day` seeded from the same pool.
    The chain cannot be run here and the reason is the point of this section.
    """
    attempted: list[tuple[dict, str]] = []
    drawn: list[str] = []

    class Recording:
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

    state = State(day=built.day.delivery_day, pools=built.pools)
    report, tomorrow = run_day(state, seed=SEED, deliver=spy,
                               rates=Recording(Rates()), **built.kwargs)

    pool = handoff.PositionedPool(
        delivery_day=built.day.delivery_day,
        positioned={f: tuple(e["package_id"] for e in p)
                    for f, p in built.pools.items()},
        envelopes={f: tuple(p) for f, p in built.pools.items()})

    rng = random.Random(SEED + built.day.delivery_day.toordinal())
    rates = Rates()
    fleet = place(dict(built.kwargs["allocation"]), built.kwargs["bikes"],
                  previous={})
    per_facility = {f: len(v) for f, v in fleet.by_facility().items()}
    days = [depot_delivery.run(DeliveryScenario(
        facility_id=facility, delivery_day=built.day.delivery_day,
        bikes=per_facility.get(facility, 0),
        deliver=lambda offered, _f, _b: (list(offered), []),
        outcome=lambda _envelope: rates.draw(rng),
        reason=lambda _envelope: sub_reason(rng)), pool)
        for facility in pool.positioned]
    return report, tomorrow, days, attempted, drawn


def test_slice_3_reproduces_section_10s_own_morning(seeded):
    """The delivery half, on §10's stated 3,100 rather than the empty start.

    The equality test above runs from an empty start, which separates the
    collection half from the delivery half but is *not* §10's morning. This
    one is: the fixture's own pools, which §10 states as 2,700 new plus 400
    postponed held locally. Slice 3 answers it exactly — same dispatched, same
    per-package outcomes, same unassigned — so the decomposition is not an
    artefact of the start state chosen to make it separable.
    """
    report, _, days, attempted, drawn = seeded

    assert sum(day.tally.dispatched for day in days) == report.tally.dispatched

    # Per-package, as the paragraph above says — not a multiset of outcome
    # values and not a count. An earlier version asserted both of those and
    # went green under the same permutation that reds A3, so §10's real
    # morning — the only place this cohort is exercised — was checked more
    # weakly than the empty start it exists to corroborate.
    assert ({package_id: str(outcome)
             for day in days for package_id, outcome in day.outcomes.items()}
            == {envelope["package_id"]: outcome
                for (envelope, _), outcome in zip(attempted, drawn,
                                                  strict=True)})
    assert (Counter((e.package_id, e.reason)
                    for day in days for e in day.unassigned)
            == Counter((e.package_id, e.reason) for e in report.unassigned))


def test_why_the_whole_chain_cannot_be_run_on_this_morning(built, chained,
                                                           seeded):
    """The finding, pinned so it is not rediscovered as a decomposition bug.

    Slices 1 and 2 build **tomorrow's** pool out of today's inflow: 4,140
    envelopes. §10's stated morning is 3,100. They are different cohorts of
    different days, not the same figure computed two ways — so feeding the
    chain here would compare a pool against one it was never meant to produce,
    and the mismatch would read as a decomposition failure.

    That is a fact about §10's worked example, not about the slices: the
    document's morning is not what the document's own pickup day yields. It
    belongs in a spec note, and it is why the equality test runs from an empty
    start with the deviation stated rather than being run here.
    """
    _, positioned, _, _ = chained
    report, _, _, _, _ = seeded

    slice_built = sum(len(ids) for ids in positioned.positioned.values())

    assert slice_built == 4140
    assert report.tally.ready_pool == 3100
    assert slice_built != report.tally.ready_pool, (
        "if these ever agree, the chain can be run on §10's morning directly "
        "and this test should be replaced by that comparison")
