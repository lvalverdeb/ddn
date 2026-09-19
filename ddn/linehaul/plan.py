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

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ddn.linehaul.circuit import (
    NO_VAN_LEG,
    Declined,
    Leg,
    Transit,
    choose,
    legs_for,
    sequence_departure,
)

# §5's one-day lag: line-haul runs on day D and the depot releases its routes
# on the morning of D+1. `route_release_time` is a time of day, so the deadline
# a van is working to is tomorrow's, not today's. Treating it as today's puts
# every deadline in the past before a single van is back from pickups — which
# is what the first run of this module did, and it carried nothing at all.
DAY = 24 * 3600


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
    def held(self) -> int:
        return sum(len(ids) for ids in self.rolled.values())


def _released_at(van: dict[str, Any]) -> int:
    """When a van is free for line-haul; zero if it never went on pickups.

    §9.1 marks `linehaul_release_at` "datetime, optional", and a record may
    carry the column with no value rather than leave it out -- which is what a
    van held for line-haul from the outset looks like. Reading it with a
    default only covers the absent case, and a present `None` reached `int()`
    and raised.
    """
    return int(van.get("linehaul_release_at") or 0)


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

    deadlines = {f["id"]: latest_departure(f, unload_seconds=unload_seconds)
                 for f in facilities}
    hub_transit = {f["id"]: int(f["transit_from_hub_min"]) * 60
                   for f in facilities}
    # §5.3.2 bounds a transfer by "the receiving depot's morning release".
    releases = {f["id"]: DAY + int(f["route_release_time"]) for f in facilities}
    waiting_transfers = list(transfers)
    declined: list[Declined] = []
    hub_id = "HUB"

    free = sorted(vans, key=_released_at)
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
        aboard_all = [e for e in pool]
        aboard_g = sum(int(e.get("weight_g", 200)) for e in aboard_all)
        stops, carried, refused = choose(
            [facility_id], mine, transit=transit, hub_transit=hub_transit,
            release_of=releases, unload_seconds=unload_seconds,
            earliest_departure=_released_at(chosen), carried_g=aboard_g)
        declined.extend(refused)
        settled = {d.transfer_id for d in refused} | {t.transfer_id for t in carried}
        waiting_transfers = [t for t in waiting_transfers
                             if t.transfer_id not in settled]

        # §5.3 departs as late as the whole circuit allows, not just its first
        # stop -- `sequence_departure` explains why that distinction matters.
        departure = (deadline if len(stops) == 1 else sequence_departure(
            stops, hub_transit=hub_transit, transit=transit,
            release_of=releases, unload_seconds=unload_seconds))

        aboard = [e for e in pool if int(e["expected_ready_at"]) <= departure]
        missed = [e for e in pool if int(e["expected_ready_at"]) > departure]
        aboard_g = sum(int(e.get("weight_g", 200)) for e in aboard)

        timed = legs_for(stops, departure, hub_id=hub_id,
                         hub_transit=hub_transit, transit=transit)
        legs, weight = [], aboard_g + sum(t.weight_g for t in carried)
        for index, (origin, destination, left, arrived) in enumerate(timed):
            legs.append(Leg(
                from_facility=origin, to_facility=destination,
                departure=left, arrival=arrived,
                hub_loads={facility_id: tuple(e["package_id"] for e in aboard)}
                if index == 0 else {},
                transfer_ids=tuple(t.transfer_id for t in carried
                                   if t.to_facility_id == destination),
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
        if missed:
            rolled[facility_id] = tuple(e["package_id"] for e in missed)
            reasons[facility_id] = NOT_READY_IN_TIME

    # Anything still waiting had no circuit that reached its origin (§9.2).
    declined.extend(Declined(t.transfer_id, NO_VAN_LEG)
                    for t in waiting_transfers)
    return LinehaulPlan(trips=tuple(trips), rolled=rolled, reasons=reasons,
                        declined=tuple(declined))
