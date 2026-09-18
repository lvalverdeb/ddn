"""§5.5 — the evening return run.

Unlike §5.3 this *is* a routing problem: §5.5 calls it "a static CVRP from the
hub to customer sites, solved each evening". But it is not last mile with the
arrows reversed, and two differences do the work.

**The stop is a customer site, not an envelope.** §9.1's return-run record is
`customer_id, lat, lon, package_ids` — a list. Several rejected envelopes going
back to the same sender are one visit. In §5.4 one envelope is one stop; here
the aggregation is the first thing that happens.

**The eligible pool is yesterday's outcomes, not today's demand.** §6 sends
Rejected and Returned envelopes back to the customer, and §6.1 adds those whose
SLA date has passed. Everything else is either delivered, or held for another
attempt.
"""

from __future__ import annotations

import pytest

from ddn import returns

HUB = {"id": "HUB", "lat": 9.9472, "lon": -84.0531,
       "shift_start": 61200, "shift_end": 75600}          # 17:00-21:00, late shift


def envelope(package_id: str, customer: str, outcome: str,
             facility: str = "HUB", **over) -> dict:
    return {"package_id": package_id, "customer_id": customer,
            "customer_lat": 9.95, "customer_lon": -84.06,
            "previous_outcome": outcome, "facility_id": facility, **over}


def vehicle(vid: str, kind: str = "van") -> dict:
    return {"vehicle_id": vid, "type": kind, "facility_id": "HUB",
            "role": "return", "capacity_envelopes": 35,
            "capacity_weight_g": 500_000,
            "shift_start": 61200, "shift_end": 75600}


def matrix_for(n: int):
    from vrp.model import TravelMatrix
    rows = tuple(tuple(0 if i == j else 300 for j in range(n)) for i in range(n))
    return TravelMatrix(version="ret", durations=rows, distances=rows)


# --------------------------------------------------------------------------
# Who goes back (§6, §6.1)
# --------------------------------------------------------------------------

def test_rejected_and_defective_envelopes_go_back():
    """§6: both are "back to facility, then customer via return run"."""
    eligible = returns.eligible([envelope("A", "C1", "Rejected"),
                                 envelope("B", "C1", "Returned")])

    assert {e["package_id"] for e in eligible} == {"A", "B"}


def test_a_postponed_envelope_is_held_not_returned():
    """§6: postponed is "held at facility in Ready state", retried while the
    SLA date has not passed. Returning it would end an attempt the operation
    intends to make."""
    assert returns.eligible([envelope("A", "C1", "Postponed")]) == []


def test_a_delivered_envelope_is_closed():
    assert returns.eligible([envelope("A", "C1", "Delivered")]) == []


def test_an_sla_expired_envelope_goes_back_whatever_its_outcome():
    """§6.1: "once the SLA date has passed without delivery, the envelope is
    returned to the customer via the return run" — so it leaves the delivery
    pool even though nobody refused it."""
    stale = envelope("A", "C1", "Postponed", sla_expired=True)

    assert [e["package_id"] for e in returns.eligible([stale])] == ["A"]


def test_a_depot_rejection_waits_for_the_following_evening():
    """§5.5: envelopes rejected at a secondary depot "travel back to the hub on
    the van's return leg and join the following evening's return run".

    They are not at the hub tonight, so a run that included them would be
    planning a visit from a place the envelope is not.
    """
    eligible = returns.eligible([envelope("A", "C1", "Rejected", facility="HUB"),
                                 envelope("B", "C2", "Rejected", facility="D3")])

    assert [e["package_id"] for e in eligible] == ["A"]


# --------------------------------------------------------------------------
# One site, many envelopes (§9.1)
# --------------------------------------------------------------------------

def test_envelopes_to_one_customer_become_one_stop():
    """§9.1's return-run record carries a *list* of package_ids. Three refused
    envelopes going back to one sender is one visit, not three."""
    stops = returns.sites([envelope("A", "C1", "Rejected"),
                           envelope("B", "C1", "Returned"),
                           envelope("C", "C2", "Rejected")])

    assert len(stops) == 2
    by_id = {s.customer_id: s for s in stops}
    assert set(by_id["C1"].package_ids) == {"A", "B"}
    assert by_id["C2"].package_ids == ("C",)


def test_a_stop_carries_the_count_it_is_returning():
    """The CVRP dimension. §4.1 gives vans a weight limit and motorbikes a
    count; a site returning forty envelopes is not one envelope's worth of
    load."""
    stops = returns.sites([envelope(f"P{i}", "C1", "Rejected") for i in range(40)])

    assert stops[0].envelope_count == 40


# --------------------------------------------------------------------------
# The problem it builds (§5.5)
# --------------------------------------------------------------------------

def test_every_route_starts_and_ends_at_the_hub():
    """§5.5: "from the hub to customer sites". A return is a round trip."""
    stops = returns.sites([envelope("A", "C1", "Rejected")])
    problem = returns.to_problem(HUB, stops, [vehicle("V1")], matrix_for(2))

    for v in problem.vehicles:
        assert v.start_location_id == "HUB"
        assert v.end_location_id == "HUB"


def test_the_fleet_is_the_callers_because_question_13_is_open():
    """§5.5 defers it: "if the security rule for mailbags also applies to
    returns, the return run is van-only". Until that is answered the mapping
    must not decide — it takes whatever vehicles it is given."""
    stops = returns.sites([envelope("A", "C1", "Rejected")])

    vans = returns.to_problem(HUB, stops, [vehicle("V1", "van")], matrix_for(2))
    bikes = returns.to_problem(HUB, stops, [vehicle("M1", "motorbike")],
                               matrix_for(2))

    assert vans.vehicles[0].capacities["grams"] == 500_000
    assert bikes.vehicles[0].capacities["grams"] == 35_000


def test_the_service_time_is_per_stop_and_provisional():
    """§7.4 gives ten minutes per envelope *delivered* — recipient opens,
    reviews, signs — and no figure at all for a return.

    It cannot simply be reused: §7.4 calls multiple envelopes to one recipient
    "rare and not modelled separately", while a return stop aggregates them by
    construction. So the figure here is per stop, borrowed, and flagged rather
    than invented per envelope.
    """
    stops = returns.sites([envelope(f"P{i}", "C1", "Rejected") for i in range(5)])
    problem = returns.to_problem(HUB, stops, [vehicle("V1")], matrix_for(2))

    assert problem.orders[0].delivery.service_fixed == returns.SERVICE_SECONDS
    assert returns.SERVICE_SECONDS == 600


def test_a_matrix_that_does_not_span_the_stops_is_refused():
    stops = returns.sites([envelope("A", "C1", "Rejected"),
                           envelope("B", "C2", "Rejected")])

    with pytest.raises(ValueError, match="must span exactly 3"):
        returns.to_problem(HUB, stops, [vehicle("V1")], matrix_for(5))


def test_a_return_cannot_be_declined():
    """Why an infeasible return run is the right answer rather than a partial one.

    §5.5 does not make returns optional: a refused envelope goes back to the
    customer. The model gives return stops no prize, and the platform treats a
    prizeless order as one that "has no price at which declining is
    acceptable, so a plan must place it or report infeasible".

    That is what makes "two vans cannot do this" a usable answer. With a prize
    the solver would quietly hand back a shorter run and the shortfall would
    have to be noticed rather than reported.
    """
    from vrp.model import must_be_served

    stops = returns.sites([envelope("A", "C1", "Rejected")])
    problem = returns.to_problem(HUB, stops, [vehicle("V1")], matrix_for(2))

    assert problem.orders[0].prize == 0
    assert must_be_served(problem.orders[0])


def test_a_vans_envelope_capacity_is_section_4_1s_arithmetic():
    """A van is bounded by weight, not by a motorbike's box.

    `models/ddn-return.json` gave the VAN class `envelopes: 35` — the
    motorbike's number. §4.1 gives the van 500 kg and no envelope count, and
    states the conversion itself: "at the default a van carries up to 2,500
    envelopes on line-haul". The wrong figure made §10's own return run
    infeasible — 80 envelopes, two vans, a 35-envelope cap — which is how it
    was found.
    """
    model = returns.load_model()
    declared = {spec["class"]: spec["capacities"] for spec in model["fleet"]}
    assert declared["VAN"]["grams"] == 500_000
    assert declared["VAN"]["envelopes"] == 500_000 // 200 == 2500
    assert declared["MOTO"]["envelopes"] == 35, "the bike's box is 35 (§7.1)"
