"""§5.3 — hub to secondary depots, the night before delivery.

Not a routing problem. §5.3 says so: "primarily an assignment problem; each van
serves one depot per trip." There is nothing to sequence — a van leaves the hub,
drives to one depot, unloads. What has to be decided is *which van goes where
and when*, against a deadline that is not a time window on a stop but a
property of the destination: the depot's morning route release.

The one-day lag (§5) is what makes this stage load-bearing. Everything
line-hauled on day D is delivered on D+1, so a van that misses a release does
not make a late delivery — it makes no delivery at all, and its envelopes wait
a whole day. §5.3: "A van that cannot make the release deadline should not
depart; its load waits for the next day's line-haul."
"""

from __future__ import annotations

from ddn import linehaul

HOUR = 3600


def depot(facility_id: str, release: int, transit_min: int) -> dict:
    """A §3.1 row. `latest_van_departure` is derived, never stated."""
    return {"id": facility_id, "route_release_time": release,
            "transit_from_hub_min": transit_min}


def envelope(package_id: str, facility_id: str, ready_at: int) -> dict:
    return {"package_id": package_id, "facility_id": facility_id,
            "expected_ready_at": ready_at, "status": "Ready"}


def van(vehicle_id: str, available_from: int = 0) -> dict:
    return {"vehicle_id": vehicle_id, "type": "van",
            "linehaul_release_at": available_from}


# --------------------------------------------------------------------------
# The deadline (§3.1, §5.3)
# --------------------------------------------------------------------------

def test_the_latest_departure_is_derived_not_declared():
    """§3.1's last column is "[release − transit − unload]", a formula rather
    than a figure, so it cannot go stale when transit is re-measured."""
    d = depot("D1", release=6 * HOUR, transit_min=90)

    assert linehaul.latest_departure(d, unload_seconds=20 * 60) == (
        linehaul.DAY + 6 * HOUR - 90 * 60 - 20 * 60)


def test_the_deadline_is_tomorrow_morning_not_this_one():
    """§5's one-day lag, which is easy to lose in a field called
    `route_release_time`.

    Line-haul runs on day D; the depot releases its routes on D+1. Read as
    today's time of day, every deadline is already past before the first van is
    back from pickups — the first run of this module carried nothing at all for
    exactly that reason.
    """
    d = depot("D1", release=6 * HOUR, transit_min=60)
    evening = 20 * HOUR

    assert linehaul.latest_departure(d, unload_seconds=20 * 60) > evening


def test_a_trip_arrives_before_the_depot_opens_its_routes():
    """The constraint the whole stage exists to satisfy."""
    plan = linehaul.plan(
        [depot("D1", release=6 * HOUR, transit_min=60)],
        [envelope("P1", "D1", ready_at=0)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert trip.arrival <= linehaul.DAY + 6 * HOUR - 20 * 60
    assert trip.destination == "D1"


# --------------------------------------------------------------------------
# Departing late on purpose (§5.3)
# --------------------------------------------------------------------------

def test_a_van_waits_for_more_envelopes_if_it_still_makes_the_release():
    """§5.3: "A van may wait for more envelopes to become ready if it can still
    reach the depot before its morning release."

    Leaving early is not safer — it is a day's delay for everything still in
    the clean room.
    """
    d = depot("D1", release=8 * HOUR, transit_min=60)
    late = 6 * HOUR
    plan = linehaul.plan(
        [d], [envelope("EARLY", "D1", ready_at=0),
              envelope("LATE", "D1", ready_at=late)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert set(trip.package_ids) == {"EARLY", "LATE"}
    assert trip.departure >= late


def test_an_envelope_not_ready_by_departure_rolls_to_the_next_day():
    """§5.3: "otherwise it departs with what is ready and later envelopes roll
    to the next day's line-haul." """
    d = depot("D1", release=4 * HOUR, transit_min=60)
    plan = linehaul.plan(
        [d], [envelope("READY", "D1", ready_at=0),
              # still in the clean room when the van has to leave, which is
              # after midnight: §5's lag puts the deadline on D+1.
              envelope("TOO_LATE", "D1", ready_at=linehaul.DAY + 3 * HOUR)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert trip.package_ids == ("READY",)
    assert plan.rolled["D1"] == ("TOO_LATE",)


# --------------------------------------------------------------------------
# Refusing to depart (§5.3)
# --------------------------------------------------------------------------

def test_a_van_that_cannot_make_the_release_does_not_depart():
    """§5.3 is explicit, and it is the opposite of what a routing solver would
    do with a soft window: the trip is not made late, it is not made."""
    # A depot 30 hours away cannot be reached before it releases, whatever
    # time the van leaves.
    d = depot("D1", release=2 * HOUR, transit_min=30 * 60)
    plan = linehaul.plan(
        [d], [envelope("P1", "D1", ready_at=0)],
        [van("V1")], unload_seconds=20 * 60)

    assert plan.trips == ()
    assert plan.rolled["D1"] == ("P1",)


def test_a_van_busy_on_pickups_past_the_deadline_is_not_used():
    """§9.1's `linehaul_release_at` — "time a pickup van must be back at the hub
    for line-haul duty". Vans are the contested resource (§4.1), so one that is
    still out is not a van."""
    d = depot("D1", release=4 * HOUR, transit_min=60)
    plan = linehaul.plan(
        [d], [envelope("P1", "D1", ready_at=0)],
        # back at the hub only after the depot has already released
        [van("V1", available_from=linehaul.DAY + 8 * HOUR)],
        unload_seconds=20 * 60)

    assert plan.trips == ()
    assert "D1" in plan.rolled


# --------------------------------------------------------------------------
# Scarcity (§4.1, §8.3)
# --------------------------------------------------------------------------

def test_one_van_serves_one_depot_per_trip():
    """§5.3. A van carrying two depots' envelopes would have to unload twice,
    which is a route, and this stage is not routing."""
    plan = linehaul.plan(
        [depot("D1", release=6 * HOUR, transit_min=60),
         depot("D2", release=6 * HOUR, transit_min=60)],
        [envelope("A", "D1", ready_at=0), envelope("B", "D2", ready_at=0)],
        [van("V1"), van("V2")], unload_seconds=20 * 60)

    assert len(plan.trips) == 2
    assert {t.destination for t in plan.trips} == {"D1", "D2"}
    assert len({t.van_id for t in plan.trips}) == 2


def test_the_tightest_deadline_is_served_first_when_vans_are_scarce():
    """Vans are "the most likely operational bottleneck" (§8.3), so which depot
    loses a van is a real decision.

    Earliest deadline first: a depot releasing at 04:00 cannot be served later,
    while one releasing at 10:00 still can be. Serving the loose one first can
    lose both; serving the tight one first never loses more than the loose one.
    """
    plan = linehaul.plan(
        [depot("LOOSE", release=10 * HOUR, transit_min=60),
         depot("TIGHT", release=4 * HOUR, transit_min=60)],
        [envelope("A", "LOOSE", ready_at=0), envelope("B", "TIGHT", ready_at=0)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert trip.destination == "TIGHT"
    assert plan.rolled["LOOSE"] == ("A",)


def test_a_depot_with_no_envelopes_gets_no_van():
    """Vans are scarce; an empty trip is one another depot did not get."""
    plan = linehaul.plan(
        [depot("D1", release=6 * HOUR, transit_min=60),
         depot("EMPTY", release=6 * HOUR, transit_min=60)],
        [envelope("A", "D1", ready_at=0)],
        [van("V1"), van("V2")], unload_seconds=20 * 60)

    assert [t.destination for t in plan.trips] == ["D1"]


def test_the_plan_reports_what_9_2_asks_for():
    """§9.2: "van_id, destination, package_ids, departure time, expected
    arrival"."""
    plan = linehaul.plan(
        [depot("D1", release=6 * HOUR, transit_min=60)],
        [envelope("P1", "D1", ready_at=0)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert trip.van_id == "V1"
    assert trip.destination == "D1"
    assert trip.package_ids == ("P1",)
    assert trip.arrival == trip.departure + 60 * 60


def test_every_envelope_is_either_carried_or_held():
    """The property that makes the plan auditable rather than plausible.

    An envelope belonging to neither list is a parcel nobody is looking for:
    it did not go, and nothing says it is waiting. §9.2's unassigned list is
    only trustworthy if the two halves are exhaustive.
    """
    envelopes = [
        envelope("A", "TIGHT", ready_at=0),           # carried
        envelope("B", "TIGHT", ready_at=20 * HOUR),   # not ready in time
        envelope("C", "LOOSE", ready_at=0),           # no van left
        envelope("D", "NOVAN", ready_at=0),           # deadline already gone
    ]
    plan = linehaul.plan(
        [depot("TIGHT", release=5 * HOUR, transit_min=60),
         depot("LOOSE", release=9 * HOUR, transit_min=60),
         depot("NOVAN", release=1 * HOUR, transit_min=180)],
        envelopes, [van("V1")], unload_seconds=20 * 60)

    accounted = {pid for trip in plan.trips for pid in trip.package_ids}
    accounted |= {pid for ids in plan.rolled.values() for pid in ids}

    assert accounted == {e["package_id"] for e in envelopes}
    assert plan.carried + plan.held == len(envelopes)


def test_a_trip_says_when_the_van_is_back():
    """§5.3: "the van (and driver) are unavailable until they return."

    §9.2's line-haul output does not ask for this, but §8.3 calls van-hours the
    likely bottleneck and §4.1 makes pickups van-only — so when a van is back
    at the hub is what decides whether it can collect tomorrow. A plan that
    reports only the outward leg cannot answer that.
    """
    plan = linehaul.plan(
        [depot("D6", release=6 * HOUR, transit_min=240)],
        [envelope("P1", "D6", ready_at=0)],
        [van("V1")], unload_seconds=20 * 60)
    trip, = plan.trips

    assert trip.returns == trip.arrival + 20 * 60 + 240 * 60
    assert trip.returns > trip.arrival
