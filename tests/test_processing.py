"""§5.2.5's ready times and §5.2.4's pre-sort."""

from __future__ import annotations

import ast
import pathlib

import pytest

from ddn import assumptions, processing
from ddn.processing import Readiness, presort, requires_assembly, schedule
from ddn.processing.readiness import ASSEMBLY_TYPES
from tests.fixtures import peak_day
from tests.matrices import road_matrix, rows

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
    return presort(envelopes, facilities, road_matrix(points),
                   rows(points), **kwargs)


def test_presort_assigns_each_envelope_to_its_nearest_facility_by_road():
    """§3.3: "nearest facility by road distance"."""
    near_hub = envelope("P1", lat=9.9333, lon=-84.0833)
    near_d1 = envelope("P2", lat=9.9981, lon=-84.1197)
    assert [s.facility_id for s in sorted_over([near_hub, near_d1])] == [
        "HUB", "D1"]


def test_a_zip_centroid_is_sorted_and_carries_its_runner_up():
    """§3.2's "[flag these]"; Open Question 14 has not said what to do.

    The point halfway between the two facilities *as the crow flies* is not
    halfway by road — which is exactly why §3.3 assigns on road distance. It is
    still sorted, and still reports what it nearly went to instead.
    """
    midpoint = envelope("P1", lat=9.9657, lon=-84.1015,
                        coord_source="zip_centroid")
    one = sorted_over([midpoint])[0]
    assert one.facility_id in {"HUB", "D1"}
    assert one.runner_up in {"HUB", "D1"}
    assert one.facility_id != one.runner_up
    assert isinstance(one.straddles, bool), (
        "flagged or not according to road metres, not to appearances")


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
    """A road margin is not the straight-line one, and that is the point.

    Between the hub and D1 the road runs 11,551 m against 8,235 m as the crow
    flies. Any margin computed the short way flags a different set of
    envelopes than §3.2's "straddling two facilities' areas" means.
    """
    between = envelope("P1", lat=9.9657, lon=-84.1015,
                       coord_source="zip_centroid")
    points = [*FACILITIES, between]
    result = presort([between], FACILITIES, road_matrix(points),
                     rows(points))[0]
    nearest = road_matrix(points).distance(2, rows(points)[result.facility_id])
    runner_up = road_matrix(points).distance(2, rows(points)[result.runner_up])
    assert result.margin_m == runner_up - nearest
    assert result.margin_m > 0


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


def test_the_clean_room_works_by_priority_not_by_arrival():
    """§5.2.3: "where the queue exceeds capacity, prioritised by envelope
    priority and SLA date".

    §10 turns that into a checkable claim — "assembly clears 700 of the 900 by
    cut-off, and the 200 rolled to tomorrow are **the lowest-priority ones**".
    The queue was ordered by arrival, so the 200 that rolled were whichever
    bags reached the hub last, and §5.2.3's sentence had no code behind it.

    §5.2.3 also calls assembly the likely bottleneck on a high-volume day,
    which is the day the order decides something.
    """
    day = peak_day.load()
    # Every envelope that arrived, not `ready()` -- that is the pool *after*
    # the cut-off, which is the question being asked rather than the input.
    pool = [{"package_id": e.package_id, "mailbag_id": e.mailbag_id,
             "package_type": e.package_type, "priority": float(e.priority),
             "sla_date": e.sla_date.isoformat()}
            for e in day.envelopes]
    arrival_of = {bag.mailbag_id: 0 for request in day.requests
                  for bag in request.mailbags}

    done = {r.package_id: r for r in schedule(pool, arrival_of)}
    needs = [e for e in pool if e["package_type"] == "assembly"]
    assert len(needs) == peak_day.ASSEMBLY_REQUIRED, "§10's 900"

    by_finish = sorted(needs, key=lambda e: done[e["package_id"]].assembled_at)
    cleared = by_finish[:peak_day.ASSEMBLY_CLEARED]
    rolled = by_finish[peak_day.ASSEMBLY_CLEARED:]
    assert len(rolled) == peak_day.ASSEMBLY_ROLLED, "§10's 200"

    assert min(e["priority"] for e in cleared) >= max(
        e["priority"] for e in rolled), (
        "§10: the ones that roll are the lowest-priority ones, so no envelope "
        "that cleared may be worth less than one that rolled")


def test_a_closer_sla_date_goes_first_among_equals():
    """§5.2.3 names priority *and* SLA date. The second breaks the first's
    ties, and §6.1 is why: the clock is the thing priority cannot express once
    two envelopes are worth the same."""
    same = [envelope("LATE", kind="assembly", priority=100,
                     sla_date="2026-09-30"),
            envelope("SOON", kind="assembly", priority=100,
                     sla_date="2026-09-17")]

    done = {r.package_id: r for r in schedule(same, {"BAG-1": 0},
                                              assembly_per_hour=1)}

    assert done["SOON"].assembled_at < done["LATE"].assembled_at


# ------------------------------- §5.2.5 over a day's pickups (was in day.py)

class _Dispatch:
    """The three attributes `ready_times` reads off a §5.1 `pickups.Dispatch`."""

    def __init__(self, collected, routes, returned_at):
        self.collected, self.routes, self.returned_at = collected, routes, returned_at


def test_ready_times_covers_the_bags_that_came_back_and_no_others():
    """§5.2.5 times what arrived. A bag still at the customer has no ready time.

    The distinction matters downstream: §5.3 loads a van from this mapping, so
    an envelope given a time it has not earned would be promised to a depot it
    is not travelling to.
    """
    inflow = [{"package_id": "P-1", "mailbag_id": "BAG-1", "package_type": "finished"},
              {"package_id": "P-2", "mailbag_id": "BAG-2", "package_type": "finished"}]
    dispatch = _Dispatch(collected={"BAG-1"}, routes={"VAN-1": ["BAG-1"]},
                         returned_at={"VAN-1": 9 * 3600})

    ready_at = processing.ready_times(dispatch, inflow)

    assert set(ready_at) == {"P-1"}
    assert ready_at["P-1"] > 9 * 3600, "ready is after the bag got back, not at"


def test_ready_times_takes_a_dispatch_without_importing_pickups():
    """§5.2 is downstream of §5.1 in the day but not in the import graph.

    Three attribute reads are not worth tying the two stages together, and the
    stand-in above is the proof: anything answering `collected`, `routes` and
    `returned_at` works.
    """
    imported = {name
                for node in ast.walk(ast.parse(
                    pathlib.Path("ddn/processing/readiness.py").read_text()))
                if isinstance(node, ast.ImportFrom)
                for name in [node.module or ""]}
    assert not any(m.startswith("ddn.pickups") for m in imported), imported


def test_position_carries_the_sender_site_off_the_request():
    """§5.5 returns an envelope to the *sender*, whose site only the bag knows.

    By the time an envelope is rejected the request it arrived in is long gone,
    so the coordinates are stamped on now. §9.1's envelope table has no field
    for them, which is why they ride on the record rather than being looked up.
    """
    requests = [{"mailbag_id": "BAG-1", "lat": 9.93, "lon": -84.08}]
    inflow = [{"package_id": "P-1", "mailbag_id": "BAG-1", "facility_id": "D1",
               "lat": 10.0, "lon": -84.2}]
    positioned: dict[str, list] = {}

    processing.position(positioned, {"P-1": 8 * 3600}, requests, inflow)

    placed, = positioned["D1"]
    assert (placed["customer_lat"], placed["customer_lon"]) == (9.93, -84.08)
    assert (placed["lat"], placed["lon"]) == (10.0, -84.2), "recipient unchanged"
    assert placed["expected_ready_at"] == 8 * 3600


def test_position_falls_back_to_the_envelope_when_no_request_carries_the_bag():
    """A bag with no matching request still gets a site rather than none.

    `returns.sites` reads the field unconditionally and `simulation.day`
    refuses to guess when it is missing, so an absent request must degrade to
    a worse answer, not to a crash two stages later.
    """
    inflow = [{"package_id": "P-1", "mailbag_id": "BAG-MISSING",
               "facility_id": "HUB", "lat": 10.0, "lon": -84.2}]
    positioned: dict[str, list] = {}

    processing.position(positioned, {"P-1": 0}, [], inflow)

    placed, = positioned["HUB"]
    assert (placed["customer_lat"], placed["customer_lon"]) == (10.0, -84.2)


def test_why_the_pipeline_does_not_presort_and_what_would_change_it():
    """§3.3's rule has one caller, and the reason is the road recording.

    `presort` ranks by road distance, so it needs a matrix over every envelope
    address. `tests/matrices.py` replays 94 recorded points — the facilities
    and the pickup sites — and refuses a coordinate it does not hold rather
    than falling back to arithmetic, which §3.3 exists to prevent. So the
    suite cannot sort §10's 4,550 envelopes at all, and the day pipeline reads
    the `facility_id` §5.2.4 says the hub stamps at file receipt.

    Measured rather than asserted from memory: calling `presort` on the
    fixture's own inflow raises, naming the first missing coordinate.

    If envelope-level travel ever arrives — §3.1's real coordinates, or a
    recording extended to addresses — this test fails and `presort` should be
    wired into `processing`'s stage properly, along with e2e-2 §2 item 1's
    `keep_straddlers_at_hub`.
    """
    from ddn.model import travel as road
    from tests import peak_day_inputs
    from tests.matrices import road_matrix

    built = peak_day_inputs.build()
    facilities = built.kwargs["facilities"]
    sample = built.kwargs["inflow"][:20]
    points = [*facilities, *sample]

    with pytest.raises(KeyError, match="no recorded road travel"):
        processing.presort(sample, facilities, road_matrix(points),
                           road.index_of(points))
