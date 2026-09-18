"""§4.2's counts and §4.3's van split."""

from __future__ import annotations

import pytest

from ddn import assumptions
from ddn.allocation import EFFECTIVE_PER_BIKE, allocate, place, vans

HOUR = 3600
FLEET = [f"MOTO-{n:03d}" for n in range(1, 121)]


def test_the_rate_is_the_documented_one():
    """§4.2: "~25 envelopes per motorbike per day"."""
    assert EFFECTIVE_PER_BIKE == assumptions.ENVELOPES_PER_BIKE == 25


def test_allocation_divides_the_whole_fleet_and_no_more():
    pools = {"HUB": 1600, "D1": 850, "D2": 650, "D3": 550, "D4": 400,
             "D5": 320, "D6": 180}
    targets = allocate(pools, 120)
    assert sum(targets.values()) == 120
    assert all(count >= 1 for count in targets.values())


def test_a_facility_with_no_demand_gets_no_bikes():
    targets = allocate({"HUB": 100, "D6": 0}, 10)
    assert targets["D6"] == 0


def test_day_one_moves_nothing_because_nothing_was_anywhere():
    plan = place({"HUB": 10, "D1": 5}, FLEET[:15])
    assert plan.moves == 0
    assert len(plan.allocations) == 15


def test_an_unchanged_day_moves_no_vehicle():
    """§7.2: reallocation between consecutive days is a cost, not a free move."""
    targets = {"HUB": 10, "D1": 5}
    first = place(targets, FLEET[:15])
    previous = {a.vehicle_id: a.facility_id for a in first.allocations}
    again = place(targets, FLEET[:15], previous=previous)
    assert again.moves == 0
    assert again.by_facility() == first.by_facility()


def test_only_the_difference_moves_when_demand_shifts():
    first = place({"HUB": 10, "D1": 5}, FLEET[:15])
    previous = {a.vehicle_id: a.facility_id for a in first.allocations}
    shifted = place({"HUB": 7, "D1": 8}, FLEET[:15], previous=previous)
    assert shifted.moves == 3, "three riders relocate, not fifteen"


def test_a_fleet_too_small_for_the_targets_is_refused():
    with pytest.raises(ValueError, match="same fleet"):
        place({"HUB": 10, "D1": 10}, FLEET[:15])


def test_the_van_split_is_section_10s():
    """§10: six on pickups tapering to two, four held for line-haul."""
    plan = vans([f"VAN-{n:02d}" for n in range(1, 11)],
                taper_from=12 * HOUR, taper_until=15 * HOUR)
    roles = [a.role for a in plan.allocations]
    assert roles.count("pickup") == assumptions.PICKUP_VANS_EARMARKED == 6
    assert roles.count("linehaul") == 4
    assert all(a.facility_id == "HUB" for a in plan.allocations), "§4.3: hub-based"


def test_four_of_the_six_are_released_across_the_afternoon():
    """§5.1.3: "progressively released ... as depot departure times approach"."""
    plan = vans([f"VAN-{n:02d}" for n in range(1, 11)],
                taper_from=12 * HOUR, taper_until=15 * HOUR)
    releases = sorted(a.release_at for a in plan.allocations
                      if a.release_at is not None)
    assert len(releases) == 4
    assert releases == sorted(set(releases)), "staggered, not freed together"
    assert releases[0] == 12 * HOUR
    assert releases[-1] == 15 * HOUR


def test_two_vans_keep_collecting_to_the_end():
    plan = vans([f"VAN-{n:02d}" for n in range(1, 11)],
                taper_from=12 * HOUR, taper_until=15 * HOUR)
    still_out = [a for a in plan.allocations
                 if a.role == "pickup" and a.release_at is None]
    assert len(still_out) == assumptions.PICKUP_VANS_AFTER_TAPER == 2


def test_an_earmark_bigger_than_the_fleet_is_refused():
    with pytest.raises(ValueError, match="earmarks"):
        vans(["VAN-01"], earmarked=6, taper_from=0, taper_until=1)


def test_a_taper_that_grows_is_refused():
    with pytest.raises(ValueError, match="cannot end with more"):
        vans([f"VAN-{n:02d}" for n in range(1, 11)], earmarked=2, taper_to=6,
             taper_from=0, taper_until=1)


def test_the_allocation_record_is_section_9_2s():
    """§9.2: "vehicle_id, facility_id, role per day"."""
    one = place({"HUB": 1}, ["MOTO-001"]).allocations[0]
    assert (one.vehicle_id, one.facility_id, one.role) == (
        "MOTO-001", "HUB", "delivery")
