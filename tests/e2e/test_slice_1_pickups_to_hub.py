"""E2E-1 — slice 1, pickups to hub: the acceptance rows, not yet built.

Every row of `docs/e2e/e2e-1-pickups-to-hub.md` §7, one test each, named by its
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
    found = [r for r in rows.ROWS if r.startswith("A")]
    assert len(found) == rows.EXPECTED["A"] == 9


def _not_built(row_id: str) -> None:
    """The seam each row is built through.

    Raising rather than asserting False: an empty body would *pass*, and under
    `strict=True` a passing xfail fails the build — so an unwritten row would
    look like a regression instead of like work outstanding.
    """
    raise NotImplementedError(
        f"{row_id}: {rows.scenario(row_id)} -> {rows.expected(row_id)}")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a1():
    """E2E-1 §7 row A1, e2e-1-pickups-to-hub.md.

    Scenario: 180 bags at 70 sites over the day, 6 pickup vans tapering to 2 by mid-afternoon (4 released)
    Expected: All 180 collected; every van back before its `linehaul_release_at` or shift end; no bike receives a stop
    """
    _not_built("A1")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a2():
    """E2E-1 §7 row A2, e2e-1-pickups-to-hub.md.

    Scenario: Same day; 900 of 4,800 envelopes need assembly
    Expected: Bags with assembly content are on average collected earlier than bags without; clean room is never idle while assembly work is queued at a customer site
    """
    _not_built("A2")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a3():
    """E2E-1 §7 row A3, e2e-1-pickups-to-hub.md.

    Scenario: Assembly throughput limits same-day clearance to 700
    Expected: Exactly 200 assembly envelopes roll; they are the 200 lowest-priority (ties by latest SLA)
    """
    _not_built("A3")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a4():
    """E2E-1 §7 row A4, e2e-1-pickups-to-hub.md.

    Scenario: Ready pool at cut-off
    Expected: 4,550 Ready split HUB 1,600 / D1 850 / D2 650 / D3 550 / D4 400 / D5 320 / D6 180; 10 disputed and 40 low-confidence Held
    """
    _not_built("A4")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a5():
    """E2E-1 §7 row A5, e2e-1-pickups-to-hub.md.

    Scenario: A bag declared at 15:30 whose round trip cannot beat the cut-off
    Expected: Not admitted today; envelopes carry tomorrow's `expected_ready_at`; visible to next-day planning
    """
    _not_built("A5")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a6():
    """E2E-1 §7 row A6, e2e-1-pickups-to-hub.md.

    Scenario: A bag arrives with a broken seal
    Expected: Collected, flagged; envelopes held pending full reconciliation; customer notified
    """
    _not_built("A6")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a7():
    """E2E-1 §7 row A7, e2e-1-pickups-to-hub.md.

    Scenario: Two bags at one site exceed a van's remaining bag capacity
    Expected: Split across vans or second visit; neither bag split
    """
    _not_built("A7")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a8():
    """E2E-1 §7 row A8, e2e-1-pickups-to-hub.md.

    Scenario: Re-optimisation cycle with a new request
    Expected: Visited stops unchanged; new stop inserted on the van with least (cost − λ·readiness); plan published within the cycle
    """
    _not_built("A8")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_a9():
    """E2E-1 §7 row A9, e2e-1-pickups-to-hub.md.

    Scenario: Upload file arrives *after* the bag (exception)
    Expected: Envelopes processed in the slower order; `expected_ready_at` later than a same-time bag with a file; flagged late-ready
    """
    _not_built("A9")
