"""§5.3.2's inter-depot transfers: the lifecycle, the circuits, the bullets."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from ddn import linehaul
from ddn.linehaul import circuit
from ddn.linehaul.circuit import MAX_LOAD_G
from ddn.model import Status, TransferReason, TransferRequest, may
from ddn.solver_adapter import Day, check_day_constraints, postcheck

HOUR = 3600
TODAY = date(2026, 9, 16)
# §3.1 and §5.6 give clock times with no zone; the operation runs in one place,
# so datetimes here are naive and local, as they are throughout the modules.
RELEASE = datetime.combine(date(2026, 9, 17), time(7))
RAISED = datetime.combine(TODAY, time(18))


#: `circuit.choose`'s inputs for the ranking rows below. `release_of` carries
#: `DAY + release` as `linehaul.plan` builds it — the release is the *next*
#: morning, so it sits past 24 h and `_ranked` reads the time of day back out
#: with `% DAY`.
RELEASES = {"D1": circuit.DAY + 7 * HOUR, "D3": circuit.DAY + 7 * HOUR}
HUB_TRANSIT = {"D1": 30 * 60, "D3": 30 * 60}


def TRANSIT(origin: str, destination: str) -> int:
    """Flat inter-depot travel: these rows are about ranking, not geography."""
    return 30 * 60


def depot(name: str, *, transit_min: int, release_h: int = 7) -> dict:
    return {"id": name, "route_release_time": release_h * HOUR,
            "transit_from_hub_min": transit_min}


def transfer(transfer_id: str = "T1", *, frm: str = "D1", to: str = "D3",
             deadline: datetime = RELEASE, grams: int = 200,
             priority: float = 100.0,
             reason: TransferReason = TransferReason.MISASSIGNMENT):
    return TransferRequest(transfer_id, f"PKG-{transfer_id}", frm, to, reason,
                           RAISED, deadline, weight_g=grams,
                           priority=priority)


def envelopes(facility: str, count: int, *, grams: int = 200,
              priority: float = 0.0) -> list[dict]:
    return [{"package_id": f"{facility}-{i}", "facility_id": facility,
             "expected_ready_at": 16 * HOUR, "weight_g": grams,
             "priority": priority}
            for i in range(count)]


# ------------------------------------------------------------- §5.2.6

def test_the_lifecycle_draws_the_transfer_path():
    """§5.2.6: "Transfer requested → In transfer → Ready (at new depot)"."""
    assert may(Status.POSTPONED, Status.TRANSFER_REQUESTED)
    assert may(Status.TRANSFER_REQUESTED, Status.IN_TRANSFER)
    assert may(Status.IN_TRANSFER, Status.READY)


def test_an_envelope_in_transfer_is_not_routable():
    """§7.1: "not routable until it arrives at the destination depot"."""
    assert not may(Status.IN_TRANSFER, Status.DISPATCHED)
    assert Status.IN_TRANSFER not in __import__(
        "ddn.model.lifecycle", fromlist=["ROUTABLE"]).ROUTABLE


def test_a_transfer_that_cannot_make_its_deadline_goes_back_instead():
    """§5.3.2: "otherwise it is returned to the customer via the hub"."""
    assert may(Status.TRANSFER_REQUESTED, Status.RETURN_RUN)


def test_a_transfer_to_the_same_depot_is_refused():
    with pytest.raises(ValueError, match="to itself"):
        transfer(frm="D1", to="D1")


def test_the_deadline_is_what_decides():
    """§9.1: min(receiving depot's next morning release, SLA date)."""
    one = transfer(deadline=RELEASE)
    assert one.makes(RELEASE - timedelta(minutes=1))
    assert not one.makes(RELEASE + timedelta(minutes=1))


# ------------------------------------------------------------- §5.3.2

def transit(_a: str, _b: str) -> int:
    """Inter-depot seconds. §3.1 gives none, so the caller supplies them."""
    return 40 * 60


def test_a_transfer_rides_a_circuit_through_its_origin():
    """§5.3.2: transfer loads are "picked up at earlier depots"."""
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 3), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()],
                          transit=transit)
    trip, = night.trips
    assert trip.transfer_ids == ("T1",)
    assert [(leg.from_facility, leg.to_facility) for leg in trip.legs] == [
        ("HUB", "D1"), ("D1", "D3"), ("D3", "HUB")], (
        "§5.3.2: the circuit starts and ends at the hub")
    assert night.declined == ()


def test_the_circuit_departs_early_enough_for_its_last_stop():
    """§5.3 departs as late as possible — for the *whole* circuit.

    A van timed only against D1 leaves at D1's own latest departure and then
    cannot reach D3 before its release. §5.3.2's constraint is arrival before
    the receiving depot's morning release, which is a property of the circuit.
    """
    depots = [depot("D1", transit_min=30), depot("D3", transit_min=45)]
    alone = linehaul.plan(depots, envelopes("D1", 3), [{"vehicle_id": "V1"}],
                          unload_seconds=1800)
    with_transfer = linehaul.plan(depots, envelopes("D1", 3),
                                  [{"vehicle_id": "V1"}], unload_seconds=1800,
                                  transfers=[transfer()], transit=transit)

    assert with_transfer.trips[0].departure < alone.trips[0].departure
    # The last *delivering* leg. The circuit now closes with the run home,
    # which arrives after every release and is not what this bounds.
    last = with_transfer.trips[0].legs[-2]
    assert last.to_facility == "D3"
    assert last.arrival <= linehaul.DAY + 7 * HOUR


# --------------------------------------------------------------- §8.3

def test_the_night_is_van_hours_until_the_van_is_back_at_the_hub():
    """§5.3: "the van (and driver) are unavailable until they return".

    `Trip.arrival` is arrival at the *first* depot (`plan.py:350` reads it off
    `legs[0]`), so a span closed there stops counting the moment the first
    load is dropped and charges §8.3 nothing for the rest of the circuit.
    The van is out past even the last leg: `returns` adds the unload at the
    hub, and the spelling below is the leg plus that unload rather than
    `trip.returns`, so it is the claim being checked and not the code restated.
    """
    unload = 1800
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 3), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=unload, transfers=[transfer()],
                          transit=transit)
    trip, = night.trips

    assert trip.legs[-1].to_facility == "HUB", (
        "§5.3.2: the circuit ends at the hub, so the last leg is the run home")
    assert night.van_seconds == trip.legs[-1].arrival + unload - trip.departure
    assert trip.arrival < trip.legs[-1].arrival, (
        "arrival at the first depot is not the end of the shift; a span closed "
        "there would under-count §8.3 by the rest of the circuit")


def test_transfer_van_hours_are_the_legs_added_not_the_transfers_carried():
    """§8.3: line-haul hours "including inter-depot legs ... transfers **add
    to it**" -- added legs, not added requests.

    The marginal is the night as planned less the same night with no transfers
    at all. One transfer to D3 appends a stop and costs the detour; the
    twentieth rides the leg the first one already bought, and costs nothing.
    A per-request charge, or one that bills the whole circuit rather than the
    difference, cannot produce the same number at n=1 and n=20.
    """
    depots = [depot("D1", transit_min=30), depot("D3", transit_min=45)]

    def marginal(count: int) -> tuple[int, int]:
        args = (depots, envelopes("D1", 3), [{"vehicle_id": "VAN-01"}])
        night = linehaul.plan(*args, unload_seconds=1800, transit=transit,
                              transfers=[transfer(f"T{i}") for i in range(count)])
        alone = linehaul.plan(*args, unload_seconds=1800)
        return (night.van_seconds - alone.van_seconds,
                len(night.transfers_carried))

    first, carried_first = marginal(1)
    twentieth, carried_twentieth = marginal(20)

    assert (carried_first, carried_twentieth) == (1, 20), (
        "all twenty have to be carried, or the flat cost below is only telling "
        "us the planner refused nineteen of them")
    assert first > 0, "the first transfer buys a leg the plan would not have flown"
    assert twentieth == first, (
        f"twenty transfers cost {twentieth}s and one costs {first}s; §8.3 "
        "charges for the legs added, and both nights added the same one leg")


def test_a_transfer_nobody_passes_is_declined_with_a_reason():
    """§9.2: "transfers not carried, with reason"."""
    night = linehaul.plan([depot("D1", transit_min=30)], envelopes("D1", 2),
                          [{"vehicle_id": "VAN-01"}], unload_seconds=1800,
                          transfers=[transfer(frm="D5", to="D6")],
                          transit=transit)
    assert night.trips[0].transfer_ids == ()
    assert [d.reason for d in night.declined] == [linehaul.NO_VAN_LEG]


def test_without_inter_depot_transit_a_transfer_is_refused_not_guessed():
    """§3.1 supplies hub transit only; §12 Q8 asks for nothing else."""
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 2), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()])
    assert night.trips[0].transfer_ids == ()
    assert "transit" in night.declined[0].reason


def test_a_transfer_that_would_overload_the_van_is_declined():
    """§7.1: 500 kg across hub-origin, transfer and return envelopes.

    The envelope outscores the transfer (§8.1), so §5.3.2's competition seats
    it first and there is nothing left for the transfer. Before Step 3 the
    priorities were not read at all and the envelope boarded because it was
    hub-origin; the outcome was the same for the wrong reason, which is what
    the score here pins.
    """
    heavy = envelopes("D1", 1, grams=500_000, priority=900.0)
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          heavy, [{"vehicle_id": "VAN-01"}], unload_seconds=1800,
                          transfers=[transfer(grams=1000)], transit=transit)
    assert [d.reason for d in night.declined] == [linehaul.OVER_CAPACITY]


def test_hub_origin_load_is_held_to_the_same_five_hundred_kilos():
    """§7.1's combined load bounds hub-origin envelopes too, not just transfers.

    This is the hole Step 3 closed. `plan` used to board a depot's whole pool
    unconditionally and offer `choose` only the remainder, so a transfer could
    be declined `OVER_CAPACITY` on a van the planner had *itself* loaded past
    500 kg. Three envelopes at 200 kg were 600 kg on one leg and the plan
    reported no trouble at all.

    Two ride, the third is rolled, and no leg exceeds the cap.
    """
    night = linehaul.plan([depot("D1", transit_min=30)],
                          envelopes("D1", 3, grams=200_000),
                          [{"vehicle_id": "VAN-01"}], unload_seconds=1800)

    legs = [leg for trip in night.trips for leg in trip.legs]
    assert [leg.weight_g for leg in legs] == [400_000, 0], "out laden, home empty"
    assert not any(leg.overloaded for leg in legs)
    assert night.rolled == {"D1": ("D1-2",)}
    assert night.reasons["D1"] == linehaul.OVER_CAPACITY


def test_a_higher_scoring_transfer_takes_the_seat_from_a_hub_origin_envelope():
    """§5.3.2: "hub-origin loads and transfers compete", ranked by §8.1.

    The competition is only observable when the van is short and the transfer
    outranks the load, which is the case §10's data never reaches -- its worst
    leg is 30.7% of the cap. So it is built here: 400 kg of hub-origin envelopes
    scoring 1 against a 200 kg transfer scoring 900, on one 500 kg van.

    The transfer boards and the lower-scoring envelope is bumped. Board the
    hub-origin pool first, as `plan` did before Step 3, and the reverse happens:
    the two envelopes take the van and the transfer is declined.
    """
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 2, grams=200_000, priority=1.0),
                          [{"vehicle_id": "VAN-01"}], unload_seconds=1800,
                          transfers=[transfer(grams=200_000, priority=900.0)],
                          transit=transit)

    assert night.trips[0].transfer_ids == ("T1",)
    assert night.declined == ()
    assert night.rolled == {"D1": ("D1-1",)}, "the loser is the lower score"
    assert night.reasons["D1"] == linehaul.OVER_CAPACITY


def test_every_transfer_is_carried_or_declined():
    """The accounting §9.2 asks for: none may quietly vanish."""
    wanted = [transfer("T1"), transfer("T2", frm="D5", to="D6"),
              transfer("T3", grams=500_000)]
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 2), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=wanted, transit=transit)
    carried = {tid for trip in night.trips for tid in trip.transfer_ids}
    declined = {d.transfer_id for d in night.declined}
    assert carried | declined == {"T1", "T2", "T3"}
    assert not (carried & declined)


def test_the_load_falls_as_the_circuit_drops_it():
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 3), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()],
                          transit=transit)
    out, across, home = night.trips[0].legs
    assert out.weight_g == 3 * 200 + 200, "hub load plus the transfer"
    assert across.weight_g == 200, "only the transfer, after D1 is unloaded"
    assert home.weight_g == 0, "empty on the run home; nothing is going back"
    assert not any(leg.overloaded for leg in night.trips[0].legs)


# ------------------------------------------------------------- §7.1

def test_section_7_1_now_enforces_every_bullet():
    assert len(postcheck.BULLETS) == 13
    assert postcheck.NOT_YET_ENFORCED == ()
    assert postcheck.COMBINED_LOAD in postcheck.BULLETS
    assert postcheck.TRANSFER_WITHIN_SLA in postcheck.BULLETS


def test_the_limit_is_section_4_1s_five_hundred_kilograms():
    """§4.1: "Van | 500 kg". §7.1 repeats it for a leg's combined load.

    Pinned as a literal, because every other test of this bullet builds its
    violating input *relative* to the constant -- `500_001`, previously
    `MAX_LOAD_G + 1`. A relative input moves with the limit, so the limit was
    free: raising `MAX_LOAD_G` tenfold left the whole suite green. The
    mechanism was covered and the number was not, which is the same shape as
    the bike's 35 being pinned at `test_returns.py`.
    """
    assert linehaul.MAX_LOAD_G == 500_000


def test_an_overloaded_leg_is_a_violation():
    """§7.1's combined-load bullet, which only a leg can break."""
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D1", package_ids=(), departure=0,
        arrival=1, returns=2,
        legs=(linehaul.Leg("HUB", "D1", 0, 1, weight_g=500_001),)),))
    found = check_day_constraints(Day(today=TODAY, linehaul=night))
    assert postcheck.COMBINED_LOAD in {v.bullet for v in found}


def test_a_transfer_arriving_after_its_deadline_is_a_violation():
    """§7.1: it should have gone to the return run instead."""
    late = transfer(deadline=datetime.combine(date(2026, 9, 17), time(3)))
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D3", package_ids=(), departure=0,
        arrival=1, returns=2, transfer_ids=("T1",),
        legs=(linehaul.Leg("D1", "D3", 0, linehaul.DAY + 6 * HOUR,
                           transfer_ids=("T1",), weight_g=200),)),))
    found = check_day_constraints(
        Day(today=TODAY, linehaul=night, transfers=[late]))
    assert postcheck.TRANSFER_WITHIN_SLA in {v.bullet for v in found}


def test_a_transfer_arriving_in_time_is_no_violation():
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D3", package_ids=(), departure=0,
        arrival=1, returns=2, transfer_ids=("T1",),
        legs=(linehaul.Leg("D1", "D3", 0, linehaul.DAY + 6 * HOUR,
                           transfer_ids=("T1",), weight_g=200),)),))
    found = check_day_constraints(
        Day(today=TODAY, linehaul=night, transfers=[transfer()]))
    assert postcheck.TRANSFER_WITHIN_SLA not in {v.bullet for v in found}


# --------------------- §5.3.2's Priority rule (v0.16), under a short circuit

def test_a_full_circuit_declines_the_lowest_priority_transfer():
    """§5.3.2: "ranked by the same priority score as delivery (§8.1)".

    `choose` used to take the caller's list order and decline once full, so
    which transfers rode was decided by list position — and `simulation.day`
    passes `[*state.transfers, *raised]`, which made it "yesterday's first"
    the moment v0.15 gave it a queue. That is a policy and nobody chose it.
    """
    heavy = MAX_LOAD_G // 2 + 1
    low = transfer("T-low", priority=10.0, grams=heavy)
    high = transfer("T-high", priority=900.0, grams=heavy)

    for order in ([low, high], [high, low]):
        _, carried, declined = circuit.choose(
            ["D1"], order, transit=TRANSIT, hub_transit=HUB_TRANSIT,
            release_of=RELEASES, unload_seconds=0, earliest_departure=0,
            carried_g=0)

        assert [t.transfer_id for t in carried] == ["T-high"]
        assert [d.transfer_id for d in declined] == ["T-low"]
        assert declined[0].reason == circuit.OVER_CAPACITY


def test_the_order_of_the_caller_s_list_no_longer_decides():
    """Two runs of one night decline the same transfers, whatever the order.

    The same property `lastmile.select` insists on, and for the same reason:
    a plan that depends on list position is one nobody can reproduce.
    """
    third = MAX_LOAD_G // 3 + 1
    pool = [transfer(f"T-{i}", priority=float(i), grams=third) for i in range(4)]

    def ran(order):
        _, carried, _ = circuit.choose(
            ["D1"], order, transit=TRANSIT, hub_transit=HUB_TRANSIT,
            release_of=RELEASES, unload_seconds=0, earliest_departure=0,
            carried_g=0)
        return [t.transfer_id for t in carried]

    assert ran(pool) == ran(list(reversed(pool))) == ["T-3", "T-2"]


def test_a_transfer_due_at_the_next_release_rides_whatever_it_scores():
    """§5.3.2 v0.16's hard inclusion, and why it is not "SLA-today".

    The old wording said a transfer for an SLA-today envelope was a hard
    inclusion — a case §7.1 forbids, because line-haul runs on D and delivery
    on D+1, so an envelope due today cannot be served by a transfer at all and
    `_raise_transfers` never raises one. v0.16 names the envelope due on the
    receiving depot's **next morning release** instead, which is the earliest
    a transfer can still serve.

    §9.1's `deadline` is `min(release, SLA date)`, so the case is the SLA
    *winning* that minimum: its deadline falls before the release. A first
    version of this test gave the slack transfer `RELEASE + 3 days`, which has
    the same time of day as the release — so both looked due and the score
    decided, which is what "hard" rules out.
    """
    heavy = MAX_LOAD_G // 2 + 1
    due = transfer("T-due", priority=1.0, grams=heavy,
                   deadline=datetime.combine(RELEASE.date(), time()))
    rich = transfer("T-rich", priority=9999.0, grams=heavy, deadline=RELEASE)

    _, carried, declined = circuit.choose(
        ["D1"], [rich, due], transit=TRANSIT, hub_transit=HUB_TRANSIT,
        release_of=RELEASES, unload_seconds=0, earliest_departure=0,
        carried_g=0)

    assert [t.transfer_id for t in carried] == ["T-due"], (
        "the hard inclusion lost to a score, which is what 'hard' rules out")
    assert [d.transfer_id for d in declined] == ["T-rich"]


def test_a_transfer_without_a_score_is_refused_not_ranked_last():
    """§9.1 v0.16 declares `priority`; absent is invisible and load-bearing.

    A transfer defaulting to zero sorts last and never rides, silently — the
    same failure `contract.costs` refuses a model that prices nothing for.
    """
    with pytest.raises(ValueError, match="no priority"):
        TransferRequest("T-0", "PKG-0", "D1", "D3",
                        TransferReason.MISASSIGNMENT, RAISED, RELEASE)
