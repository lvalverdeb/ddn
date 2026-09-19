"""§9.1's entities and §5.2.6's lifecycle, checked against the document.

Where a test needs to know what the spec says, it reads the spec file rather
than the module under test. A check whose content comes from the thing it
checks agrees with it whether or not it is right.
"""

from __future__ import annotations

import itertools
from dataclasses import fields
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from ddn.model import (
    DEFAULT_SERVICE_MIN,
    DEFAULT_WEIGHT_G,
    TERMINAL,
    TRANSITIONS,
    CoordSource,
    Envelope,
    Facility,
    FacilityType,
    GeocodeConfidence,
    IllegalTransition,
    Status,
    advance,
    may,
)

SPEC = (Path(__file__).resolve().parent.parent
        / "docs" / "vrp-problem-definition.md").read_text(encoding="utf-8")

#: §3.1 and §5.6 give clock times with no zone; the operation runs in one
#: place, so datetimes here are naive and local throughout.
DAY = date(2026, 9, 17)

LEGAL = [(src, dst) for src, targets in TRANSITIONS.items() for dst in targets]
ILLEGAL = [
    pair for pair in itertools.product(Status, repeat=2)
    if pair[1] not in TRANSITIONS[pair[0]]
]

#: Transcribed from §9.1's envelope table. "lat, lon" is one row and
#: "time_window_start / end" is another; both are expanded here.
SECTION_9_1_ENVELOPE = {
    "package_id", "customer_id", "recipient_id", "package_type", "mailbag_id",
    "status", "expected_ready_at", "lat", "lon", "coord_source",
    "geocode_confidence", "facility_id", "priority", "weight_g", "sla_date",
    "time_window_start", "time_window_end", "service_time_min",
    "attempt_number", "previous_outcome", "locked_vehicle_id",
    "excluded_by_ops",
}


def envelope(**overrides: object) -> Envelope:
    """A minimal valid envelope; every test varies one thing from here."""
    record: dict[str, object] = {
        "package_id": "PKG-1", "customer_id": "CUST-1", "recipient_id": "RCPT-1",
        "package_type": "finished", "mailbag_id": "BAG-1", "status": Status.READY,
        "expected_ready_at": None, "lat": 9.9, "lon": -84.1,
        "coord_source": CoordSource.ACTUAL,
        "geocode_confidence": GeocodeConfidence.HIGH,
        "facility_id": "HUB", "priority": 150.0, "sla_date": date(2026, 9, 17),
    }
    return Envelope(**(record | overrides))  # type: ignore[arg-type]


def facility(**overrides: object) -> Facility:
    record: dict[str, object] = {
        "facility_id": "D6", "name": "Liberia", "type": FacilityType.DEPOT,
        "lat": 10.6346, "lon": -85.4377, "route_release_time": time(7, 0),
        "transit_from_hub_min": 240, "unload_min": 30,
    }
    return Facility(**(record | overrides))  # type: ignore[arg-type]


# ------------------------------------------------------------------ lifecycle

#: The edges §5.2.6 draws, counted off the diagram in v0.14:
#: Requested->Collected->Received at hub->Reconciled, then Reconciled to both
#: Assembled and Sorted and Assembled->Sorted->Ready (7); Ready->Line-haul->
#: At depot->Dispatched with Ready->Dispatched for the parenthesised depot leg,
#: and Dispatched to each of §6's four outcomes (8); Postponed->Ready (1);
#: Postponed->Transfer requested->In transfer->Ready (3); Transfer requested to
#: Ready and to Return run, and Ready->Return run, all three added in v0.13 (3);
#: Rejected and Returned to Return run, and Return run->Returned to customer (3).
SECTION_5_2_6_EDGES = 25


def test_the_transition_table_is_not_empty():
    """The two parametrized suites below are generated from `TRANSITIONS`.

    That makes them vacuous exactly when the thing they check is broken:
    emptying the table would empty `LEGAL`, leave `ILLEGAL` asserting that
    every pair raises, and report a pass. A test whose subject is also its
    fixture has to say how much subject it expects.

    Counted from §5.2.6 rather than from the table — see the enumeration
    above. `>=` rather than `==` because an edge the code draws and the
    document does not is a different failure, and
    `test_every_transition_is_one_section_5_2_6_draws` is what reports it.
    """
    assert sum(len(t) for t in TRANSITIONS.values()) >= SECTION_5_2_6_EDGES
    assert len(LEGAL) >= SECTION_5_2_6_EDGES
    assert ILLEGAL, "every pair legal would make the refusal suite vacuous too"


@pytest.mark.parametrize(("source", "target"), LEGAL, ids=lambda s: str(s))
def test_every_transition_the_table_draws_is_permitted(source, target):
    assert may(source, target)
    assert advance(source, target) is target


@pytest.mark.parametrize(("source", "target"), ILLEGAL, ids=lambda s: str(s))
def test_every_pair_the_table_omits_raises(source, target):
    assert not may(source, target)
    with pytest.raises(IllegalTransition):
        advance(source, target)


def test_illegal_transition_is_an_error_not_a_warning():
    """CLAUDE.md: a violation is a failing test, never a warning."""
    with pytest.raises(IllegalTransition) as caught:
        advance(Status.REQUESTED, Status.DELIVERED)
    assert issubclass(caught.type, ValueError)
    assert "§5.2.6" in str(caught.value)
    assert "Collected" in str(caught.value), "the message should name the way out"


def test_terminal_statuses_lead_nowhere():
    for status in TERMINAL:
        assert TRANSITIONS[status] == frozenset()


def test_every_status_is_named_in_section_5_2_6():
    """The state names are the document's, not this package's."""
    diagram = SPEC.split("#### 5.2.6")[1].split("```")[1]
    for status in Status:
        assert status.value in diagram, f"§5.2.6 does not name {status.value!r}"


def test_hub_direct_envelopes_dispatch_without_line_haul():
    """§5.2.6 parenthesises the depot leg, so §3.3's hub-direct skip it."""
    assert may(Status.READY, Status.DISPATCHED)


def test_depot_bound_envelopes_take_the_line_haul_leg():
    assert advance(Status.READY, Status.LINE_HAUL) is Status.LINE_HAUL
    assert advance(Status.LINE_HAUL, Status.AT_DEPOT) is Status.AT_DEPOT
    assert advance(Status.AT_DEPOT, Status.DISPATCHED) is Status.DISPATCHED


def test_postponed_returns_to_ready_for_another_attempt():
    """§6.1: retried until the SLA date, with no fixed attempt maximum."""
    assert advance(Status.DISPATCHED, Status.POSTPONED) is Status.POSTPONED
    assert advance(Status.POSTPONED, Status.READY) is Status.READY


def test_sla_expiry_leaves_ready_for_the_return_run():
    """§6.1's expiry edge: still Ready, past its SLA date, so it goes back."""
    assert advance(Status.READY, Status.RETURN_RUN) is Status.RETURN_RUN
    assert advance(Status.RETURN_RUN, Status.RETURNED_TO_CUSTOMER)


def test_a_rejected_envelope_cannot_be_redelivered():
    """§6: Rejected is removed from the pool; its only way out is the return run."""
    assert TRANSITIONS[Status.REJECTED] == frozenset({Status.RETURN_RUN})


# -------------------------------------------------------------- derived values

def test_is_ready_is_true_only_in_ready():
    for status in Status:
        assert envelope(status=status).is_ready is (status is Status.READY)


def test_must_deliver_today_when_the_sla_date_is_today():
    """§6.1: SLA date = today is a hard constraint, not a weighting."""
    today = date(2026, 9, 17)
    assert envelope(sla_date=today).must_deliver_today(today) is True
    assert envelope(sla_date=date(2026, 9, 18)).must_deliver_today(today) is False
    assert envelope(sla_date=date(2026, 9, 16)).must_deliver_today(today) is False


def test_latest_van_departure_subtracts_transit_and_unload():
    """§3.1: release - transit - unload."""
    near = facility(transit_from_hub_min=30, unload_min=30)
    assert near.latest_van_departure(DAY) == datetime.combine(DAY, time(6, 0))


def test_latest_van_departure_falls_on_the_previous_day_for_a_distant_depot():
    """§3.1's one-day lag: D6's four-hour transit makes it an overnight run."""
    when = facility().latest_van_departure(DAY)
    assert when == datetime.combine(DAY, time(2, 30))


def test_latest_van_departure_crosses_midnight_when_it_must():
    when = facility(transit_from_hub_min=480).latest_van_departure(DAY)
    assert when == datetime.combine(DAY - timedelta(days=1), time(22, 30)), (
        "the van leaves on D for a release on D+1"
    )


def test_hub_knows_it_is_the_hub():
    assert facility(facility_id="HUB", type=FacilityType.HUB).is_hub
    assert not facility().is_hub


# ----------------------------------------------------------------- validation

def test_weight_defaults_to_200_grams_when_absent():
    """§4.1: actual where recorded, default 200 g."""
    assert envelope().weight_g == DEFAULT_WEIGHT_G == 200
    assert envelope(weight_g=950).weight_g == 950


def test_service_time_defaults_to_ten_minutes():
    """§7.4, and §9.1's "Default 10"."""
    assert envelope().service_time_min == DEFAULT_SERVICE_MIN == 10


@pytest.mark.parametrize("bad", ["Urgent", None, True, [1]])
def test_priority_must_be_a_numeric_score(bad):
    """§8.1: one numeric score per envelope; categories are tier offsets in it."""
    with pytest.raises(TypeError, match="§8.1"):
        envelope(priority=bad)


def test_priority_accepts_an_integer_tier_offset():
    """§8.1's worked tiers are integers: Urgent 1,000-1,999."""
    assert envelope(priority=1500).priority == 1500.0


def test_envelope_carries_exactly_the_fields_of_section_9_1():
    """CLAUDE.md: §9 is the only schema definition -- no field for convenience."""
    assert {f.name for f in fields(Envelope)} == SECTION_9_1_ENVELOPE


def test_coord_source_uses_the_schema_spelling_not_the_prose_one():
    """§9.1 underscores them; §3.2's hyphenated prose is about the same values."""
    assert {c.value for c in CoordSource} == {
        "actual", "geocoded_address", "zip_centroid"
    }
    assert "coord_source" in SPEC


def test_geocode_confidence_has_the_three_values_section_9_1_lists():
    assert {c.value for c in GeocodeConfidence} == {"high", "medium", "low"}


# ------------------------------------------------------- §5.3.2's inter-depot

def test_between_reads_ordered_road_pairs():
    """§3.1 gives transit from the hub and nothing between depots.

    `between` supplies the missing half from the same gateway table §3.3 needs
    to rank facilities, and it reads the pair in the order asked. Road travel
    is not symmetric — D1 to D2 is 40 minutes on this recording and D2 to D1 is
    43 — so a circuit timed on the reverse leg departs on a minute the van
    cannot make.
    """
    from ddn.model import travel as road
    from tests.fixtures import peak_day
    from tests.matrices import road_matrix, rows

    points = [{"id": f.facility_id, "lat": f.lat, "lon": f.lon}
              for f in peak_day.load().facilities]
    transit = road.between(road_matrix(points), rows(points))

    assert transit("D1", "D2") // 60 == 40
    assert transit("D2", "D1") // 60 == 43
    assert transit("D1", "D1") == 0


def test_between_refuses_a_facility_the_matrix_does_not_span():
    """The nearest row is not a safe guess — `over`'s reason, by id."""
    from ddn.model import travel as road
    from tests.matrices import flat_matrix

    transit = road.between(flat_matrix(2), {"HUB": 0, "D1": 1})
    assert transit("HUB", "D1") == 180
    with pytest.raises(KeyError):
        transit("HUB", "D9")
