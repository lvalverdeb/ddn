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


@dataclass(frozen=True)
class LinehaulPlan:
    """The night's line-haul, and what it could not carry."""

    trips: tuple[Trip, ...] = ()
    rolled: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def carried(self) -> int:
        return sum(len(trip.package_ids) for trip in self.trips)

    @property
    def held(self) -> int:
        return sum(len(ids) for ids in self.rolled.values())


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
         unload_seconds: int) -> LinehaulPlan:
    """Assign vans to depots for one night.

    Args:
        facilities: §3.1 rows for the depots, each carrying
            `route_release_time` and `transit_from_hub_min`.
        envelopes: depot-bound envelopes with `facility_id` and
            `expected_ready_at`.
        vans: §9.1 vehicle records; `linehaul_release_at` is when a van that
            spent the day on pickups is back at the hub.
        unload_seconds: how long unloading takes at the depot.

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
    transit = {f["id"]: int(f["transit_from_hub_min"]) * 60 for f in facilities}

    free = sorted(vans, key=lambda v: int(v.get("linehaul_release_at", 0)))
    trips: list[Trip] = []
    rolled: dict[str, tuple[str, ...]] = {}

    # Tightest deadline first; vans are scarce and a missed release costs a day.
    for facility_id in sorted(waiting, key=lambda name: deadlines.get(name, 0)):
        pool = waiting[facility_id]
        deadline = deadlines.get(facility_id)
        chosen = next(
            (v for v in free
             if deadline is not None
             and int(v.get("linehaul_release_at", 0)) <= deadline),
            None)
        if chosen is None:
            # No van can reach this depot before it releases its routes, so
            # nothing departs. §5.3 prefers a whole day's delay to a van that
            # arrives after the bikes have gone.
            rolled[facility_id] = tuple(e["package_id"] for e in pool)
            continue

        free.remove(chosen)
        departure = deadline
        aboard = [e for e in pool if int(e["expected_ready_at"]) <= departure]
        missed = [e for e in pool if int(e["expected_ready_at"]) > departure]
        trips.append(Trip(
            van_id=chosen["vehicle_id"],
            destination=facility_id,
            package_ids=tuple(e["package_id"] for e in aboard),
            departure=departure,
            arrival=departure + transit[facility_id],
            returns=departure + 2 * transit[facility_id] + unload_seconds,
        ))
        if missed:
            rolled[facility_id] = tuple(e["package_id"] for e in missed)

    return LinehaulPlan(trips=tuple(trips), rolled=rolled)
