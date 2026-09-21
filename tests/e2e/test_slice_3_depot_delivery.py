"""E2E-3 — slice 3, depot delivery and outcomes: the acceptance rows, not yet built.

Every row of `docs/e2e/e2e-3-depot-delivery-and-outcomes.md` §7, one test each, named by its
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
    found = [r for r in rows.ROWS if r.startswith("C")]
    assert len(found) == rows.EXPECTED["C"] == 12


def _not_built(row_id: str) -> None:
    """The seam each row is built through.

    Raising rather than asserting False: an empty body would *pass*, and under
    `strict=True` a passing xfail fails the build — so an unwritten row would
    look like a regression instead of like work outstanding.
    """
    raise NotImplementedError(
        f"{row_id}: {rows.scenario(row_id)} -> {rows.expected(row_id)}")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c1():
    """E2E-3 §7 row C1, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: D1: 620 in pool, 24 bikes
    Expected: 600 dispatched; 20 unassigned are the lowest-priority non-SLA-today, non-locked; no route exceeds shift; each route ≤ 35 and starts/ends at D1
    """
    _not_built("C1")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c2():
    """E2E-3 §7 row C2, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: HUB: 1,150 in pool, 48 bikes
    Expected: All dispatched
    """
    _not_built("C2")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c3():
    """E2E-3 §7 row C3, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: D2–D6 combined: 1,330 in pool, 48 bikes
    Expected: 130 unassigned across the five, each depot's own lowest-priority
    """
    _not_built("C3")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c4():
    """E2E-3 §7 row C4, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: An SLA-today envelope ranked 619th of 620 at D1
    Expected: Dispatched; a higher-priority non-SLA-today envelope is the one left
    """
    _not_built("C4")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c5():
    """E2E-3 §7 row C5, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Operations locks in 5 envelopes and locks out 3 at D1
    Expected: Re-run: the 5 are on routes, the 3 are unassigned with reason "locked-out", other assignments change minimally
    """
    _not_built("C5")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c6():
    """E2E-3 §7 row C6, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Outcomes at D1: 50 rejected, 30 defective, 120 postponed (40 incorrect address)
    Expected: 80 join D1's return load for the next van leg, not tonight's return run; 80 postponed re-enter D1's pool with attempt+1; 40 re-geocoded — those whose nearest facility changed raise transfer requests, the rest re-enter D1's pool
    """
    _not_built("C6")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c7():
    """E2E-3 §7 row C7, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Same as C6 but at HUB
    Expected: Rejected/defective enter tonight's return run
    """
    _not_built("C7")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c8():
    """E2E-3 §7 row C8, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Customer cancels an envelope at 10:00 that is on a route with ETA 14:00
    Expected: Stop removed at next refresh; envelope → return flow with reason "cancelled"
    """
    _not_built("C8")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c9():
    """E2E-3 §7 row C9, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Customer cancels an envelope already delivered
    Expected: Cancellation refused; state stays Delivered
    """
    _not_built("C9")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c10():
    """E2E-3 §7 row C10, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Envelope with SLA date today is postponed (recipient unavailable)
    Expected: Not re-entered; moves to return flow with reason "SLA expired" at end of day
    """
    _not_built("C10")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c11():
    """E2E-3 §7 row C11, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Envelope postponed three times, SLA in five days
    Expected: Re-enters each day; no cap applied; ranking tie-break favours it over a same-priority first attempt
    """
    _not_built("C11")


@pytest.mark.xfail(strict=True, reason="row not built")
def test_c12():
    """E2E-3 §7 row C12, e2e-3-depot-delivery-and-outcomes.md.

    Scenario: Mutation: disable the home-facility check
    Expected: At least one test fails on the violation *detail* text
    """
    _not_built("C12")
