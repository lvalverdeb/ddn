"""The §10 fixture, checked against §10.

The figures are asserted twice over: once against the section's own text, so a
mistranscription fails rather than propagates, and once against the built
entities, so the builder is held to the figures.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from ddn.model import CoordSource, GeocodeConfidence, Status, VehicleRole, VehicleType
from tests.fixtures import peak_day

SECTION_10 = (
    (Path(__file__).resolve().parent.parent / "docs" / "vrp-problem-definition.md")
    .read_text(encoding="utf-8")
    .split("## 10.")[1]
    .split("## 11.")[0]
)


@pytest.mark.parametrize(
    "figure",
    [
        "120 motorbikes", "10 vans",
        "HUB 48, D1 24, D2 18, D3 14, D4 8, D5 6, D6 2",
        "180 bag requests", "70 customer sites", "4,800 envelopes",
        "900 of which need assembly", "1,300 zip-only",
        "40 low-confidence", "10 discrepancies",
        "clears 700 of the 900", "200 rolled",
        "4,550 envelopes are Ready",
        "HUB 1,600; D1 850; D2 650; D3 550; D4 400; D5 320; D6 180",
        # §10's end of day. Absent from this list until now, which is exactly
        # why v0.13 could restate every one of them without a test moving:
        # the figures that *are* here are checked against the document, and
        # the ones that were not were checked against a copy of themselves.
        "Total unassigned 150", "dispatched 2,950",
        "2,750 delivered", "50 rejected", "30 defective", "120 postponed",
        "80 envelopes to 30 customer sites, 2 vans",
        "4,550 positioned today + 150 unassigned + 120 postponed = 4,820",
    ],
)
def test_the_fixtures_figures_are_section_10s_own_words(figure):
    assert figure in SECTION_10


def test_transcribed_constants_match_those_words():
    assert peak_day.MOTORBIKES == 120
    assert peak_day.VANS == 10
    assert peak_day.SITES == 70
    assert peak_day.BAGS == 180
    assert peak_day.INFLOW == 4800
    assert peak_day.ASSEMBLY_REQUIRED == 900
    assert peak_day.ASSEMBLY_CLEARED + peak_day.ASSEMBLY_ROLLED == 900
    assert peak_day.ZIP_ONLY == 1300
    assert peak_day.LOW_CONFIDENCE_HELD == 40
    assert peak_day.READY == 4550
    assert sum(peak_day.BIKE_ALLOCATION.values()) == peak_day.MOTORBIKES


def test_section_10_closes_and_the_fixture_checks_it():
    """4,800 - 200 - 40 - 10 is 4,550, which is what §10 states as Ready.

    v0.12 said twelve discrepancies and was two short of its own total; the
    fixture carried ten against it and recorded the difference. v0.13 states
    the subtraction outright and lands on ten, so there is nothing left to
    record -- but the arithmetic is still worth asserting, because it is the
    property the document got wrong once.
    """
    held = peak_day.ASSEMBLY_ROLLED + peak_day.LOW_CONFIDENCE_HELD + peak_day.DISPUTED
    assert peak_day.READY + held == peak_day.INFLOW
    assert peak_day.READY == sum(peak_day.READY_BY_FACILITY.values())


# ------------------------------------------------------------ what it builds

def test_loading_the_fixture_gives_the_days_inflow():
    day = peak_day.load()
    assert len(day.envelopes) == peak_day.INFLOW
    assert day.collection_day < day.delivery_day, "§3.1: delivery is D+1"


def test_the_build_is_deterministic():
    assert peak_day.build() == peak_day.build()
    assert peak_day.load() is peak_day.load()


def test_ready_envelopes_match_section_10s_per_facility_split():
    ready = peak_day.load().ready()
    assert len(ready) == peak_day.READY
    assert Counter(e.facility_id for e in ready) == peak_day.READY_BY_FACILITY


def test_every_envelope_not_ready_is_held_where_section_10_holds_it():
    counts = Counter(e.status for e in peak_day.load().envelopes)
    assert counts[Status.RECONCILED] == peak_day.ASSEMBLY_ROLLED
    assert counts[Status.REQUESTED] == peak_day.LOW_CONFIDENCE_HELD
    assert counts[Status.RECEIVED_AT_HUB] == peak_day.DISPUTED


def test_bags_and_sites_divide_as_section_10_says():
    day = peak_day.load()
    assert len(day.requests) == peak_day.SITES
    assert len(day.mailbags) == peak_day.BAGS
    assert sum(bag.envelope_count for bag in day.mailbags) == peak_day.INFLOW
    assert sum(b.assembly_required_count for b in day.mailbags) == 900


def test_assembly_envelopes_are_the_900_the_manifests_declare():
    envelopes = peak_day.load().envelopes
    assert sum(e.package_type == "assembly" for e in envelopes) == 900


def test_the_rolled_two_hundred_are_the_lowest_priority_assembly_work():
    """§10: "the 200 rolled to tomorrow are the lowest-priority ones"."""
    envelopes = peak_day.load().envelopes
    assembly = [e for e in envelopes if e.package_type == "assembly"]
    rolled = [e for e in assembly if e.status is Status.RECONCILED]
    cleared = [e for e in assembly if e.status is not Status.RECONCILED]
    assert len(rolled) == peak_day.ASSEMBLY_ROLLED
    assert max(e.priority for e in rolled) <= min(e.priority for e in cleared)


def test_zip_only_envelopes_end_geocoded_except_the_forty_held():
    """§5.2.2 runs before arrival, so 1,260 of the 1,300 carry an address."""
    counts = Counter(e.coord_source for e in peak_day.load().envelopes)
    assert counts[CoordSource.ZIP_CENTROID] == peak_day.LOW_CONFIDENCE_HELD
    assert counts[CoordSource.GEOCODED_ADDRESS] == (
        peak_day.ZIP_ONLY - peak_day.LOW_CONFIDENCE_HELD
    )
    assert sum(counts.values()) == peak_day.INFLOW


def test_the_only_low_confidence_envelopes_are_the_held_ones():
    """§3.2: low confidence is held and flagged rather than routed."""
    for e in peak_day.load().envelopes:
        if e.geocode_confidence is GeocodeConfidence.LOW:
            assert not e.is_ready


def test_ready_envelopes_carry_the_ready_time_line_haul_planning_needs():
    """§5.2.5: line-haul must know how many will be ready by each departure."""
    for e in peak_day.load().ready():
        assert e.expected_ready_at is not None


def test_some_ready_envelopes_are_due_today():
    """§6.1's hard constraint needs something to bind on."""
    day = peak_day.load()
    due = [e for e in day.ready() if e.must_deliver_today(day.delivery_day)]
    assert due, "no envelope has an SLA date of the delivery day"


def test_the_fleet_is_section_10s_fleet():
    vehicles = peak_day.load().vehicles
    bikes = [v for v in vehicles if v.type is VehicleType.MOTORBIKE]
    vans = [v for v in vehicles if v.type is VehicleType.VAN]
    assert len(bikes) == peak_day.MOTORBIKES
    assert len(vans) == peak_day.VANS
    assert Counter(v.facility_id for v in bikes) == peak_day.BIKE_ALLOCATION


def test_pickups_are_van_only_and_bikes_carry_no_bags():
    """§7.1: mailbags are collected by vans only; motorbikes never pick up."""
    for vehicle in peak_day.load().vehicles:
        if vehicle.type is VehicleType.MOTORBIKE:
            assert vehicle.role is VehicleRole.DELIVERY
            assert vehicle.capacity_mailbags == 0
        else:
            assert vehicle.role in {VehicleRole.PICKUP, VehicleRole.LINEHAUL}


def test_four_of_the_six_pickup_vans_are_released_to_line_haul():
    """§10: "6 on pickups ... tapering to 2 ... as 4 are released"."""
    vans = [v for v in peak_day.load().vehicles if v.type is VehicleType.VAN]
    pickups = [v for v in vans if v.role is VehicleRole.PICKUP]
    assert len(pickups) == peak_day.PICKUP_VANS
    assert sum(v.linehaul_release_at is not None for v in pickups) == 4
