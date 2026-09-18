"""§4.2's first half: dividing a fixed fleet between facilities.

`docs/solver-capabilities.md` settled the shape. A vehicle's depot is fixed
before the solve -- `Vehicle.start_location_id` is a required string, and the
one lock that mentions a depot pins an *order* to one -- so §4.2's "integrated"
option cannot be expressed and the two-stage approach the document already
recommends is the only one available. This is that first stage: it runs before
any routing and decides what each facility has to route with.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ddn import assumptions

#: §4.2's planning figure: "~25 envelopes per motorbike per day". §7.4 is where
#: it comes from -- 35 envelopes times ten minutes is 350 against an 8-hour
#: shift, so the box is never what binds.
EFFECTIVE_PER_BIKE = assumptions.ENVELOPES_PER_BIKE


def allocate(pools: dict[str, int], bikes: int) -> dict[str, int]:
    """§4.2's two-stage first half: divide a fixed fleet between facilities.

    Largest-remainder, so the fleet is neither over- nor under-committed: the
    obvious `round()` per facility can allocate more bikes than exist, which is
    a plan nobody can run.

    A facility with any demand at all gets at least one bike. Zero would leave
    its envelopes unserved with bikes idle elsewhere, and §8's first objective
    is delivered work rather than tidy arithmetic.
    """
    demand = sum(pools.values())
    if not demand:
        return {facility: 0 for facility in pools}

    exact = {f: n / demand * bikes for f, n in pools.items()}
    floors = {f: max(1, int(share)) if pools[f] else 0
              for f, share in exact.items()}
    spare = bikes - sum(floors.values())
    for facility in sorted(exact, key=lambda f: exact[f] - int(exact[f]),
                           reverse=True):
        if spare <= 0:
            break
        if pools[facility]:
            floors[facility] += 1
            spare -= 1
    return floors


@dataclass(frozen=True, slots=True)
class Allocation:
    """§9.2's fleet-allocation record: "vehicle_id, facility_id, role per day"."""

    vehicle_id: str
    facility_id: str
    role: str
    #: §9.1's `linehaul_release_at`: when a pickup van is due back for §5.3.
    release_at: int | None = None


@dataclass(frozen=True)
class FleetPlan:
    """A day's allocation, and what it cost to change yesterday's."""

    allocations: tuple[Allocation, ...] = ()
    #: §7.2's soft constraint made visible: vehicles that changed facility.
    moves: int = 0

    def by_facility(self) -> dict[str, tuple[str, ...]]:
        placed: dict[str, list[str]] = {}
        for allocation in self.allocations:
            placed.setdefault(allocation.facility_id, []).append(
                allocation.vehicle_id)
        return {facility: tuple(ids) for facility, ids in placed.items()}


def place(targets: Mapping[str, int], fleet: Sequence[str], *,
          previous: Mapping[str, str] | None = None,
          role: str = "delivery") -> FleetPlan:
    """Give each facility its count, moving as few vehicles as possible.

    §7.2 makes "fleet reallocation between facilities on consecutive days" a
    soft constraint and §4.2 says why: "relocation between distant facilities
    has a time cost", and the rider is unavailable while it happens. The counts
    are not negotiable -- `allocate` has already decided them from demand -- so
    what is left to minimise is *which* vehicles move, and a bike that is
    already where it is needed should stay there.

    Args:
        targets: facility id -> how many vehicles it needs.
        fleet: every vehicle id available today.
        previous: yesterday's vehicle id -> facility id. Absent on day one,
            when nothing has moved because nothing was anywhere.
        role: §9.1's role for these vehicles.

    Returns:
        A `FleetPlan` whose `moves` counts the vehicles that changed facility.
        Reported rather than minimised away silently: §7.2 is a cost to weigh,
        and a plan that moved forty riders should say so.

    Raises:
        ValueError: if the fleet cannot cover the targets, because a plan that
            allocates vehicles nobody has is not a plan.
    """
    wanted = sum(targets.values())
    if wanted > len(fleet):
        raise ValueError(
            f"§4.2 allocates {wanted} vehicles from a fleet of {len(fleet)}; "
            "the totals must come from the same fleet")

    previous = dict(previous or {})
    unplaced = sorted(fleet)
    settled: dict[str, list[str]] = {facility: [] for facility in targets}

    # Everyone who is already where they are needed stays. Sorted so that two
    # runs of the same day answer the same way.
    for facility, target in targets.items():
        staying = [v for v in unplaced if previous.get(v) == facility][:target]
        settled[facility] = list(staying)
        for vehicle in staying:
            unplaced.remove(vehicle)

    moves = 0
    for facility, target in targets.items():
        while len(settled[facility]) < target:
            vehicle = unplaced.pop(0)
            settled[facility].append(vehicle)
            if previous.get(vehicle, facility) != facility:
                moves += 1

    return FleetPlan(
        allocations=tuple(
            Allocation(vehicle_id=vehicle, facility_id=facility, role=role)
            for facility in sorted(settled)
            for vehicle in sorted(settled[facility])),
        moves=moves)


def vans(fleet: Sequence[str], *,
         earmarked: int = assumptions.PICKUP_VANS_EARMARKED,
         taper_to: int = assumptions.PICKUP_VANS_AFTER_TAPER,
         taper_from: int, taper_until: int,
         hub_id: str = "HUB") -> FleetPlan:
    """§4.3's split: how many vans collect, how many line-haul, and when.

    §4.3 is the contested decision of the day -- "all vans are hub-based, [so]
    the daily plan must decide how many run pickups and how many are held for
    line-haul, and at what time pickup vans are released". §5.1.3 adds the
    shape: the earmark "should taper during the afternoon so that vans are
    progressively released to line-haul as depot departure times approach".

    The released vans are spread evenly across the taper window rather than
    freed together, because §5.3's deadlines are staggered too: a depot four
    hours away needs its van long before one thirty minutes away.

    Args:
        fleet: every van id.
        earmarked: §5.1.3's pickup earmark at the start of the day.
        taper_to: how many are still collecting when the taper ends.
        taper_from: when the first van is released.
        taper_until: when the last one is.
        hub_id: §4.3 -- every van is hub-based.

    Returns:
        A `FleetPlan` of van allocations. A van released to line-haul keeps the
        pickup role and carries `release_at`, because that is what §9.1's
        vehicle table says it is: a pickup vehicle with a `linehaul_release_at`.

    Raises:
        ValueError: if the earmark does not fit the fleet, or the taper ends
            with more vans collecting than it began with.
    """
    if earmarked > len(fleet):
        raise ValueError(
            f"§5.1.3 earmarks {earmarked} vans for pickups from a fleet of "
            f"{len(fleet)}")
    if taper_to > earmarked:
        raise ValueError(
            f"a taper cannot end with more vans on pickups ({taper_to}) than "
            f"it started with ({earmarked})")

    ordered = sorted(fleet)
    collecting, hauling = ordered[:earmarked], ordered[earmarked:]
    released = earmarked - taper_to

    allocations = []
    for index, van in enumerate(collecting):
        release: int | None = None
        if index < released:
            step = 0 if released == 1 else index * (
                (taper_until - taper_from) // max(released - 1, 1))
            release = taper_from + step
        allocations.append(Allocation(van, hub_id, "pickup", release))
    allocations.extend(Allocation(van, hub_id, "linehaul") for van in hauling)
    return FleetPlan(allocations=tuple(allocations))
