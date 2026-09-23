"""E2E-2 — slice 2, hub to depots: the acceptance rows.

Every row of `docs/e2e/e2e-2-hub-to-depots.md` §8, one test each, named by its
row id and citing the document. The scenario and expectation are read from the
table by `tests.e2e.rows` rather than restated here, so a reworded row changes
what the test claims instead of leaving it asserting text the document no
longer carries.

`tests/fixtures/peak_day_transfers.py` supplies the night: §10's day plus
thirty transfer candidates, 28 of which §7.1 permits. It is built from the
document's figures rather than from what the planner emits, which is the whole
point of keeping it separate from the code under test.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta

import pytest

from ddn import assumptions, linehaul, processing, returns
from ddn.allocation import capacity_for
from ddn.e2e import hub_to_depots
from ddn.e2e.handoff import ReadyPool
from ddn.e2e.hub_to_depots.scenario import Scenario
from ddn.linehaul import MAX_LOAD_G
from ddn.model.records import TransferReason
from tests.e2e import rows
from tests.fixtures import peak_day_transfers

FX = peak_day_transfers.load()
UNLOAD = assumptions.FACILITY_UNLOAD_MIN * 60


def test_the_document_still_carries_every_row_this_file_covers():
    """A row deleted from the table would take its test with it silently."""
    found = [r for r in rows.ROWS if r.startswith("B")]
    assert len(found) == rows.EXPECTED["B"] == 9


# ------------------------------------------------------------- the harness

def _night(*, transfers=(), returning=(), envelopes=None, vans=None):
    """§5.3's planner on the fixture's night, with one thing varied."""
    return linehaul.plan(
        list(FX.depots),
        list(FX.hub_loads if envelopes is None else envelopes),
        list(FX.vans if vans is None else vans),
        transfers=list(transfers), returning=list(returning),
        transit=FX.transit, unload_seconds=UNLOAD)


def _through_the_slice(*, transfers=(), returning=(), straddling=()):
    """The same night through `ddn/e2e/hub_to_depots`, as E2E-1 would feed it."""
    pool = {FX.hub["id"]: (), **{
        d["id"]: tuple(e for e in FX.hub_loads if e["facility_id"] == d["id"])
        for d in FX.depots}}
    ready = ReadyPool(collection_day=FX.clock_day, ready=pool)
    return hub_to_depots.run(
        Scenario(facilities=[FX.hub, *FX.depots], vans=list(FX.vans),
                 transit=FX.transit, transfers=list(transfers),
                 returning=list(returning), straddling=tuple(straddling),
                 collection_day=FX.clock_day, delivery_day=FX.day), ready)


def _ready_pool():
    return {FX.hub["id"]: (), **{
        d["id"]: tuple(e for e in FX.hub_loads if e["facility_id"] == d["id"])
        for d in FX.depots}}


def _legs(plan):
    return [(leg.from_facility, leg.to_facility, leg.departure, leg.arrival)
            for trip in plan.trips for leg in trip.legs]


# ----------------------------------------------------------------- the rows

def test_b1():
    """E2E-2 §8 row B1, e2e-2-hub-to-depots.md.

    Scenario: §10 day: 4,550 Ready; 1,600 hub-direct; 2,950 depot-bound;
    4 held vans + 3 released, one late van; D6 at 4 h transit
    Expected: All 4,550 positioned by each facility's morning release; D6 load
    departs on an overnight run and its van is unavailable next morning; no
    leg over 500 kg; violation list empty
    """
    positioned, _ = _through_the_slice()
    night = _night()

    # Every depot-bound envelope is on a circuit or rolled with a reason, and
    # §1's boundary is a partition: an envelope in neither is one nobody is
    # looking for.
    carried = {pid for trip in night.trips for pid in trip.package_ids}
    rolled = {pid for ids in night.rolled.values() for pid in ids}
    assert carried | rolled == {e["package_id"] for e in FX.hub_loads}
    assert not carried & rolled

    assert positioned.violations == (), "§13.1's list, from the day check"
    for trip in night.trips:
        for leg in trip.legs:
            assert leg.weight_g <= MAX_LOAD_G, "§7.1's 500 kg"

    # "departs on an overnight run": D6's circuit leaves after midnight on the
    # collection day's clock, which is what an overnight run *is* here.
    d6 = [trip for trip in night.trips
          if "D6" in {leg.to_facility for leg in trip.legs} | {trip.destination}]
    assert d6, "no van reached D6"
    assert any(trip.departure > 24 * 3600 for trip in d6), (
        "D6 is four hours out and its release is the next morning; a circuit "
        "leaving before midnight would not be the overnight run the row names")


def test_b2():
    """E2E-2 §8 row B2, e2e-2-hub-to-depots.md.

    Scenario: No transfers, no returns
    Expected: Plan leg-for-leg identical to the single-destination baseline
    (regression, Task 8)
    """
    # **This compared `_night()` with `_night(transfers=(), returning=())`
    # and `_night` defaults both** — a self-comparison that could not fail.
    # The row's real content is §5.3.1's shape: before Task 8 a trip was one
    # hub-to-depot leg, and the regression is that the multi-leg *capability*
    # leaves that shape alone when nothing needs it.
    plain = _night()

    def depots_called(trip):
        return [leg.to_facility for leg in trip.legs
                if leg.to_facility != FX.hub["id"]]

    assert plain.trips, "an empty night would make the rest vacuous"
    assert all(len(depots_called(trip)) == 1 for trip in plain.trips), (
        "§5.3.1: with nothing to carry between depots, a circuit is one "
        "hub-to-depot run and the leg home")
    assert plain.declined == ()
    assert plain.returned == ()
    assert all(leg.transfer_ids == () and leg.return_ids == ()
               for trip in plain.trips for leg in trip.legs)

    # And the assertion is not vacuous: the same night with transfers aboard
    # does produce multi-depot circuits, so "one depot" is a property of the
    # empty night rather than of this planner.
    carrying = _night(transfers=FX.requests)
    assert any(len(depots_called(trip)) > 1 for trip in carrying.trips), (
        "no circuit ever calls twice, so B2 asserts nothing about Task 8")


def test_b3():
    """E2E-2 §8 row B3, e2e-2-hub-to-depots.md.

    Scenario: 30 transfers layered on B1 (18 address corrections,
    9 misassignments, 3 rebalancing), 2 of which cannot meet SLA
    Expected: 28 carried on inter-depot legs and arrive before deadline;
    2 reported to returns; hub-origin loads not displaced except by
    higher-priority transfers
    """
    split = {reason: sum(c.reason is reason for c in FX.candidates)
             for reason in TransferReason}
    assert split[TransferReason.ADDRESS_CORRECTION] == 18
    assert split[TransferReason.MISASSIGNMENT] == 9
    assert split[TransferReason.REBALANCING] == 3
    assert len(FX.requests) == 28 and len(FX.to_returns) == 2

    night = _night(transfers=FX.requests)
    carried = set(night.transfers_carried)
    declined = {d.transfer_id for d in night.declined}

    assert carried | declined == {r.transfer_id for r in FX.requests}
    assert not carried & declined

    # "arrive before deadline" — checked per transfer against the leg that
    # carried it, not as an aggregate.
    by_id = {r.transfer_id: r for r in FX.requests}
    midnight = datetime.combine(FX.clock_day, time())
    for trip in night.trips:
        for leg in trip.legs:
            arrival = midnight + timedelta(seconds=leg.arrival)
            for transfer_id in leg.transfer_ids:
                assert by_id[transfer_id].makes(arrival), (
                    f"{transfer_id} arrived after its deadline")

    # "hub-origin loads not displaced": the same envelopes travel with the
    # transfers aboard as without them.
    assert (sum(len(t.package_ids) for t in night.trips)
            == sum(len(t.package_ids) for t in _night().trips))


def test_b4():
    """E2E-2 §8 row B4, e2e-2-hub-to-depots.md.

    Scenario: Van-hours short by one circuit
    Expected: Dropped loads are the lowest-priority; every SLA-today transfer
    and envelope is carried; rolled list carries reason "no van"
    """
    full = _night()
    reached = {trip.destination for trip in full.trips}
    assert len(reached) > 1, "a single-depot night cannot be short by a circuit"

    short = _night(vans=list(FX.vans)[:1])
    missed = {facility for facility, ids in short.rolled.items() if ids}

    assert missed, "one van reached every depot; the row cannot bite"
    assert all(short.reason_for(f) == linehaul.NO_VAN for f in missed), (
        "§9.2 asks for the reason, and 'no van' is not 'not ready in time' — "
        "one is a fleet too small, the other a hub too slow"
    )

    # --- "dropped loads are the lowest-priority" (§5.3.2, §8.1)
    #
    # §10's envelopes are 200 g each and carry no score, so the fixture's own
    # night never fills a van -- its worst leg is 30.7% of the 500 kg -- and
    # nothing is ever dropped for capacity. The weights and scores below are
    # the test's, not the document's: sixty of D1's envelopes at 10 kg are
    # 600 kg against a 500 kg van, scored 60 down to 1 so that "lowest" names
    # something checkable rather than whichever ten the loop reached last.
    ready = min(int(e["expected_ready_at"]) for e in FX.hub_loads)
    d1 = [e for e in FX.hub_loads if e["facility_id"] == "D1"][:60]
    scored = [dict(e, weight_g=10_000, priority=float(len(d1) - i),
                   expected_ready_at=ready)
              for i, e in enumerate(d1)]
    score_of = {e["package_id"]: e["priority"] for e in scored}

    heavy = _night(envelopes=scored)
    dropped = set(heavy.rolled.get("D1", ()))
    assert dropped, "500 kg offered against a 500 kg van has to drop something"
    assert heavy.reason_for("D1") == linehaul.OVER_CAPACITY
    assert (max(score_of[p] for p in dropped)
            < min(v for k, v in score_of.items() if k not in dropped)), (
        "every dropped envelope must score below every carried one, or "
        "'lowest-priority' names something the planner did not do"
    )

    # --- "every SLA-today transfer and envelope is carried" (§5.3.2, §6.1)
    #
    # The clause is quoted whole because only its first half is asserted.
    #
    # v0.16 retired the row's literal wording for the transfer half: §7.1
    # forbids *raising* a transfer for an envelope due today, because
    # line-haul runs on D and delivery on D+1. What replaced it is the hard
    # inclusion this asserts — a transfer whose deadline was set by the SLA
    # rather than by the receiving depot's morning release. The fixture
    # carries none (all 28 deadlines sit on the release), so one is built here.
    #
    # The envelope half asserts nothing, and that is a decision rather than an
    # omission: `compete`'s docstring records it. §6.1's SLA-today has no
    # envelope equivalent the planner can express — `plan` holds no reference
    # date to compare §9.1's `sla_date` against, and by the same D/D+1
    # reasoning an envelope due *today* cannot be served by a line-haul
    # arriving tomorrow morning either. Whether the row's wording should
    # follow v0.16 the way the spec did is Luis's call, not this test's.
    #
    # The pair differs in the deadline and nothing else: same weight, same
    # score, deliberately below every envelope's. The soft twin loses its seat
    # and the hard inclusion keeps it, so the deadline is what carried it.
    low = min((t for t in FX.requests if t.from_facility_id == "D1"),
              key=lambda t: t.priority)
    twin = replace(low, weight_g=10_000, priority=0.5)
    inclusion = replace(twin, deadline=twin.deadline - timedelta(hours=1))
    rest = [t for t in FX.requests if t.transfer_id != low.transfer_id]

    # Fifty is the tight fit: exactly 500 kg of envelopes, so whether the
    # transfer rides is decided by the deadline and not by leftover room.
    seats = scored[:50]
    with_hard = _night(envelopes=seats, transfers=[inclusion, *rest])
    with_soft = _night(envelopes=seats, transfers=[twin, *rest])

    assert low.transfer_id in {tid for trip in with_hard.trips
                               for tid in trip.transfer_ids}, (
        "a hard inclusion rides even scoring below every envelope aboard"
    )
    assert {d.transfer_id: d.reason for d in with_soft.declined}[
        low.transfer_id] == linehaul.OVER_CAPACITY, (
        "its soft twin loses the same seat — otherwise the van was never "
        "short and the hard inclusion was never tested"
    )


def test_b5():
    """E2E-2 §8 row B5, e2e-2-hub-to-depots.md.

    Scenario: 200 envelopes still in assembly at 16:00, expected Ready by
    17:30; D2's latest departure 18:00
    Expected: The D2 van waits and carries them; a van for D6 (latest
    departure 15:00) does not

    **The row's clock cannot be taken literally, and the reason is §5.**
    `latest_departure` is `DAY + release − transit − unload`, because line-haul
    runs on D and the release is D+1's morning — so *every* depot's deadline is
    past 24 h and a "latest departure of 15:00" does not exist. On the
    fixture's own depots the deadlines are D1 at 30 h and D6 at 26.5 h.

    What the row is really about is the relationship: envelopes that become
    Ready after one depot's deadline and before another's. So the ready time
    sits between the two, and D1 plays the patient van while D6 plays the one
    that cannot wait.
    """
    patient, hurried = FX.depots[0], FX.depots[5]
    assert (patient["id"], hurried["id"]) == ("D1", "D6")

    late_deadline = linehaul.latest_departure(patient, unload_seconds=UNLOAD)
    early_deadline = linehaul.latest_departure(hurried, unload_seconds=UNLOAD)
    ready_at = (late_deadline + early_deadline) // 2
    assert early_deadline < ready_at <= late_deadline

    waited = [{"package_id": f"PKG-L{i}", "facility_id": "D1",
               "expected_ready_at": ready_at, "weight_g": 20,
               "priority": 100.0} for i in range(200)]
    could_not = [dict(e, package_id=e["package_id"].replace("L", "E"),
                      facility_id="D6") for e in waited]

    night = linehaul.plan([patient, hurried], waited + could_not,
                          list(FX.vans), transit=FX.transit,
                          unload_seconds=UNLOAD)
    carried = {pid for trip in night.trips for pid in trip.package_ids}

    assert {e["package_id"] for e in waited} <= carried, "the van waited"
    assert not {e["package_id"] for e in could_not} & carried
    assert night.reason_for("D6") == linehaul.NOT_READY_IN_TIME, (
        "§9.2 wants the reason, and 'not ready in time' is a hub too slow "
        "where 'no van' is a fleet too small")


def test_b6():
    """E2E-2 §8 row B6, e2e-2-hub-to-depots.md.

    Scenario: 40 zip-centroid envelopes within EQUIDISTANT_MARGIN_M of two
    depots
    Expected: Kept at hub and flagged; not on any van; appear in rolled list
    with reason "held-straddle"

    **Reported in `held`, not in `rolled`, and the row's own §1 is why.** The
    boundary is "either positioned ... or explicitly rolled", and a straddler
    is positioned — at the hub, which §2 item 2 insists is a dispatching
    facility like any other. Calling it rolled puts one envelope in both
    halves of the partition B1 asserts. Flagged for the document; the reason
    string is the row's.
    """
    straddlers = tuple(f"PKG-STRADDLE-{i}" for i in range(40))
    flagged = tuple(processing.Sorted(package_id=p, facility_id="D2",
                                      runner_up="D3", margin_m=500.0,
                                      straddles=True) for p in straddlers)

    kept, held = processing.keep_straddlers_at_hub(flagged, hub_id="HUB")

    assert held == straddlers
    assert {s.facility_id for s in kept} == {"HUB"}, "not committed to a depot"

    positioned, _ = _through_the_slice(straddling=straddlers)
    reported = {e.package_id: e.reason for e in positioned.held}

    assert set(reported) == set(straddlers)
    assert set(reported.values()) == {processing.HELD_STRADDLE}
    assert not set(straddlers) & {pid for ids in positioned.positioned.values()
                                  for pid in ids}, "not on any van"


def test_b7():
    """E2E-2 §8 row B7, e2e-2-hub-to-depots.md.

    Scenario: 50 rejected at D3 during the day
    Expected: Ride D3's return leg; arrive at hub; appear in *tomorrow's*
    return run input, not tonight's
    """
    rejected = [{"package_id": f"PKG-R{i}", "facility_id": "D3",
                 "customer_id": f"CUST-{i}", "previous_outcome": "Rejected",
                 "customer_lat": 9.9, "customer_lon": -84.1, "weight_g": 20}
                for i in range(50)]
    assert all(returns.goes_back(e) for e in rejected)

    night = _night(returning=rejected)
    home = [leg for trip in night.trips for leg in trip.legs if leg.return_ids]

    assert home, "nothing rode home; §5.5's depot rejects were dropped"
    assert all(leg.to_facility == FX.hub["id"] for leg in home), (
        "a return rides to the hub, not between depots")
    assert set(night.returned) == {e["package_id"] for e in rejected}

    # "tomorrow's return run input, not tonight's": they are at D3 while
    # tonight's run departs the hub, so §5.5's own gate excludes them.
    assert returns.sites(rejected, hub_id=FX.hub["id"]) == []


def test_b8():
    """E2E-2 §8 row B8, e2e-2-hub-to-depots.md.

    Scenario: A van released from pickups at 15:10 with unloading 20 min
    Expected: Not assigned a leg departing before 15:30
    """
    released_at = 15 * 3600 + 10 * 60
    unload = 20 * 60
    van = dict(FX.vans[0], vehicle_id="VAN-LATE", role="pickup",
               linehaul_release_at=released_at)

    night = linehaul.plan(list(FX.depots), list(FX.hub_loads), [van],
                          transit=FX.transit, unload_seconds=unload)
    departures = [trip.departure for trip in night.trips
                  if trip.van_id == "VAN-LATE"]

    assert departures, "the van was given no leg at all, so nothing is proved"
    assert min(departures) >= released_at + unload, (
        "§7.1: a van does not depart on line-haul until it is back *and* "
        "unloaded; both, not either")


def test_b9():
    """E2E-2 §8 row B9, e2e-2-hub-to-depots.md.

    Scenario: Rebalancing: D1 projected 900 vs 600 capacity, D2 400 vs 450
    Expected: Proposal to transfer ≤ 50 lowest-priority D1 envelopes to D2 if
    a D1→D2 leg fits; otherwise no proposal and D1 rolls by priority
    """
    pool = {"D1": [{"package_id": f"PKG-{i:04}", "priority": float(i)}
                   for i in range(900)]}
    projected = {"D1": 900, "D2": 400}
    capacity = {"D1": capacity_for(24), "D2": capacity_for(18)}
    assert (capacity["D1"], capacity["D2"]) == (600, 450)

    offered = linehaul.rebalancing(projected, capacity, pools=pool,
                                   reaches=lambda a, b: True)
    proposal, = offered

    assert (proposal.from_facility_id, proposal.to_facility_id) == ("D1", "D2")
    assert proposal.leg == ("D1", "D2")
    assert len(proposal.package_ids) == 50, "the row caps the offer at 50"
    assert proposal.package_ids == tuple(f"PKG-{i:04}" for i in range(50)), (
        "the lowest-priority, because §8.1 ranks what is delivered and this "
        "is its mirror — an envelope served tomorrow should not travel")

    # The 50 above is `min(room, over)` and room happens to *be* 50, so the
    # cap has not been exercised: deleting it leaves this green. Give the
    # neighbour more room than the cap and it has to bind on its own.
    roomy = linehaul.rebalancing({"D1": 900, "D2": 100}, capacity, pools=pool,
                                 reaches=lambda a, b: True)
    assert len(roomy[0].package_ids) == 50, (
        "room for 350 and 300 over capacity; only the cap holds this to 50")

    # "otherwise no proposal": a pair with no leg is a silence, not an error,
    # and D1 then rolls by priority in `select` as it would have anyway.
    assert linehaul.rebalancing(projected, capacity, pools=pool,
                                reaches=lambda a, b: False) == []


@pytest.mark.parametrize("row_id", sorted(r for r in rows.ROWS
                                          if r.startswith("B")))
def test_every_b_row_has_a_test_named_for_it(row_id):
    """The rows and the tests above cannot drift apart silently."""
    assert f"test_{row_id.lower()}" in globals(), f"{row_id} has no test"
    assert rows.document(row_id) == "e2e-2-hub-to-depots.md"
    assert isinstance(rows.expected(row_id), str) and rows.expected(row_id)


def test_slice_2_offers_rebalancing_proposals_and_applies_none():
    """e2e-2 §5 row 5, which nothing produced until now.

    §5.3.2 gives the choice between moving envelopes and moving motorbikes to
    §4.2's cost comparison — and that needs a relocation cost
    `docs/assumptions.md` does not carry — so this slice offers and decides
    nothing. A planner that moved envelopes on its own authority would be
    making an allocation decision from inside §5.3, which is the one thing
    e2e-2 §3 rules out by name.
    """
    from tests.fixtures import peak_day

    ready = ReadyPool(collection_day=FX.clock_day, ready=_ready_pool())
    base = Scenario(facilities=[FX.hub, *FX.depots], vans=list(FX.vans),
                    transit=FX.transit, collection_day=FX.clock_day,
                    delivery_day=FX.day)

    pool, _ = hub_to_depots.run(
        replace(base, bikes={d["id"]: peak_day.BIKE_ALLOCATION[d["id"]]
                             for d in FX.depots}), ready)
    plain, _ = hub_to_depots.run(base, ready)

    assert plain.rebalancing == (), "no bikes given means the trigger is not run"
    for proposal in pool.rebalancing:
        assert proposal.from_facility_id != proposal.to_facility_id
        assert proposal.package_ids
        assert proposal.leg == (proposal.from_facility_id,
                                proposal.to_facility_id)

    # Nothing moved: the positioned pool is identical with and without the
    # proposals, which is what "offers, not decisions" has to mean.
    assert pool.positioned == plain.positioned
