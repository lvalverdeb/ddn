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


def test_a_van_with_an_explicit_null_release_time_is_still_available():
    """§9.1 marks `linehaul_release_at` "datetime, optional".

    A van held for line-haul from the outset has the column and no value, which
    is not the same as the column being absent — and reading it with a default
    only covered the absent case, so a record carrying `None` reached `int()`
    and raised. Found by feeding the planner §9.1-shaped records from the
    simulator rather than test-shaped ones.
    """
    depot = {"id": "D1", "route_release_time": 7 * 3600,
             "transit_from_hub_min": 30}
    envelopes = [{"package_id": "P1", "facility_id": "D1",
                  "expected_ready_at": 0}]
    night = linehaul.plan([depot], envelopes,
                          [{"vehicle_id": "V1", "linehaul_release_at": None}],
                          unload_seconds=1800)
    assert night.carried == 1
    assert night.trips[0].van_id == "V1"


# ----------------------------------------------------- §5.5 depot returns


def reject(package_id: str, facility: str, *, grams: int = 200) -> dict:
    """An envelope §5.5 sends home, waiting at the depot that refused it."""
    return {"package_id": package_id, "facility_id": facility,
            "previous_outcome": "Rejected", "weight_g": grams}


def test_a_depot_reject_rides_the_leg_that_leaves_its_depot():
    """§5.3.2: each leg carries "returns bound for the hub".

    Not the outbound leg — the envelope is not on the van until the van has
    been to the depot to get it. So it joins at D1 and stays aboard to the hub.
    """
    night = linehaul.plan([depot("D1", 7 * HOUR, 30)],
                          [envelope("E1", "D1", 16 * HOUR)],
                          [van("V1")], unload_seconds=20 * 60,
                          returning=[reject("R1", "D1")])

    out, home = night.trips[0].legs
    assert out.return_ids == (), "nothing is going back on the way out"
    assert home.return_ids == ("R1",)
    assert (home.from_facility, home.to_facility) == ("D1", "HUB")


def test_the_load_rises_when_the_circuit_picks_returns_up():
    """§7.1 bounds "hub-origin, transfer **and return** envelopes" on any leg.

    The hub load leaves at D1 and the returns join there, so the run home is
    not empty — which is the third of the bullet's three components, and the
    one that was never counted.
    """
    night = linehaul.plan([depot("D1", 7 * HOUR, 30)],
                          [envelope("E1", "D1", 16 * HOUR)],
                          [van("V1")], unload_seconds=20 * 60,
                          returning=[reject("R1", "D1", grams=1000),
                                     reject("R2", "D1", grams=1000)])

    out, home = night.trips[0].legs
    assert out.weight_g == 200, "one hub-origin envelope at §4.1's default"
    assert home.weight_g == 2000, "two returns collected at D1"


def test_returns_at_a_depot_no_van_reaches_stay_there():
    """They are not lost and not silently carried: no leg, so no ride.

    §5.5 gives them the *following* evening, and a depot with nothing inbound
    gets no van tonight — `test_a_depot_with_no_envelopes_gets_no_van` is the
    other half of that rule.
    """
    night = linehaul.plan([depot("D1", 7 * HOUR, 30), depot("D2", 7 * HOUR, 45)],
                          [envelope("E1", "D1", 16 * HOUR)],
                          [van("V1")], unload_seconds=20 * 60,
                          returning=[reject("R1", "D2")])

    assert night.returned == (), "D2 had no van to put them on"
    assert all(leg.return_ids == () for leg in night.trips[0].legs)


def test_what_rode_home_is_read_off_the_legs():
    """`returned` is what the caller moves to the hub, so it must be what the
    plan actually carried rather than a second list kept beside it."""
    night = linehaul.plan([depot("D1", 7 * HOUR, 30)],
                          [envelope("E1", "D1", 16 * HOUR)],
                          [van("V1")], unload_seconds=20 * 60,
                          returning=[reject("R1", "D1"), reject("R2", "D1")])

    assert night.returned == ("R1", "R2")


# ------------------------------------- §4.3: which vans are available at all


def test_a_van_still_out_on_pickups_is_not_given_a_leg():
    """§4.3: "A van on pickups is available for line-haul only after it has
    returned to the hub and unloaded."

    §5.1.3's taper releases some pickup vans and leaves the rest collecting to
    the cut-off — §10 has six on pickups "tapering to 2", so two never return
    in time to haul anything. Those two were being treated as *the earliest
    available vans*, because `_released_at` reads a missing release time as
    zero, and zero sorts first.

    Nothing caught it: the §9.1 `role` reaches the planner from the simulator
    but the day-pipeline fixture was not passing it, so on the path that did
    have the field the two vans were simply never the deciding choice.
    """
    depots = [depot("D1", 7 * HOUR, 30)]
    envelopes = [envelope("E1", "D1", 0)]
    collecting = {"vehicle_id": "VAN-05", "type": "van", "role": "pickup"}
    held = {"vehicle_id": "VAN-07", "type": "van", "role": "linehaul"}

    night = linehaul.plan(depots, envelopes, [collecting, held],
                          unload_seconds=20 * 60)

    assert [t.van_id for t in night.trips] == ["VAN-07"], (
        "the van that never came back cannot carry anything")


def test_a_released_pickup_van_is_available_again():
    """The other half of §4.3: once back and unloaded it may haul."""
    released = {"vehicle_id": "VAN-01", "type": "van", "role": "pickup",
                "linehaul_release_at": 0}

    night = linehaul.plan([depot("D1", 7 * HOUR, 30)],
                          [envelope("E1", "D1", 0)], [released],
                          unload_seconds=20 * 60)

    assert [t.van_id for t in night.trips] == ["VAN-01"]


def test_a_record_that_states_no_role_is_still_available():
    """§9.1 marks the field optional, so absence must not remove a van.

    The test is by exception — anything not marked `pickup` is available —
    because the alternative silently shrinks the night's fleet for a caller
    whose records predate the column.
    """
    unmarked = {"vehicle_id": "VAN-99", "type": "van"}

    assert linehaul.available([unmarked]) == [unmarked]


# ------------------- §5.3's boundary, promoted out of simulation/day.py

READY_AT = {"P-hub": 0, "P-depot": 8 * 3600}
INFLOW = [
    {"package_id": "P-hub", "facility_id": "HUB"},
    {"package_id": "P-depot", "facility_id": "D1"},
    {"package_id": "P-slow", "facility_id": "D2"},   # not ready today
]


def test_depot_bound_is_ready_and_not_at_the_hub():
    """§5.3's whole boundary: ready today, and somewhere the hub is not.

    Hub-direct envelopes are not "not transported" — they are already where
    they will be dispatched from, so they never enter a circuit. An envelope
    still in processing has no ready time and cannot be loaded either.
    """
    moving = linehaul.depot_bound(INFLOW, READY_AT, hub_id="HUB")

    assert [e["package_id"] for e in moving] == ["P-depot"]
    assert moving[0]["expected_ready_at"] == 8 * 3600, "the van needs the time"


def test_depot_bound_does_not_touch_the_records_it_reads():
    """The stamped copy is a copy. §5.4 reads the same inflow afterwards."""
    before = [dict(e) for e in INFLOW]
    linehaul.depot_bound(INFLOW, READY_AT, hub_id="HUB")
    assert INFLOW == before


def test_strip_rolled_takes_back_what_no_circuit_carried():
    """A rolled envelope is still at the hub in the morning.

    Leaving it in the depot's pool would offer §5.4 an envelope that is not
    there — the one error §7.1's depot bullet exists to prevent.
    """
    positioned = {"HUB": [{"package_id": "P-hub"}],
                  "D1": [{"package_id": "P-a"}, {"package_id": "P-rolled"}]}
    night = linehaul.LinehaulPlan(trips=(), rolled={"D1": ("P-rolled",)})

    linehaul.strip_rolled(positioned, night, hub_id="HUB")

    assert [e["package_id"] for e in positioned["D1"]] == ["P-a"]


def test_strip_rolled_leaves_the_hub_alone():
    """Nothing rolls when it never had to travel.

    The hub's pool is not a line-haul destination, so a package id appearing
    in both places is a coincidence of naming, not a package to remove.
    """
    positioned = {"HUB": [{"package_id": "P-rolled"}], "D1": []}
    night = linehaul.LinehaulPlan(trips=(), rolled={"D1": ("P-rolled",)})

    linehaul.strip_rolled(positioned, night, hub_id="HUB")

    assert [e["package_id"] for e in positioned["HUB"]] == ["P-rolled"]
