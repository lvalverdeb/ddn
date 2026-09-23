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
    lifecycle,
    may,
    outcomes,
)
from ddn.model.records import Outcome

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
#: **One row of §9.1 is deliberately absent: `late_ready` (v0.17).** Nothing
#: writes it -- `ready_times` returns times, not envelopes, so §5.1.1's flag
#: lives on `processing.Readiness` and reaches no `Envelope`. Whether it earns
#: a place here at all is open: `docs/spec-proposals/v0.17-late-file.md` §8
#: question 1. Adding it before there is a writer would be a field that is
#: always null and a test that passes because of it.
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
#: Rejected and Returned to Return run, and Return run->Returned to customer (3);
#: Dispatched->Return run, added in v0.15 for a cancellation taken off a route
#: (1).
SECTION_5_2_6_EDGES = 26


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


# --------------------------------------- §6's outcomes, as §5.2.6 statuses

def test_every_outcome_section_6_names_is_an_attempt_outcome_or_cancellation():
    """`records.Outcome` and `lifecycle.AFTER_ATTEMPT` must not drift apart.

    The table is keyed by §6's spelling rather than by the enum, because
    `records` imports `Status` from `lifecycle` and the other direction would
    close the circle. That leaves the same words written in two files, which
    is exactly the arrangement that goes stale — so this is the guard.

    §6 v0.15 has five rows and only four of them are attempt outcomes. The
    partition is asserted rather than the equality: `Cancelled` sits in the
    table because its last column — effect on tomorrow's pool — is answered
    like a rejection's, and it is not something a doorstep can produce.
    """
    assert (lifecycle.ATTEMPT_OUTCOMES | {lifecycle.CANCELLED}
            == {str(o) for o in Outcome})
    assert lifecycle.CANCELLED not in lifecycle.AFTER_ATTEMPT


@pytest.mark.parametrize("outcome", sorted(lifecycle.ATTEMPT_OUTCOMES))
def test_an_outcome_reaches_a_status_dispatched_can_actually_reach(outcome):
    """§6's choice is validated against §5.2.6's edges, not asserted beside them.

    `may` and `advance` check a transition the caller already chose; until now
    nothing in the package chose one, so each caller picked its own and they
    agreed by coincidence. `after_attempt` goes through `advance` so a §5.2.6
    change that stopped drawing one of these raises here, rather than putting
    an envelope in a status three stages later that nothing can move it out of.
    """
    assert lifecycle.may(lifecycle.Status.DISPATCHED,
                         lifecycle.after_attempt(outcome))


def test_an_outcome_section_6_does_not_name_is_refused():
    """Not a default, not a warning — §5.2.6 draws no such edge."""
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.after_attempt("Lost in transit")


def test_only_delivery_settles_an_envelope():
    """§6: a rejection or a defect goes to the return run, a postponement
    comes back to Ready. Both leave something for tomorrow to carry, and a
    day that counted them as finished would lose them."""
    settled = {o for o in lifecycle.ATTEMPT_OUTCOMES if lifecycle.settles(o)}

    assert settled == {str(Outcome.DELIVERED)}


def test_a_cancellation_is_refused_as_an_attempt_outcome():
    """Nobody went to the door, so `after_attempt` must not answer for it.

    §6 v0.15 keeps `Cancelled` in the outcome table for the sake of its last
    column, which is a statement about tomorrow's pool and not about a
    doorstep. Letting it through here would put it in `outcomes.record`'s
    attempt loop and make it consume a draw.
    """
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.after_attempt(lifecycle.CANCELLED)


@pytest.mark.parametrize("current", [lifecycle.Status.READY,
                                     lifecycle.Status.DISPATCHED])
def test_a_cancellation_reaches_the_return_run_from_either_side_of_dispatch(
        current):
    """§6 v0.15's two cases, and the two §5.2.6 edges behind them.

    Before dispatch the envelope is still Ready; on a route it is Dispatched
    and the stop comes off. e2e-3 §2.2 asked only for the first, and the
    second is what its own next sentence needs — until v0.15 §5.2.6 drew
    nothing out of Dispatched but the four attempt outcomes.
    """
    assert lifecycle.after_cancellation(current) is lifecycle.Status.RETURN_RUN


def test_a_cancellation_after_delivery_is_refused_by_the_state_machine():
    """§5.2.6 draws nothing out of Delivered, so the refusal is `advance`'s.

    e2e-3 §2.2's third bullet — "if already visited and delivered, cancellation
    is refused" — needs no rule of its own, and writing one would be a second
    place for it to disagree with §5.2.6. §13.1 turns this into `409` with the
    current state.
    """
    with pytest.raises(lifecycle.IllegalTransition) as refused:
        lifecycle.after_cancellation(lifecycle.Status.DELIVERED)

    assert "§5.2.6" in str(refused.value)


# ------------------------------------ §6 applied to a facility's attempts

def test_record_asks_for_a_reason_immediately_after_the_postponement():
    """The call order is `outcomes.record`'s contract, not an implementation note.

    A caller whose `outcome` and `reason` read the same `random.Random` — which
    is exactly what `simulation.day` does — gets a different day if the reasons
    are drawn in a second pass. A different day with *identical totals*, since
    the same number of draws come off the same generator: the counts match, the
    per-envelope outcomes do not, and nothing downstream of a tally can see it.

    So this pins the sequence directly. Asserting it through a simulated day
    cannot: a test that feeds canned outcomes and a constant reason passes
    either way, which is how this went uncaught at first.
    """
    calls: list[tuple[str, str]] = []
    answers = iter(["Delivered", "Postponed", "Rejected", "Postponed"])

    def outcome(envelope):
        calls.append(("outcome", envelope["package_id"]))
        return next(answers)

    def reason(envelope):
        calls.append(("reason", envelope["package_id"]))
        return "recipient unavailable"

    outcomes.record([{"package_id": f"P-{i}"} for i in range(4)],
                    outcome=outcome, reason=reason)

    assert calls == [("outcome", "P-0"),
                     ("outcome", "P-1"), ("reason", "P-1"),
                     ("outcome", "P-2"),
                     ("outcome", "P-3"), ("reason", "P-3")]


def test_record_never_asks_a_reason_for_anything_but_a_postponement():
    """One draw per envelope, plus one per postponement, and not one more."""
    asked: list[str] = []
    answers = iter(["Delivered", "Rejected", "Returned"])

    outcomes.record([{"package_id": f"P-{i}"} for i in range(3)],
                    outcome=lambda envelope: next(answers),
                    reason=lambda envelope: asked.append(envelope["package_id"]))

    assert asked == []


def test_record_sweeps_the_expired_without_attempting_them():
    """§6.1: expired envelopes were never at a doorstep, and still go back.

    `outcome` is not asked about them — asking would consume a draw for an
    attempt that did not happen — but they are §5.5's load tonight all the
    same, stamped so `returns.goes_back` recognises them.
    """
    attempted = [{"package_id": "P-live"}]
    swept = [{"package_id": "P-old"}]

    recorded = outcomes.record(attempted, swept,
                               outcome=lambda envelope: "Delivered",
                               reason=lambda envelope: "unused")

    assert [a.package_id for a in recorded.attempts] == ["P-live"]
    assert [e["package_id"] for e in recorded.going_back] == ["P-old"]
    assert recorded.going_back[0]["sla_expired"] is True


# ------------------------------- §5.2.6's transfer edges, which nothing took

def test_a_transfer_moves_the_envelope_through_every_status_5_2_6_draws():
    """Postponed -> Transfer requested -> In transfer -> Ready (at new depot).

    All three edges were in `TRANSITIONS` and nothing took any of them, so an
    envelope with a transfer raised against it was indistinguishable from one
    sitting at its depot. §7.1's "an envelope in transfer is not routable
    until it arrives" is a statement about status, so until something wrote
    one there was nothing for a check to read.
    """
    envelope = {"package_id": "PKG-1", "facility_id": "D1",
                "status": "Postponed"}

    requested = outcomes.transfer_requested(envelope)
    moving = outcomes.in_transfer(requested)
    there = outcomes.arrived(moving, "D3")

    assert requested["status"] == "Transfer requested"
    assert moving["status"] == "In transfer"
    assert (there["status"], there["facility_id"]) == ("Ready", "D3")
    assert envelope["status"] == "Postponed", "the caller's record is its own"


def test_the_facility_and_the_status_move_together():
    """§7.1: routable the moment it arrives and not one moment earlier.

    An envelope stamped with its new depot while still `In transfer` is
    exactly what the bullet forbids, and `_handover` used to move the facility
    alone — leaving `Postponed` on a record §5.4 would then be offered.
    """
    moving = outcomes.in_transfer(
        outcomes.transfer_requested({"package_id": "P", "facility_id": "D1",
                                     "status": "Postponed"}))

    assert moving["facility_id"] == "D1", "not there yet"
    assert outcomes.arrived(moving, "D3")["facility_id"] == "D3"


@pytest.mark.parametrize("step, source", [
    (lambda e: outcomes.in_transfer(e), "Ready"),
    (lambda e: outcomes.arrived(e, "D3"), "Delivered"),
    (lambda e: outcomes.transfer_requested(e), "Delivered"),
])
def test_a_transfer_step_out_of_the_wrong_status_is_refused(step, source):
    """Each advances from the record's own status, so §5.2.6 refuses what it
    does not draw.

    `Postponed -> Ready` is deliberately not among these: §5.2.6 draws it
    ("Postponed: back to Ready for next attempt"), so `arrived` from Postponed
    is legal and using it here would be asserting the diagram is something it
    is not.
    """
    with pytest.raises(lifecycle.IllegalTransition):
        step({"package_id": "P", "facility_id": "D1", "status": source})


def test_a_record_with_no_status_is_refused_rather_than_assumed():
    """Assuming it is where the caller wanted it is how a check goes quiet."""
    with pytest.raises(lifecycle.IllegalTransition):
        outcomes.in_transfer({"package_id": "P", "facility_id": "D1"})
