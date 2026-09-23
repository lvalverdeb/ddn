"""§5.3.2's rebalancing choice: move the envelopes, or move the motorbikes.

§5.3.2 gives the rule in one sentence: "the choice between moving envelopes
and moving motorbikes is a cost comparison made by the allocation step:
transfer if a van leg between the two depots already exists or can be added
within van-hours, and the moved envelopes arrive before the receiving depot's
morning release; otherwise reallocate motorbikes or leave the envelopes
unassigned by priority."

Read closely that is a **gate and then a comparison**, and three outcomes
rather than two. The `if` clause is feasibility -- a leg, van-hours, an
arrival before the release -- and "cost comparison" is what decides between
the arms that survive it. The trailing "or leave the envelopes unassigned by
priority" is the third arm, and a `Decision` that could not express it would
be dropping a clause of the rule.

**The comparison weighs two different resources, and the document does not
give an exchange rate.** A transfer costs van-minutes; a reallocation costs
bike-minutes (`assumptions.BIKE_RELOCATION_MIN` per bike, itself invented --
§4.2 says only that "relocation between distant facilities has a time cost").
§12 Q15 asks for exactly "the cost basis for comparing an envelope transfer
with a motorbike reallocation" and is unanswered. **The interpretation taken
here is that the two are compared at par, one minute against one minute**,
because that is the only reading that needs no number the document withholds.
When Q15 is answered this is the line that changes, and §7.2's soft constraint
-- "rebalancing by transfer when reallocating motorbikes would have been
cheaper, or vice versa" -- is what will measure whether par was wrong.

Why this module and not `linehaul/`: `linehaul.rebalancing` returns
`Proposal` rather than `TransferRequest` because "§4.2 owns the fleet, and a
planner that rebalanced on its own authority would be making an allocation
decision from inside §5.3". This is §4.2, so the dependency points this way.

Why `candidates` rather than envelope rows: §9.1 defines a transfer's deadline
as min(receiving depot's next morning release, SLA date), and
`TransferRequest`'s own docstring makes that "the caller's to compute, because
only the caller knows the receiving depot's release". Building the requests
here would put a second copy of that rule inside `allocation/`, which
`simulation/day.py` already carries a scar comment about. So the caller hands
in the requests it *would* raise and this module decides which of them ride.
The signature is wider than the plan's four parameters for that reason.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ddn import assumptions
from ddn.allocation.fleet import EFFECTIVE_PER_BIKE, FleetPlan, place
from ddn.linehaul.plan import Proposal
from ddn.model.records import TransferRequest


class Arm(StrEnum):
    """§5.3.2's three outcomes for an over-full depot.

    `UNASSIGNED` is the sentence's last clause and not an error: a depot no
    leg can drain and no spare bike can serve rolls its lowest-priority
    envelopes, which is §8.1 working rather than failing.
    """

    TRANSFER = "transfer"
    REALLOCATE = "reallocate"
    UNASSIGNED = "unassigned"


@dataclass(frozen=True, slots=True)
class Decision:
    """What §4.2 answered, and which arm answered it for each depot.

    `by_depot` is what makes T9's "emit either TransferRequests or a changed
    allocation, never both for the same envelopes" checkable rather than
    asserted in prose: one arm per depot, and the transfers name only depots
    whose arm is `TRANSFER`.
    """

    #: Unchanged and *identical* -- not merely equal -- when no arm moved a
    #: bike, so a caller can tell "decided nothing" from "decided to keep".
    allocation: FleetPlan
    transfers: tuple[TransferRequest, ...] = ()
    by_depot: Mapping[str, Arm] = field(default_factory=dict)
    #: §5.3.2's third clause, per depot: offered and placed by neither arm.
    unassigned: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def decide(proposals: Sequence[Proposal], allocation: FleetPlan, *,
           candidates: Mapping[str, TransferRequest],
           arrivals: Mapping[tuple[str, str], datetime],
           added_leg_minutes: Mapping[tuple[str, str], float],
           van_hours: float,
           projected: Mapping[str, int],
           capacity: Mapping[str, int]) -> Decision:
    """Answer §5.3.2 for every over-full depot that has a proposal.

    A depot with no proposal at all is not visible here, which is
    `linehaul.rebalancing`'s own reading: "with none there is no proposal, and
    the over-full depot rolls by priority instead". That rolling is §5.4's,
    not §4.2's, so this returns nothing about it.

    Args:
        proposals: `linehaul.rebalancing`'s offers, lowest §8.1 priority first
            within each, which is the order they are taken in.
        allocation: today's motorbike placement, the second arm's subject.
        candidates: package id -> the request the caller would raise for it,
            carrying §9.1's deadline. A package with no candidate cannot ride.
        arrivals: leg -> when its van would put the envelopes down. Checked
            against each request's deadline by `TransferRequest.makes`, which
            is §5.3.2's "arrive before the receiving depot's morning release".
        added_leg_minutes: leg -> van-minutes to run it, `0` where the leg
            already runs tonight. **Absent means the leg cannot be added**,
            which is a different silence from "free".
        van_hours: hours left unspent on the day's vans; §5.3.2's "within
            van-hours" is this, and it is spent across all depots, not each.
        projected: facility id -> envelopes expected in its pool tomorrow.
        capacity: facility id -> what its bikes can serve. Passed rather than
            derived from `allocation` so that this and `rebalancing` weigh the
            same overage; deriving it twice is how the two come to disagree.

    Returns:
        A `Decision`. Its `allocation` is the *same object* when no bike
        moved, so "unchanged" is testable by identity.
    """
    placed = allocation.by_facility()
    bikes = {facility: len(ids) for facility, ids in placed.items()}
    fleet = [a.vehicle_id for a in allocation.allocations]
    previous = {a.vehicle_id: a.facility_id for a in allocation.allocations}
    roles = {a.role for a in allocation.allocations}
    role = roles.pop() if len(roles) == 1 else "delivery"

    offered: dict[str, list[Proposal]] = {}
    for proposal in proposals:
        offered.setdefault(proposal.from_facility_id, []).append(proposal)

    # What each facility could give up without going over itself: one bike is
    # `EFFECTIVE_PER_BIKE` envelopes of capacity, so a depot with room for
    # fewer than that has no whole bike to spare.
    givable = {
        facility: min(count, max(0, (capacity.get(facility, 0)
                                     - projected.get(facility, 0))
                                 // EFFECTIVE_PER_BIKE))
        for facility, count in bikes.items()}

    targets = dict(bikes)
    spare_minutes = van_hours * 60
    transfers: list[TransferRequest] = []
    by_depot: dict[str, Arm] = {}
    unassigned: dict[str, tuple[str, ...]] = {}

    # Worst over-full depot first, so the scarce resources -- van-hours and
    # spare bikes -- go where §8.1 leaves the most envelopes unserved.
    for source in sorted(offered, key=lambda f: (
            -(projected.get(f, 0) - capacity.get(f, 0)), f)):
        over = projected.get(source, 0) - capacity.get(source, 0)
        if over <= 0:
            continue
        mine = offered[source]

        riding, minutes = _transfer_arm(
            mine, candidates=candidates, arrivals=arrivals,
            added_leg_minutes=added_leg_minutes, budget=spare_minutes)
        taking = _reallocation_arm(over, source, givable)

        # §5.3.2's comparison, at par for want of §12 Q15's exchange rate.
        # Ties go to the transfer: the sentence names it first, and moving
        # paper leaves the fleet where yesterday put it (§7.2).
        arm = Arm.UNASSIGNED
        if riding and (not taking
                       or minutes <= assumptions.BIKE_RELOCATION_MIN
                       * sum(taking.values())):
            arm = Arm.TRANSFER
        elif taking:
            arm = Arm.REALLOCATE

        by_depot[source] = arm
        if arm is Arm.TRANSFER:
            transfers.extend(riding)
            spare_minutes -= minutes
            carried = {request.package_id for request in riding}
            left = tuple(pid for proposal in mine
                         for pid in proposal.package_ids if pid not in carried)
            if left:
                unassigned[source] = left[:over - len(carried)]
        elif arm is Arm.REALLOCATE:
            for donor, count in taking.items():
                targets[donor] -= count
                targets[source] = targets.get(source, 0) + count
                givable[donor] -= count
        else:
            offers = tuple(pid for proposal in mine
                           for pid in proposal.package_ids)
            unassigned[source] = offers[:over]

    if targets == bikes:
        return Decision(allocation=allocation, transfers=tuple(transfers),
                        by_depot=by_depot, unassigned=unassigned)

    # `place` counts moves against what it is given, so re-placing against
    # today would report only tonight's relocations and forget yesterday's.
    # §7.2 is a running cost, so the two are added.
    moved = place(targets, fleet, previous=previous, role=role)
    return Decision(
        allocation=FleetPlan(allocations=moved.allocations,
                             moves=allocation.moves + moved.moves),
        transfers=tuple(transfers), by_depot=by_depot, unassigned=unassigned)


def _transfer_arm(proposals: Sequence[Proposal], *,
                  candidates: Mapping[str, TransferRequest],
                  arrivals: Mapping[tuple[str, str], datetime],
                  added_leg_minutes: Mapping[tuple[str, str], float],
                  budget: float) -> tuple[list[TransferRequest], float]:
    """§5.3.2's `if`: which offers clear the gate, and what the vans pay.

    Every clause of the gate is a separate refusal and none of them is an
    error: no leg that can be added, no room left in the day's van-hours, or
    an arrival the envelope's own deadline will not accept.
    """
    riding: list[TransferRequest] = []
    minutes = 0.0
    for proposal in proposals:
        cost = added_leg_minutes.get(proposal.leg)
        arrival = arrivals.get(proposal.leg)
        if cost is None or arrival is None or minutes + cost > budget:
            continue
        makes = [candidates[pid] for pid in proposal.package_ids
                 if pid in candidates and candidates[pid].makes(arrival)]
        if not makes:
            continue
        minutes += cost
        riding.extend(makes)
    return riding, minutes


def _reallocation_arm(over: int, source: str,
                      givable: Mapping[str, int]) -> dict[str, int]:
    """§5.3.2's `otherwise`: the bikes that would cover `over` instead.

    Empty when the depots with slack cannot between them spare enough, which
    is the case that leaves §5.3.2's last clause as the only answer.
    """
    needed = math.ceil(over / EFFECTIVE_PER_BIKE)
    taking: dict[str, int] = {}
    for donor in sorted(givable, key=lambda f: (-givable[f], f)):
        if needed <= 0:
            break
        if donor == source or givable[donor] <= 0:
            continue
        take = min(givable[donor], needed)
        taking[donor] = take
        needed -= take
    return taking if needed <= 0 else {}
