"""§5.2.5's ready times and §5.2.4's pre-sort."""

from __future__ import annotations

import pytest

from ddn import assumptions
from ddn.processing import Readiness, presort, requires_assembly, schedule
from ddn.processing.readiness import ASSEMBLY_TYPES
from tests.matrices import fake_matrix, rows

HOUR = 3600
FACILITIES = [{"id": "HUB", "lat": 9.9333, "lon": -84.0833},
              {"id": "D1", "lat": 9.9981, "lon": -84.1197}]


def envelope(package_id: str, *, bag: str = "BAG-1", kind: str = "finished",
             **over):
    return {"package_id": package_id, "mailbag_id": bag, "package_type": kind,
            "lat": 9.94, "lon": -84.08, "coord_source": "actual", **over}


def test_ready_time_is_the_sum_section_5_2_5_states():
    """van return + reconciliation + sorting, with no queue ahead of it."""
    ready = schedule([envelope("P1")], {"BAG-1": 10 * HOUR},
                     reconcile_per_hour=3600, sort_per_hour=1800)[0]
    assert ready.reconciled_at == 10 * HOUR + 1
    assert ready.sorted_at == 10 * HOUR + 3
    assert ready.ready_at == ready.sorted_at
    assert ready.assembled_at is None


def test_assembly_adds_a_step_only_for_the_types_that_need_it():
    """§5.2.3: `package_type` identifies which envelopes require the clean room.

    Each is scheduled alone. Together they share the reconciliation and sorting
    servers, and the one behind in the queue can finish last whether or not it
    was assembled -- which is the queue working, not the clean room.
    """
    rates = {"reconcile_per_hour": 3600, "assembly_per_hour": 3600,
             "sort_per_hour": 3600}
    assembled = schedule([envelope("P1", kind="assembly")], {"BAG-1": 0},
                         **rates)[0]
    plain = schedule([envelope("P2")], {"BAG-1": 0}, **rates)[0]
    assert assembled.assembled is True
    assert plain.assembled is False
    assert assembled.ready_at > plain.ready_at


def test_a_throughput_is_a_queue_not_a_private_delay():
    """Ten envelopes at ten an hour take ten hours, not one.

    The alternative reading -- a flat addition per envelope -- makes the hub
    infinitely parallel and §8.3's clean-room bottleneck impossible.
    """
    pool = [envelope(f"P{i}") for i in range(10)]
    done = schedule(pool, {"BAG-1": 0}, reconcile_per_hour=1,
                    sort_per_hour=100_000)
    assert done[-1].ready_at >= 10 * HOUR
    assert done[0].ready_at <= 2 * HOUR, "the first is not made to wait"


def test_the_queue_serves_in_order_of_arrival():
    early = envelope("P-late-id", bag="BAG-1")
    late = envelope("P-early-id", bag="BAG-2")
    done = schedule([late, early], {"BAG-1": 0, "BAG-2": 5 * HOUR},
                    reconcile_per_hour=1, sort_per_hour=100_000)
    assert done[0].package_id == "P-late-id", "off the first van, so first served"


def test_the_same_pool_schedules_the_same_way_twice():
    pool = [envelope(f"P{i}", kind="assembly" if i % 3 else "finished")
            for i in range(50)]
    arrivals = {"BAG-1": 8 * HOUR}
    assert schedule(pool, arrivals) == schedule(pool, arrivals)


def test_an_envelope_whose_bag_is_not_coming_has_no_ready_time():
    """§5.2.5 starts the sum at the van's return; a guess would be a phantom."""
    with pytest.raises(ValueError, match="no expected return"):
        schedule([envelope("P1", bag="BAG-9")], {"BAG-1": 0})


def test_a_throughput_of_zero_is_refused():
    with pytest.raises(ValueError, match="processes nothing"):
        schedule([envelope("P1")], {"BAG-1": 0}, sort_per_hour=0)


def test_requires_assembly_reads_package_type():
    assert requires_assembly(envelope("P1", kind=next(iter(ASSEMBLY_TYPES))))
    assert not requires_assembly(envelope("P1"))


def test_readiness_is_ordered_by_when_the_hub_finishes():
    pool = [envelope(f"P{i}") for i in range(5)]
    done = schedule(pool, {"BAG-1": 0}, reconcile_per_hour=5)
    assert [r.ready_at for r in done] == sorted(r.ready_at for r in done)
    assert isinstance(done[0], Readiness)


# ------------------------------------------------------------------- sorting
#
# §3.3 assigns by *road* distance, so these pass a matrix like every other
# stage. It is a fake — see `tests/matrices.py`; the suite has no gateway — and
# `detour` shapes it like roads rather than pretending it is one.


def sorted_over(envelopes, facilities=FACILITIES, **kwargs):
    points = [*facilities, *envelopes]
    return presort(envelopes, facilities, fake_matrix(points, detour=1.3),
                   rows(points), **kwargs)


def test_presort_assigns_each_envelope_to_its_nearest_facility_by_road():
    """§3.3: "nearest facility by road distance"."""
    near_hub = envelope("P1", lat=9.9333, lon=-84.0833)
    near_d1 = envelope("P2", lat=9.9981, lon=-84.1197)
    assert [s.facility_id for s in sorted_over([near_hub, near_d1])] == [
        "HUB", "D1"]


def test_a_zip_centroid_between_two_facilities_is_flagged_not_held():
    """§3.2's "[flag these]"; Open Question 14 has not said what to do."""
    midpoint = envelope("P1", lat=9.9657, lon=-84.1015,
                        coord_source="zip_centroid")
    one = sorted_over([midpoint])[0]
    assert one.straddles is True
    assert one.facility_id in {"HUB", "D1"}, "it is still sorted"
    assert one.runner_up in {"HUB", "D1"}
    assert one.facility_id != one.runner_up


def test_a_geocoded_address_between_two_facilities_is_not_flagged():
    """§3.2's concern is the centroid's artefact, not the geography."""
    midpoint = envelope("P1", lat=9.9657, lon=-84.1015,
                        coord_source="geocoded_address")
    assert sorted_over([midpoint])[0].straddles is False


def test_an_envelope_beside_one_facility_is_not_flagged():
    beside = envelope("P1", lat=9.9333, lon=-84.0833,
                      coord_source="zip_centroid")
    result = sorted_over([beside])[0]
    assert result.straddles is False
    assert result.margin_m > assumptions.EQUIDISTANT_MARGIN_M


def test_the_margin_is_measured_in_road_metres():
    """A detour factor moves the margin, which a straight line would not."""
    between = envelope("P1", lat=9.9657, lon=-84.1015,
                       coord_source="zip_centroid")
    points = [*FACILITIES, between]
    direct = presort([between], FACILITIES, fake_matrix(points),
                     rows(points))[0]
    winding = presort([between], FACILITIES, fake_matrix(points, detour=2.0),
                      rows(points))[0]
    assert winding.margin_m == pytest.approx(direct.margin_m * 2.0, rel=0.01)


def test_an_envelope_with_no_road_path_is_refused():
    """§9.2 has no reason code for it, so it is raised rather than invented."""
    from vrp.model import UNREACHABLE, TravelMatrix

    from ddn.model import NoRoadPath

    cut_off = TravelMatrix(
        version="island",
        durations=((0, 1, UNREACHABLE), (1, 0, UNREACHABLE),
                   (UNREACHABLE, UNREACHABLE, 0)),
        distances=((0, 1, UNREACHABLE), (1, 0, UNREACHABLE),
                   (UNREACHABLE, UNREACHABLE, 0)))
    stranded = envelope("P1")
    with pytest.raises(NoRoadPath, match="no road path"):
        presort([stranded], FACILITIES, cut_off,
                rows([*FACILITIES, stranded]))


def test_sorting_into_no_facilities_is_refused():
    with pytest.raises(ValueError, match="HUB or D1-D6"):
        sorted_over([envelope("P1")], facilities=[])
