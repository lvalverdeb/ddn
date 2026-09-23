"""§5.3.2's rebalancing choice: the envelopes move, or the bikes do, or neither.

The facts every case is built on, so the arithmetic below is readable: a bike
serves `EFFECTIVE_PER_BIKE` = 25 envelopes a day (§4.2), four bikes at D1 are
100, and a pool of 120 is 20 over. Twenty over needs one bike to cover, and
one bike costs `BIKE_RELOCATION_MIN` = 45 minutes -- so 45 van-minutes is the
line either side of which the comparison flips.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from ddn import assumptions
from ddn.allocation import Arm, capacity_for, decide, place
from ddn.linehaul.plan import Proposal
from ddn.model.records import TransferReason, TransferRequest

BIKES = [f"MOTO-{n:03d}" for n in range(1, 9)]
TODAY = datetime.combine(date(2026, 9, 23), time())
#: Before D2's morning release, so §5.3.2's arrival clause is satisfied.
ARRIVES = datetime.combine(date(2026, 9, 24), time(5))
DEADLINE = datetime.combine(date(2026, 9, 24), time(7))
LEG = ("D1", "D2")
OVER = [f"PKG-{n:03d}" for n in range(1, 21)]


@pytest.fixture
def allocation():
    """Four bikes each at D1 and D2, placed on a day one that moved nobody."""
    return place({"D1": 4, "D2": 4}, BIKES)


def requests(package_ids=tuple(OVER), *, deadline=DEADLINE):
    return {pid: TransferRequest(
        transfer_id=f"TR-{pid}", package_id=pid,
        from_facility_id="D1", to_facility_id="D2",
        reason=TransferReason.REBALANCING, created_at=TODAY,
        deadline=deadline, priority=float(index))
        for index, pid in enumerate(package_ids)}


def answer(allocation, **overrides):
    """§5.3.2 asked of one over-full depot, with one knob turned at a time."""
    call = {
        "proposals": [Proposal("D1", "D2", tuple(OVER), LEG)],
        "candidates": requests(),
        "arrivals": {LEG: ARRIVES},
        "added_leg_minutes": {LEG: 0.0},
        "van_hours": 8.0,
        "projected": {"D1": 120, "D2": 50},
        "capacity": {"D1": capacity_for(4), "D2": capacity_for(4)},
    }
    call.update(overrides)
    proposals = call.pop("proposals")
    return decide(proposals, allocation, **call)


def test_the_rate_and_the_cost_are_the_documented_ones():
    """The arithmetic in this file's docstring, held to its sources."""
    assert capacity_for(4) == 100
    assert assumptions.BIKE_RELOCATION_MIN == 45


def test_no_over_full_depot_leaves_the_allocation_untouched(allocation):
    """§5.3.2 is not consulted when nothing is over: T9's "output unchanged".

    Identity rather than equality, because a caller that hands this plan on
    must be able to tell "decided to keep it" from "rebuilt the same thing".
    """
    decision = decide([], allocation, candidates={}, arrivals={},
                      added_leg_minutes={}, van_hours=8.0,
                      projected={"D1": 80, "D2": 50},
                      capacity={"D1": capacity_for(4), "D2": capacity_for(4)})
    assert decision.allocation is allocation
    assert decision.transfers == ()
    assert decision.by_depot == {}
    assert decision.unassigned == {}


def test_a_proposal_for_a_depot_that_is_not_over_is_declined(allocation):
    """A stale offer decides nothing: the overage is read here, not taken."""
    decision = answer(allocation, projected={"D1": 90, "D2": 50})
    assert decision.by_depot == {}
    assert decision.allocation is allocation


def test_an_existing_leg_carries_the_envelopes(allocation):
    """§5.3.2's first clause: a leg that already runs costs no van-minutes."""
    decision = answer(allocation)
    assert decision.by_depot == {"D1": Arm.TRANSFER}
    assert [t.package_id for t in decision.transfers] == OVER
    assert decision.allocation is allocation, "the fleet stayed where it was"
    assert decision.unassigned == {}


def test_a_leg_that_cannot_be_added_falls_to_the_bikes(allocation):
    """Absent from `added_leg_minutes` is "no leg", not "a free one"."""
    decision = answer(allocation, added_leg_minutes={})
    assert decision.by_depot == {"D1": Arm.REALLOCATE}
    assert decision.transfers == ()
    moved = {f: len(ids) for f, ids in decision.allocation.by_facility().items()}
    assert moved == {"D1": 5, "D2": 3}, "one bike covers twenty envelopes"


def test_a_leg_that_will_not_fit_the_days_van_hours_falls_to_the_bikes(
        allocation):
    """§5.3.2's "within van-hours", which is a day's budget and not a wish."""
    decision = answer(allocation, added_leg_minutes={LEG: 200.0},
                      van_hours=1.0)
    assert decision.by_depot == {"D1": Arm.REALLOCATE}


def test_envelopes_that_arrive_after_their_deadline_do_not_ride(allocation):
    """§5.3.2's second clause, via §9.1's deadline on the request itself."""
    decision = answer(allocation, arrivals={LEG: datetime.combine(date(2026, 9, 24), time(9))})
    assert decision.by_depot == {"D1": Arm.REALLOCATE}
    assert decision.transfers == ()


def test_only_the_envelopes_that_make_the_deadline_ride(allocation):
    """A mixed load is not all-or-nothing; the rest are §5.3.2's last clause."""
    candidates = requests(OVER[:12]) | requests(
        OVER[12:], deadline=datetime.combine(date(2026, 9, 24), time(1)))
    decision = answer(allocation, candidates=candidates)
    assert decision.by_depot == {"D1": Arm.TRANSFER}
    assert [t.package_id for t in decision.transfers] == OVER[:12]
    assert decision.unassigned == {"D1": tuple(OVER[12:])}


def test_the_cheaper_arm_wins_when_both_are_open(allocation):
    """§7.2's soft constraint is the cost of getting this backwards.

    Ninety van-minutes against one bike's forty-five: §12 Q15 gives no
    exchange rate, and at par the bike is cheaper.
    """
    decision = answer(allocation, added_leg_minutes={LEG: 90.0})
    assert decision.by_depot == {"D1": Arm.REALLOCATE}


def test_a_tie_goes_to_the_transfer(allocation):
    """§5.3.2 names the transfer first, and paper moved leaves §7.2 alone."""
    decision = answer(allocation, added_leg_minutes={LEG: 45.0})
    assert decision.by_depot == {"D1": Arm.TRANSFER}
    assert len(decision.transfers) == len(OVER)


def test_the_comparison_prices_every_bike_it_would_move(allocation):
    """Forty-five over needs two bikes, so the bike arm costs ninety.

    The only case here where the second arm is not one bike, and the case
    that holds the multiplication: sixty van-minutes beats two relocations
    and loses to one.
    """
    wide = tuple(f"PKG-{n:03d}" for n in range(1, 46))
    decision = answer(
        allocation, proposals=[Proposal("D1", "D2", wide, LEG)],
        candidates=requests(wide), added_leg_minutes={LEG: 60.0},
        projected={"D1": 145, "D2": 50})
    assert decision.by_depot == {"D1": Arm.TRANSFER}
    assert len(decision.transfers) == 45


def test_two_bikes_move_when_they_are_the_cheaper_arm(allocation):
    decision = answer(
        allocation, proposals=[Proposal("D1", "D2", tuple(OVER), LEG)],
        added_leg_minutes={}, projected={"D1": 145, "D2": 50})
    assert decision.by_depot == {"D1": Arm.REALLOCATE}
    moved = {f: len(ids) for f, ids in decision.allocation.by_facility().items()}
    assert moved == {"D1": 6, "D2": 2}


def test_one_van_minute_more_than_the_bike_flips_the_arm(allocation):
    """The comparison is a comparison, not a preference for whichever ran."""
    assert answer(allocation, added_leg_minutes={LEG: 46.0}
                  ).by_depot == {"D1": Arm.REALLOCATE}


def test_neither_arm_leaves_the_lowest_priority_unassigned(allocation):
    """§5.3.2's last clause. D2 has room for ten, which is no whole bike.

    Ten and not zero: a bike is twenty-five envelopes of capacity, so a depot
    with less room than that cannot lend one without going over itself.
    """
    decision = answer(allocation, added_leg_minutes={},
                      projected={"D1": 120, "D2": 90})
    assert decision.by_depot == {"D1": Arm.UNASSIGNED}
    assert decision.transfers == ()
    assert decision.allocation is allocation
    assert decision.unassigned == {"D1": tuple(OVER)}, "§8.1's lowest, rolled"


def test_a_depot_takes_one_arm_and_never_both(allocation):
    """T9: "either TransferRequests or a changed allocation, never both".

    Checked per depot rather than per envelope, which is the form that can
    fail: a depot whose bikes were topped up *and* whose envelopes rode would
    have solved the same overage twice and spent both budgets doing it.
    """
    for minutes in ({LEG: 0.0}, {LEG: 90.0}, {}):
        decision = answer(allocation, added_leg_minutes=minutes)
        sending = {t.from_facility_id for t in decision.transfers}
        before = {f: len(v) for f, v in allocation.by_facility().items()}
        after = {f: len(v)
                 for f, v in decision.allocation.by_facility().items()}
        restaffed = {f for f in after if after[f] != before.get(f)}
        assert sending.isdisjoint(restaffed)
        for depot, arm in decision.by_depot.items():
            assert (depot in sending) is (arm is Arm.TRANSFER)
            assert (depot in restaffed) is (arm is Arm.REALLOCATE)


def test_relocating_bikes_adds_to_yesterdays_move_count(allocation):
    """§7.2 is a running cost: tonight's relocation does not erase the day's."""
    started = place({"D1": 4, "D2": 4}, BIKES, previous={
        vehicle: "HUB" for vehicle in BIKES})
    assert started.moves == 8
    decision = answer(started, added_leg_minutes={})
    assert decision.allocation.moves == 9


def test_a_second_leg_out_of_one_depot_pays_on_top_of_the_first(allocation):
    """§5.3.2's van-hours are a budget, and two legs draw on the same one.

    Forty over is two legs of twenty, each forty van-minutes, against an hour
    left on the day: the first is paid for and the second is not. The pair the
    second leg would have carried are §5.3.2's last clause instead.
    """
    second = tuple(f"PKG-2{n:02d}" for n in range(1, 21))
    candidates = requests() | {pid: TransferRequest(
        transfer_id=f"TR-{pid}", package_id=pid, from_facility_id="D1",
        to_facility_id="HUB", reason=TransferReason.REBALANCING,
        created_at=TODAY, deadline=DEADLINE, priority=1.0) for pid in second}
    decision = answer(
        allocation,
        proposals=[Proposal("D1", "D2", tuple(OVER), LEG),
                   Proposal("D1", "HUB", second, ("D1", "HUB"))],
        candidates=candidates,
        arrivals={LEG: ARRIVES, ("D1", "HUB"): ARRIVES},
        added_leg_minutes={LEG: 40.0, ("D1", "HUB"): 40.0},
        van_hours=1.0, projected={"D1": 140, "D2": 50})
    assert decision.by_depot == {"D1": Arm.TRANSFER}
    assert [t.package_id for t in decision.transfers] == OVER
    assert decision.unassigned == {"D1": second}


def test_the_days_van_hours_are_spent_once_across_depots(allocation):
    """Two depots, one hour of van left: the second cannot re-spend it."""
    other = [f"PKG-1{n:02d}" for n in range(1, 21)]
    decision = decide(
        [Proposal("D1", "HUB", tuple(OVER), ("D1", "HUB")),
         Proposal("D2", "HUB", tuple(other), ("D2", "HUB"))],
        place({"D1": 4, "D2": 4, "HUB": 4}, BIKES + ["MOTO-009", "MOTO-010",
                                                     "MOTO-011", "MOTO-012"]),
        candidates={**{pid: TransferRequest(
            transfer_id=f"TR-{pid}", package_id=pid, from_facility_id="D1",
            to_facility_id="HUB", reason=TransferReason.REBALANCING,
            created_at=TODAY, deadline=DEADLINE, priority=1.0)
            for pid in OVER},
            **{pid: TransferRequest(
                transfer_id=f"TR-{pid}", package_id=pid,
                from_facility_id="D2", to_facility_id="HUB",
                reason=TransferReason.REBALANCING, created_at=TODAY,
                deadline=DEADLINE, priority=1.0) for pid in other}},
        arrivals={("D1", "HUB"): ARRIVES, ("D2", "HUB"): ARRIVES},
        added_leg_minutes={("D1", "HUB"): 40.0, ("D2", "HUB"): 40.0},
        van_hours=1.0,
        projected={"D1": 121, "D2": 120, "HUB": 20},
        capacity={"D1": capacity_for(4), "D2": capacity_for(4),
                  "HUB": capacity_for(4)})
    assert decision.by_depot["D1"] is Arm.TRANSFER, "worst depot goes first"
    assert decision.by_depot["D2"] is not Arm.TRANSFER, "20 minutes left"
    assert {t.from_facility_id for t in decision.transfers} == {"D1"}
