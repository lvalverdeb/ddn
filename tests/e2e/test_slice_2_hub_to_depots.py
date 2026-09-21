"""E2E-2 — slice 2, hub to depots: the acceptance rows, not yet built.

Every row of `docs/e2e/e2e-2-hub-to-depots.md` §7, one test each, named by its
row id. They are `xfail(strict=True)`, so the count of open rows is visible in
the suite output and a row that starts passing fails the build rather than
passing quietly — a row that works by accident has not been accepted.

The scenario and expectation are read from the document by `tests.e2e.rows`
rather than restated here. A stub that copied its row would go on claiming the
old text after a rewording, which is how §10 drifted for two revisions.
"""

from __future__ import annotations

import pytest

from tests.e2e import rows


def test_the_document_still_carries_every_row_this_file_stubs():
    """A row deleted from the table would take its stub with it silently.

    The stubs are generated from the document, so a vanished row leaves no
    failing test behind — only a smaller suite, which nothing notices.
    """
    found = [r for r in rows.ROWS if r.startswith("B")]
    assert len(found) == rows.EXPECTED["B"] == 9


def _not_built(row_id: str) -> None:
    """The seam each row is built through.

    Raising rather than asserting False: an empty body would *pass*, and under
    `strict=True` a passing xfail fails the build — so an unwritten row would
    look like a regression instead of like work outstanding.
    """
    raise NotImplementedError(
        f"{row_id}: {rows.scenario(row_id)} -> {rows.expected(row_id)}")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b1():
    """E2E-2 §7 row B1, e2e-2-hub-to-depots.md.

    Scenario: §10 day: 4,550 Ready; 1,600 hub-direct; 2,950 depot-bound; 4 held vans + 3 released, one late van; D6 at 4 h transit
    Expected: All 4,550 positioned by each facility's morning release; D6 load departs on an overnight run and its van is unavailable next morning; no leg over 500 kg; violation list empty
    """
    _not_built("B1")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b2():
    """E2E-2 §7 row B2, e2e-2-hub-to-depots.md.

    Scenario: No transfers, no returns
    Expected: Plan leg-for-leg identical to the single-destination baseline (regression, Task 8)
    """
    _not_built("B2")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b3():
    """E2E-2 §7 row B3, e2e-2-hub-to-depots.md.

    Scenario: 30 transfers layered on B1 (18 address corrections, 9 misassignments, 3 rebalancing), 2 of which cannot meet SLA
    Expected: 28 carried on inter-depot legs and arrive before deadline; 2 reported to returns; hub-origin loads not displaced except by higher-priority transfers
    """
    _not_built("B3")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b4():
    """E2E-2 §7 row B4, e2e-2-hub-to-depots.md.

    Scenario: Van-hours short by one circuit
    Expected: Dropped loads are the lowest-priority; every SLA-today transfer and envelope is carried; rolled list carries reason "no van"
    """
    _not_built("B4")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b5():
    """E2E-2 §7 row B5, e2e-2-hub-to-depots.md.

    Scenario: 200 envelopes still in assembly at 16:00, expected Ready by 17:30; D2's latest departure 18:00
    Expected: The D2 van waits and carries them; a van for D6 (latest departure 15:00) does not
    """
    _not_built("B5")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b6():
    """E2E-2 §7 row B6, e2e-2-hub-to-depots.md.

    Scenario: 40 zip-centroid envelopes within EQUIDISTANT_MARGIN_M of two depots
    Expected: Kept at hub and flagged; not on any van; appear in rolled list with reason "held-straddle"
    """
    _not_built("B6")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b7():
    """E2E-2 §7 row B7, e2e-2-hub-to-depots.md.

    Scenario: 50 rejected at D3 during the day
    Expected: Ride D3's return leg; arrive at hub; appear in *tomorrow's* return run input, not tonight's
    """
    _not_built("B7")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b8():
    """E2E-2 §7 row B8, e2e-2-hub-to-depots.md.

    Scenario: A van released from pickups at 15:10 with unloading 20 min
    Expected: Not assigned a leg departing before 15:30
    """
    _not_built("B8")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_b9():
    """E2E-2 §7 row B9, e2e-2-hub-to-depots.md.

    Scenario: Rebalancing: D1 projected 900 vs 600 capacity, D2 400 vs 450
    Expected: Proposal to transfer ≤ 50 lowest-priority D1 envelopes to D2 if a D1→D2 leg fits; otherwise no proposal and D1 rolls by priority
    """
    _not_built("B9")
