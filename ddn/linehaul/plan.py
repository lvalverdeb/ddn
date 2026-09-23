"""§5.3 — hub to secondary depots, the night before delivery.

**Not a routing problem, and not solved like one.** §5.3: "primarily an
assignment problem; each van serves one depot per trip." There is nothing to
sequence. What has to be decided is which van goes where and when, against a
deadline that is a property of the *destination* rather than of a stop — the
depot's morning route release.

The one-day lag (§5) is what gives the deadline teeth. Everything line-hauled on
day D is delivered on D+1, so a van that misses a release does not deliver late,
it does not deliver at all: its envelopes wait a whole day. §5.3 makes the
consequence explicit — "a van that cannot make the release deadline should not
depart; its load waits for the next day's line-haul."

**Departing late is the correct default.** A van that leaves early strands
everything still in the clean room, so each trip leaves at the latest moment
that still makes the release. That is §5.3's "may wait for more envelopes to
become ready if it can still reach the depot before its morning release", and it
is the opposite of the instinct a routing solver trains.

**Earliest deadline first**, because vans are the contested resource — §4.1
makes pickups van-only and §8.3 calls van-hours the likely bottleneck after
clean-room assembly. A depot releasing at 04:00 cannot be served later; one
releasing at 10:00 still can. Serving the loose depot first can lose both;
serving the tight one first loses at most the loose one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ddn.linehaul.circuit import (
    DAY,
    NO_VAN_LEG,
    OVER_CAPACITY,
    Declined,
    Leg,
    Transit,
    compete,
    legs_for,
    sequence_departure,
)

# §5's one-day lag: line-haul runs on day D and the depot releases its routes
# on the morning of D+1. `route_release_time` is a time of day, so the deadline
# a van is working to is tomorrow's, not today's. Treating it as today's puts
# every deadline in the past before a single van is back from pickups — which
# is what the first run of this module did, and it carried nothing at all.


@dataclass(frozen=True)
class Trip:
    """§9.2's line-haul plan, for one van."""

    van_id: str
    destination: str
    package_ids: tuple[str, ...]
    departure: int
    arrival: int
    # §5.3: "the van (and driver) are unavailable until they return." §9.2's
    # output does not ask for this, and it is the number that decides whether
    # the van can collect tomorrow -- §4.1 makes pickups van-only and §8.3
    # calls van-hours the likely bottleneck, so the outward leg alone cannot
    # answer the question the stage is contested over.
    returns: int
    #: §5.3.2: the trip as an ordered sequence of legs. A hub-to-depot run with
    #: no transfers is one leg, which is §5.3.1 unchanged.
    legs: tuple[Leg, ...] = ()
    #: §9.1 transfer ids carried on this circuit.
    transfer_ids: tuple[str, ...] = ()


#: Why a depot's envelopes stayed at the hub. §5.3 gives two causes and they
#: are not the same problem: one is a fleet that is too small, the other a hub
#: that was too slow. Reporting both as "rolled" hides which.
NO_VAN = "no van could reach the depot before its morning release"
NOT_READY_IN_TIME = "not ready before the van's latest departure"


@dataclass(frozen=True)
class LinehaulPlan:
    """The night's line-haul, and what it could not carry."""

    trips: tuple[Trip, ...] = ()
    #: §9.2: "transfers not carried, with reason".
    declined: tuple[Declined, ...] = ()
    rolled: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: facility id -> why its rolled envelopes rolled.
    reasons: dict[str, str] = field(default_factory=dict)

    def reason_for(self, facility_id: str) -> str | None:
        """Why this depot's envelopes stayed, or `None` if none did."""
        return self.reasons.get(facility_id)

    @property
    def carried(self) -> int:
        return sum(len(trip.package_ids) for trip in self.trips)

    @property
    def transfers_carried(self) -> tuple[str, ...]:
        """§5.3.2's transfer ids that actually rode a leg tonight.

        Read off the legs for the same reason as `returned`: accumulated
        beside them it could disagree with what the plan says it carried.
        """
        return tuple(dict.fromkeys(
            transfer_id
            for trip in self.trips
            for leg in trip.legs
            for transfer_id in leg.transfer_ids))

    @property
    def held(self) -> int:
        return sum(len(ids) for ids in self.rolled.values())

    @property
    def returned(self) -> tuple[str, ...]:
        """§5.5's depot rejects that actually rode home tonight.

        Read off the legs rather than accumulated beside them, so it cannot
        disagree with what the plan says it carried. A depot no van reached
        keeps its returns for the next night -- they are not lost, they are
        simply not on a leg.
        """
        return tuple(dict.fromkeys(
            package_id
            for trip in self.trips
            for leg in trip.legs
            for package_id in leg.return_ids))


def _released_at(van: dict[str, Any]) -> int:
    """When a van is free for line-haul; zero if it never went on pickups.

    §9.1 marks `linehaul_release_at` "datetime, optional", and a record may
    carry the column with no value rather than leave it out -- which is what a
    van held for line-haul from the outset looks like. Reading it with a
    default only covers the absent case, and a present `None` reached `int()`
    and raised.
    """
    return int(van.get("linehaul_release_at") or 0)


def available(vans: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The vans §4.3 makes eligible for a line-haul leg tonight.

    §4.3: "A van on pickups is available for line-haul only after it has
    returned to the hub and unloaded." A van earmarked for pickups and never
    released has not returned -- §5.1.3's taper leaves some collecting until
    the cut-off -- so it is not eligible at all, and `_released_at`'s zero for
    a record with no release time made it look like the *earliest* available
    van rather than an unavailable one.

    The test is by exception: anything not marked `pickup` is available, so a
    §9.1 row that omits `role` stays eligible rather than silently vanishing
    from the night's fleet.
    """
    return [van for van in vans
            if van.get("role") != "pickup"
            or van.get("linehaul_release_at") is not None]


def latest_departure(facility: dict[str, Any], *, unload_seconds: int) -> int:
    """§3.1's derived column: release − transit − unload.

    Derived here rather than read from the facility row because §3.1 states it
    as a formula, not a figure. A transit time re-measured against real traffic
    should move this without anyone remembering to update a second column.

    Measured from the start of the line-haul day, so the release is a day out:
    §5 puts line-haul on D and delivery on D+1, and a deadline read as today's
    is one every van has already missed.
    """
    return (DAY + int(facility["route_release_time"])
            - int(facility["transit_from_hub_min"]) * 60
            - unload_seconds)


def plan(facilities: Sequence[dict[str, Any]],
         envelopes: Sequence[dict[str, Any]],
         vans: Sequence[dict[str, Any]], *,
         unload_seconds: int,
         transfers: Sequence[Any] = (),
         returning: Sequence[dict[str, Any]] = (),
         transit: Transit | None = None) -> LinehaulPlan:
    """Assign vans to depots for one night.

    Args:
        facilities: §3.1 rows for the depots, each carrying
            `route_release_time` and `transit_from_hub_min`.
        envelopes: depot-bound envelopes with `facility_id` and
            `expected_ready_at`.
        vans: §9.1 vehicle records; `linehaul_release_at` is when a van that
            spent the day on pickups is back at the hub.
        unload_seconds: how long unloading takes at the depot.
        transfers: §9.1 transfer requests to ride on tonight's circuits
            (§5.3.2). Each is carried when its origin is already on a van's
            circuit and its destination can be reached in time.
        returning: envelopes sitting at a depot that §5.5 sends back to the
            customer -- rejected, defective or SLA-expired. They ride the
            leg that departs their facility and are dropped at the hub, and
            their weight counts against §7.1's 500 kg like anything else on
            board. `returns.goes_back` is the predicate; this planner does
            not decide which envelopes qualify.
        transit: seconds between two facilities by id, for the inter-depot
            legs a transfer needs. §3.1 supplies hub transit only, so without
            this every transfer is declined with that as the reason rather
            than run on a guessed figure.

    Returns:
        A `LinehaulPlan`. Every envelope appears exactly once, either on a trip
        or in `rolled` — an envelope that quietly belongs to neither is a
        parcel nobody is looking for.
    """
    waiting: dict[str, list[dict[str, Any]]] = {}
    for envelope in envelopes:
        waiting.setdefault(envelope["facility_id"], []).append(envelope)

    # §5.5, by the facility the van will collect them from.
    homebound: dict[str, list[dict[str, Any]]] = {}
    for envelope in returning:
        homebound.setdefault(envelope["facility_id"], []).append(envelope)

    deadlines = {f["id"]: latest_departure(f, unload_seconds=unload_seconds)
                 for f in facilities}
    hub_transit = {f["id"]: int(f["transit_from_hub_min"]) * 60
                   for f in facilities}
    # §5.3.2 bounds a transfer by "the receiving depot's morning release".
    releases = {f["id"]: DAY + int(f["route_release_time"]) for f in facilities}
    waiting_transfers = list(transfers)
    declined: list[Declined] = []
    hub_id = "HUB"

    free = sorted(available(vans), key=_released_at)
    trips: list[Trip] = []
    rolled: dict[str, tuple[str, ...]] = {}
    reasons: dict[str, str] = {}

    # Tightest deadline first; vans are scarce and a missed release costs a day.
    for facility_id in sorted(waiting, key=lambda name: deadlines.get(name, 0)):
        pool = waiting[facility_id]
        deadline = deadlines.get(facility_id)
        chosen = next(
            (v for v in free
             if deadline is not None and _released_at(v) <= deadline),
            None)
        if chosen is None:
            # No van can reach this depot before it releases its routes, so
            # nothing departs. §5.3 prefers a whole day's delay to a van that
            # arrives after the bikes have gone.
            rolled[facility_id] = tuple(e["package_id"] for e in pool)
            reasons[facility_id] = NO_VAN
            continue

        free.remove(chosen)

        # §5.3.2: transfers waiting at the depot this van is already visiting.
        mine = [t for t in waiting_transfers
                if t.from_facility_id == facility_id]
        # §7.1 counts returns against the same 500 kg, and §5.3.2 does not rank
        # them against anything -- they ride back whatever else competes. Only
        # this depot's are known yet; `compete` may add stops, and their returns
        # join the load further down, which is why the leg check below binds.
        homebound_g = sum(int(e.get("weight_g", 200))
                          for e in homebound.get(facility_id, ()))

        # Readiness filters before the competition: an envelope that is not
        # ready cannot board however it scores (§7.1), so it must not take a
        # seat from one that can. `deadline` is the bound rather than the
        # departure, because the departure is not known until the circuit is --
        # and a circuit that gains a stop only ever leaves earlier, so every
        # envelope that makes the departure is in this set. The ones this
        # over-admits are split out again below, once the departure is known.
        ready = [e for e in pool if int(e["expected_ready_at"]) <= deadline]
        too_late = [e for e in pool if int(e["expected_ready_at"]) > deadline]

        loaded = compete(
            [facility_id], ready, mine, transit=transit,
            hub_transit=hub_transit, release_of=releases,
            unload_seconds=unload_seconds,
            earliest_departure=_released_at(chosen), reserved_g=homebound_g)
        stops, carried = loaded.stops, loaded.carried
        declined.extend(loaded.declined)
        settled = ({d.transfer_id for d in loaded.declined}
                   | {t.transfer_id for t in carried})
        waiting_transfers = [t for t in waiting_transfers
                             if t.transfer_id not in settled]

        # §5.3 departs as late as the whole circuit allows, not just its first
        # stop -- `sequence_departure` explains why that distinction matters.
        departure = (deadline if len(stops) == 1 else sequence_departure(
            stops, hub_transit=hub_transit, transit=transit,
            release_of=releases, unload_seconds=unload_seconds))

        aboard = [e for e in loaded.boarded
                  if int(e["expected_ready_at"]) <= departure]
        late = [e for e in loaded.boarded
                if int(e["expected_ready_at"]) > departure]
        missed = too_late + late
        aboard_g = sum(int(e.get("weight_g", 200)) for e in aboard)

        timed = legs_for(stops, departure, hub_id=hub_id,
                         hub_transit=hub_transit, transit=transit)
        # The load falls as the circuit drops things and rises as it picks them
        # up: hub-origin envelopes and transfers leave at their destinations,
        # §5.5's returns join at the depot they are waiting at and stay aboard
        # until the hub. §7.1 bounds the total on every leg, so the weight has
        # to be tracked per leg rather than assumed to be the departure load.
        homeward: list[dict[str, Any]] = []
        legs, weight = [], aboard_g + sum(t.weight_g for t in carried)
        for index, (origin, destination, left, arrived) in enumerate(timed):
            picked_up = homebound.pop(origin, []) if origin != hub_id else []
            homeward.extend(picked_up)
            weight += sum(int(e.get("weight_g", 200))
                          for e in picked_up)
            legs.append(Leg(
                from_facility=origin, to_facility=destination,
                departure=left, arrival=arrived,
                hub_loads={facility_id: tuple(e["package_id"] for e in aboard)}
                if index == 0 else {},
                transfer_ids=tuple(t.transfer_id for t in carried
                                   if t.to_facility_id == destination),
                return_ids=tuple(e["package_id"] for e in homeward),
                weight_g=weight))
            weight -= sum(t.weight_g for t in carried
                          if t.to_facility_id == destination)
            if index == 0:
                weight -= aboard_g

        last = legs[-1]
        trips.append(Trip(
            van_id=chosen["vehicle_id"],
            destination=facility_id,
            package_ids=tuple(e["package_id"] for e in aboard),
            departure=departure,
            arrival=legs[0].arrival,
            returns=last.arrival + hub_transit.get(last.to_facility, 0)
            + unload_seconds,
            legs=tuple(legs),
            transfer_ids=tuple(t.transfer_id for t in carried),
        ))
        # §9.2 asks for a reason per unassigned envelope; `rolled`/`reasons` is
        # per *facility*, which predates this step. A depot that rolls for both
        # causes at once can therefore report only one.
        #
        # Capacity wins that tie. A bumped envelope was *ready* -- it lost its
        # seat to a higher score -- so `NOT_READY_IN_TIME` is not merely vaguer
        # for it, it is false. `OVER_CAPACITY` is true of at least one envelope
        # in every tuple it labels, which is the most a per-facility field can
        # promise. `docs/spec-proposals/v0.17-per-envelope-reason.md` records
        # the narrowing rather than leaving it implied.
        if missed or loaded.bumped:
            rolled[facility_id] = tuple(e["package_id"]
                                        for e in missed + loaded.bumped)
            reasons[facility_id] = (OVER_CAPACITY if loaded.bumped
                                    else NOT_READY_IN_TIME)

    # Anything still waiting had no circuit that reached its origin (§9.2).
    declined.extend(Declined(t.transfer_id, NO_VAN_LEG)
                    for t in waiting_transfers)
    return LinehaulPlan(trips=tuple(trips), rolled=rolled, reasons=reasons,
                        declined=tuple(declined))


def depot_bound(positioned: Mapping[str, Sequence[dict[str, Any]]], *,
                hub_id: str) -> list[dict[str, Any]]:
    """The ready envelopes this package has to move, in pool order.

    §5.3's whole boundary: an envelope is line-haul's problem if it is ready
    today and its facility is not the hub. Hub-direct envelopes are not "not
    transported" -- they are already where they will be dispatched from, so
    they never enter a circuit.

    It reads the *positioned pool* rather than the raw inflow so that the
    simulator and `ddn/e2e/hub_to_depots` assemble the identical list by
    construction. They did not, before: taking inflow order gave the same 2,772
    envelopes in a different sequence, and `plan` is order-sensitive -- the
    circuits came back with one leg's load in a different order. Immaterial on
    §10's day, where nothing rolls, and not immaterial in general, because when
    van-hours are short the order decides *which* envelopes roll.
    """
    return [e for facility, pool in positioned.items() if facility != hub_id
            for e in pool]


def depots(facilities: Sequence[dict[str, Any]], *,
           hub_id: str) -> list[dict[str, Any]]:
    """Every facility a circuit can call at: §3.1's list without the hub.

    The hub is a circuit's origin, not a stop on it, and the order of what is
    left is the order `plan` considers them in.
    """
    return [f for f in facilities if f["id"] != hub_id]


def strip_rolled(positioned: Mapping[str, Sequence[dict[str, Any]]],
                 night: LinehaulPlan, *,
                 hub_id: str) -> dict[str, list[dict[str, Any]]]:
    """Take back what no circuit carried, so a depot is not promised it.

    A rolled envelope is still at the hub in the morning. Leaving it in the
    depot's pool would offer §5.4 an envelope that is not there -- the one
    error §7.1's depot bullet exists to prevent. The hub's own pool is left
    alone: nothing rolls when it never had to travel.

    Returns a new mapping rather than editing one in place: the pool it is
    given may be a hand-off read from a file, whose facility pools are tuples,
    and a function that works on one caller's container and not another's is
    the kind of difference the chain exists to find.
    """
    rolled = {pid for ids in night.rolled.values() for pid in ids}
    return {facility: [e for e in pool
                       if facility == hub_id or e["package_id"] not in rolled]
            for facility, pool in positioned.items()}


@dataclass(frozen=True, slots=True)
class Proposal:
    """e2e-2 §5: "Rebalancing proposals: from, to, package_ids, expected leg"."""

    from_facility_id: str
    to_facility_id: str
    package_ids: tuple[str, ...]
    #: The leg that would carry them, as the pair a circuit would fly. Named
    #: because e2e-2 §3 makes a leg the precondition: with none there is no
    #: proposal, and the over-full depot rolls by priority instead.
    leg: tuple[str, str]


def rebalancing(projected: Mapping[str, int], capacity: Mapping[str, int], *,
                pools: Mapping[str, Sequence[Mapping[str, Any]]],
                reaches: Callable[[str, str], bool],
                cap: int = 50) -> list[Proposal]:
    """§5.3.2's third trigger, as proposals §4.2 may refuse.

    e2e-2 §3: "when a depot's projected pool exceeds its motorbikes × 25 and a
    neighbour has slack, propose transfers if a leg exists or fits within
    van-hours. **Proposals go to allocation; this slice does not decide them
    alone.**" So nothing here moves an envelope: §4.2 owns the fleet, and a
    planner that rebalanced on its own authority would be making an allocation
    decision from inside §5.3.

    It returns `Proposal`, not `TransferRequest`, which is e2e-2 §5's own
    shape for this row. Two reasons: a request is a commitment and this is an
    offer, and `TransferRequest` lives in `ddn.model` -- this package imports
    nothing from the rest of `ddn` and a proposal is not worth being the first.
    The caller raises the requests if allocation accepts.

    The envelopes offered are the **lowest** priority at the over-full depot:
    §8.1 ranks what is delivered, and what is moved is the mirror of it, since
    an envelope that will be served tomorrow should not spend tonight on a van.

    Args:
        projected: facility id -> envelopes expected in its pool tomorrow.
        capacity: facility id -> what its bikes can serve.
        pools: the envelopes behind `projected`, to choose which to offer.
        reaches: whether a leg from the first facility to the second runs
            tonight. A precondition, not an aspiration.
        cap: most envelopes to offer per pair; e2e-2 row B9 states 50.

    Returns:
        One proposal per (over-full depot, neighbour) pair that a leg reaches.
        Empty when nothing is over capacity, when no neighbour has room, or
        when no leg connects them -- three different silences, none an error.
    """
    over = {f: n - capacity.get(f, 0) for f, n in projected.items()
            if n > capacity.get(f, 0)}
    room = {f: capacity.get(f, 0) - n for f, n in projected.items()
            if capacity.get(f, 0) > n}
    proposals: list[Proposal] = []

    for source in sorted(over, key=lambda f: (-over[f], f)):
        offer = sorted(pools.get(source, ()),
                       key=lambda e: (float(e.get("priority", 0)),
                                      e["package_id"]))[:cap]
        for destination in sorted(room, key=lambda f: (-room[f], f)):
            if not offer or not over[source] or not room[destination]:
                continue
            if not reaches(source, destination):
                continue
            moving = offer[:min(room[destination], over[source])]
            proposals.append(Proposal(
                from_facility_id=source, to_facility_id=destination,
                package_ids=tuple(e["package_id"] for e in moving),
                leg=(source, destination)))
            room[destination] -= len(moving)
            over[source] -= len(moving)
            offer = offer[len(moving):]
    return proposals
