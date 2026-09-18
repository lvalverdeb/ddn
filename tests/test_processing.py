"""§5.2.5's ready times and §5.2.4's pre-sort."""

from __future__ import annotations

import pytest

from ddn import assumptions
from ddn.processing import Readiness, presort, requires_assembly, schedule
from ddn.processing.readiness import ASSEMBLY_TYPES

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

def test_presort_assigns_each_envelope_to_its_nearest_facility():
    """§3.3, applied at upload-file receipt rather than after collection."""
    near_hub = envelope("P1", lat=9.9333, lon=-84.0833)
    near_d1 = envelope("P2", lat=9.9981, lon=-84.1197)
    assert [s.facility_id for s in presort([near_hub, near_d1], FACILITIES)] == [
        "HUB", "D1"]


def test_a_zip_centroid_between_two_facilities_is_flagged_not_held():
    """§3.2's "[flag these]"; Open Question 14 has not said what to do."""
    midpoint = envelope("P1", lat=9.9657, lon=-84.1015,
                        coord_source="zip_centroid")
    sorted_one = presort([midpoint], FACILITIES)[0]
    assert sorted_one.straddles is True
    assert sorted_one.facility_id in {"HUB", "D1"}, "it is still sorted"
    assert sorted_one.runner_up in {"HUB", "D1"}
    assert sorted_one.facility_id != sorted_one.runner_up


def test_a_geocoded_address_between_two_facilities_is_not_flagged():
    """§3.2's concern is the centroid's artefact, not the geography."""
    midpoint = envelope("P1", lat=9.9657, lon=-84.1015,
                        coord_source="geocoded_address")
    assert presort([midpoint], FACILITIES)[0].straddles is False


def test_an_envelope_beside_one_facility_is_not_flagged():
    beside = envelope("P1", lat=9.9333, lon=-84.0833,
                      coord_source="zip_centroid")
    result = presort([beside], FACILITIES)[0]
    assert result.straddles is False
    assert result.margin_m > assumptions.EQUIDISTANT_MARGIN_M


def test_sorting_into_no_facilities_is_refused():
    with pytest.raises(ValueError, match="HUB or D1-D6"):
        presort([envelope("P1")], [])
