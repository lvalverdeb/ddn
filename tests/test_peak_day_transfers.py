"""The §5.3.2 transfers fixture, checked against the row it is built for.

`docs/e2e/e2e-2-hub-to-depots.md` row **B3** specifies it. The figures are read
out of that document rather than restated here, for the reason
`tests/fixtures/peak_day.py` gives about §10: a fixture checked against a copy
of its own constants agrees with itself whether or not it is right.
"""

from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from pathlib import Path

from ddn import assumptions, linehaul
from ddn.model import TransferReason
from tests.fixtures import peak_day, peak_day_transfers

E2E_2 = (Path(__file__).resolve().parent.parent
         / "docs" / "e2e" / "e2e-2-hub-to-depots.md").read_text(encoding="utf-8")

B3 = next(line for line in E2E_2.splitlines() if line.startswith("| B3 "))


def test_row_b3_still_says_what_this_fixture_was_built_for():
    """If the row is reworded, the fixture is answering a question nobody asked."""
    assert "30 transfers" in B3
    assert "18 address corrections, 9 misassignments, 3 rebalancing" in B3
    assert "2 of which cannot meet SLA" in B3


def test_the_fixture_is_row_b3s_thirty(): 
    """B3's counts, taken off the row's own text."""
    counts = [int(n) for n in re.findall(r"(\d+) (?:address|misassignment|rebalanc)",
                                         B3.replace("misassignments", "misassignment"))]
    fx = peak_day_transfers.load()
    by_reason = {reason: sum(c.reason is reason for c in fx.candidates)
                 for reason in TransferReason}

    assert len(fx.candidates) == 30
    assert [by_reason[TransferReason.ADDRESS_CORRECTION],
            by_reason[TransferReason.MISASSIGNMENT],
            by_reason[TransferReason.REBALANCING]] == counts == [18, 9, 3]


def test_seven_one_refuses_the_two_that_cannot_arrive_in_time():
    """§7.1: "A transfer is not raised for an envelope that cannot reach the
    destination before its SLA date; it goes to the return run instead."

    Asserted through the rule rather than the count: each refused candidate's
    SLA is checked against when the circuit could actually land, so the two are
    two because the rule says so, not because the fixture partitioned them.
    """
    fx = peak_day_transfers.load()
    arrival = datetime.combine(fx.day, time())

    assert len(fx.to_returns) == 2
    for candidate in fx.to_returns:
        expiry = datetime.combine(candidate.envelope.sla_date, time())
        assert expiry + timedelta(days=1) <= arrival, (
            f"{candidate.transfer_id} could still have arrived in time")
    for request in fx.requests:
        assert request.deadline > arrival, (
            f"{request.transfer_id} was raised and cannot make its deadline")


def test_the_deadline_is_the_delivery_mornings_release_not_the_next_ones():
    """§9.1: min(receiving depot's next morning release, SLA date).

    For a candidate whose SLA is days out, the release is the binding half —
    and §5.6 makes it the *delivery* morning's, because line-haul runs on D for
    delivery on D+1. A day later would hand every transfer twenty-four hours of
    slack it does not have, and the refused pair would not notice: their SLA
    bound is tighter either way, so only a far-dated candidate can tell.
    """
    fx = peak_day_transfers.load()
    release = datetime.combine(fx.day, assumptions.ROUTE_RELEASE)
    far = [r for r in fx.requests if r.deadline == release]

    assert far, "no candidate is bounded by the release; the test is vacuous"
    for request in far:
        assert request.deadline.date() == fx.day
        assert request.deadline < datetime.combine(
            fx.day + timedelta(days=1), time()), "a day out"


def test_the_carried_and_the_refused_account_for_every_candidate():
    """§9.2 accounts for each one; none may quietly vanish between the two."""
    fx = peak_day_transfers.load()
    raised = {r.transfer_id for r in fx.requests}
    refused = {c.transfer_id for c in fx.to_returns}

    assert raised | refused == {c.transfer_id for c in fx.candidates}
    assert not raised & refused


def test_the_circuits_run_on_it():
    """The fixture's whole point: `linehaul.plan` accepts it as it stands.

    Depot rows in `plan`'s shape, an inter-depot transit callable §3.1 does not
    supply, and vans whose release times are seconds from the *collection*
    day's midnight — get any of the three wrong and the plan is silently empty
    or a day out.
    """
    fx = peak_day_transfers.load()

    night = linehaul.plan(list(fx.depots), list(fx.hub_loads), list(fx.vans),
                          unload_seconds=30 * 60,
                          transfers=list(fx.requests), transit=fx.transit)

    assert night.trips, "no van went anywhere"
    assert night.carried + night.held == len(fx.hub_loads)
    assert not any(leg.overloaded for t in night.trips for leg in t.legs)


def test_the_clock_is_the_day_before_the_delivery_day():
    """§5.6: line-haul on D, delivery on D+1. `linehaul.plan` measures its
    seconds from D's midnight, so a caller that mixed the two would be a day
    out with arithmetic that still looked right."""
    fx = peak_day_transfers.load()

    assert fx.clock_day == fx.day - timedelta(days=1)
    assert fx.day == peak_day.load().delivery_day


def test_the_hub_load_is_section_10s_depot_bound_pool():
    """B3 layers on B1, which is §10 unchanged — 4,550 Ready, 1,600 hub-direct."""
    fx = peak_day_transfers.load()
    depot_bound = peak_day.READY - peak_day.READY_BY_FACILITY["HUB"]

    assert len(fx.hub_loads) == depot_bound == 2950


def test_the_vans_are_shaped_so_section_4_3_still_applies():
    """§4.3 filtering must survive the trip through this fixture.

    A van record without `role` is available by exception, so dropping the
    field readmits the two §10 leaves collecting to the cut-off — and mapping
    their absent `linehaul_release_at` to 0 is worse than readmitting them,
    because zero sorts first and the van that never came back would be chosen
    ahead of every van waiting at the hub. That is the bug `linehaul.available`
    exists to prevent, reintroduced one layer up.
    """
    fx = peak_day_transfers.load()
    excluded = {v["vehicle_id"] for v in fx.vans} - {
        v["vehicle_id"] for v in linehaul.available(list(fx.vans))}

    assert excluded == {"VAN-05", "VAN-06"}, "§10 tapers to two still out"
    assert all("role" in v for v in fx.vans)
    assert all(v["linehaul_release_at"] != 0 for v in fx.vans), (
        "an absent release is None, never 0 — 0 sorts first")
