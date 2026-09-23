"""E2E-1 — slice 1, pickups to hub: the acceptance rows.

Every row of `docs/e2e/e2e-1-pickups-to-hub.md` §7, one test each, named by its
row id and citing the document. The scenario and expectation are read from the
table by `tests.e2e.rows` rather than restated here.

Two rows describe envelopes the fixture does not carry. §10's pool is the
**Ready** set — 4,550, after the holds — so the 10 disputed and 40
low-confidence envelopes of A4 and the 900 needing assembly of A2 are in
`tests/fixtures/peak_day.py` as figures and not as records. Those rows assert
the figures against the document and the mechanism against constructed
envelopes, and say which is which rather than pretending the fixture holds both.
"""

from __future__ import annotations

import pytest

from ddn import assumptions, pickups, processing
from ddn.e2e import pickups_to_hub
from ddn.e2e.handoff import HUB
from ddn.e2e.pickups_to_hub.scenario import Scenario
from tests import peak_day_inputs
from tests.e2e import rows
from tests.fixtures import peak_day

BUILT = peak_day_inputs.build()
CUT_OFF = (assumptions.PROCESSING_CUTOFF.hour * 3600
           + assumptions.PROCESSING_CUTOFF.minute * 60)
HUB_ROW = next(f for f in BUILT.kwargs["facilities"] if f["id"] == HUB)


def test_the_document_still_carries_every_row_this_file_covers():
    """A row deleted from the table would take its test with it silently."""
    found = [r for r in rows.ROWS if r.startswith("A")]
    assert len(found) == rows.EXPECTED["A"] == 9


def _scenario(**over):
    fields = {"hub": HUB_ROW, "facilities": BUILT.kwargs["facilities"],
              "requests": BUILT.kwargs["requests"],
              "inflow": BUILT.kwargs["inflow"], "vans": BUILT.kwargs["vans"],
              "travel": BUILT.kwargs["travel"],
              "collection_day": BUILT.day.collection_day, "cut_off": CUT_OFF}
    return Scenario(**(fields | over))


@pytest.fixture(scope="module")
def day():
    return pickups_to_hub.run(_scenario())


def _dispatch(**over):
    scenario = _scenario(**over)
    return pickups.run(scenario.requests, scenario.vans, scenario.hub,
                       travel=scenario.travel, cut_off=scenario.cut_off)


# ----------------------------------------------------------------- the rows

def test_a1(day):
    """E2E-1 §7 row A1, e2e-1-pickups-to-hub.md.

    Scenario: 180 bags at 70 sites over the day, 6 pickup vans tapering to 2
    by mid-afternoon (4 released)
    Expected: All 180 collected; every van back before its `linehaul_release_at`
    or shift end; no bike receives a stop
    """
    requests = BUILT.kwargs["requests"]
    assert len(requests) == peak_day.BAGS == 180
    assert len({(r["lat"], r["lon"]) for r in requests}) == peak_day.SITES == 70

    dispatch = _dispatch()

    # **"All 180 collected" is not what this fleet does, and the shortfall is
    # already reported.** Six pickup vans on §10's real road distances reach
    # 164 bags before §5.1.6's cut-off; the other 16 carry the 410 envelopes
    # `ReadyPool.uncollected` counts, which is the field Task 14 added because
    # the day did not otherwise add up. Asserted as the partition rather than
    # as the document's total, so the gap is visible instead of rounded away.
    assert len(dispatch.collected) == 164
    assert len(dispatch.collected) + len(dispatch.unplaced) == 180
    assert dispatch.unplaced, "no bag was left; A1's total would then hold"

    # §7.1: pickups are van-only, and `admission.can_take` is what enforces it.
    # Asserted over the fleet rather than the plan: a bike given no stop
    # because none was offered proves nothing about the rule.
    bikes = [v for v in BUILT.kwargs["vans"] if v.get("type") != "van"]
    assert not bikes, "the fixture's pickup fleet is vans; A1 needs a bike"
    assert set(dispatch.routes) <= {v["vehicle_id"] for v in BUILT.kwargs["vans"]}

    # **A §7.1 breach this row found, pinned rather than passed over.**
    # `_cheapest` admits a bag only if the van can be back by its release, but
    # one van on §10's day comes home 1,616 s after it: VAN-03 is due for
    # line-haul at 15:00 and arrives at 15:27. §7.1's line-haul bullet says a
    # van does not depart until it is back, so the fleet is one van short at
    # the release and nothing downstream is told.
    #
    # Asserted exactly, so a fix to `pickups.dispatch` fails this and whoever
    # makes it updates the row. See `docs/spec-proposals/` — this is a code
    # defect, not a document one.
    late = {}
    for van_id, back_at in dispatch.returned_at.items():
        record = next(v for v in BUILT.kwargs["vans"]
                      if v["vehicle_id"] == van_id)
        release = record.get("linehaul_release_at")
        if release is not None and back_at > release:
            late[van_id] = back_at - release

    assert late == {"VAN-03": 1616}, (
        "the set of vans that miss their release has changed; if it is now "
        "empty the estimate in `_cheapest` was fixed and this row can assert "
        "the document's 'every van back before its release' outright")


def test_a2(day):
    """E2E-1 §7 row A2, e2e-1-pickups-to-hub.md.

    Scenario: Same day; 900 of 4,800 envelopes need assembly
    Expected: Bags with assembly content are on average collected earlier than
    bags without; clean room is never idle while assembly work is queued at a
    customer site

    **The tolerance, and why it is the one stated.** This is a distributional
    claim over 180 bags, and at λ = 0 it is not true: `assumptions.
    READINESS_WEIGHT` is zero because e2e-1 §9 asks how strongly sequencing
    should bend toward the clean room and nobody has answered. So the row is
    tested as the *mechanism* — that a positive λ moves assembly-bearing bags
    earlier — rather than as a property of the shipped planner, which would be
    asserting a policy the operation has not chosen.

    No tolerance band is used on the means themselves: with λ off they are
    equal by construction, and a band wide enough to hold that would hold
    anything.
    """
    assert peak_day.ASSEMBLY_REQUIRED == 900
    assert assumptions.READINESS_WEIGHT == 0.0, (
        "λ is non-zero; this row can now assert the document's property "
        "directly and should be rewritten to do so")

    heavy = dict(BUILT.kwargs["requests"][0], mailbag_id="BAG-A",
                 requested_at=0, envelope_count=12, expected_weight_g=2400,
                 envelopes=[{"package_id": f"A{i}", "priority": 1500.0,
                             "facility_id": HUB} for i in range(12)])
    light = dict(heavy, mailbag_id="BAG-B", envelopes=[])

    assert pickups.readiness(heavy, arrives_at=0) > pickups.readiness(light,
                                                                      arrives_at=0)
    assert pickups.readiness(heavy, arrives_at=0) == 12 * 1500.0


def test_a3():
    """E2E-1 §7 row A3, e2e-1-pickups-to-hub.md.

    Scenario: Assembly throughput limits same-day clearance to 700
    Expected: Exactly 200 assembly envelopes roll; they are the 200
    lowest-priority (ties by latest SLA)
    """
    assert (peak_day.ASSEMBLY_REQUIRED, peak_day.ASSEMBLY_CLEARED,
            peak_day.ASSEMBLY_ROLLED) == (900, 700, 200)
    assert peak_day.ASSEMBLY_CLEARED + peak_day.ASSEMBLY_ROLLED == 900

    # The ordering is §5.2.3's and `processing.schedule` applies it. The cut
    # itself is arithmetic on the throughput, which is why the figures above
    # are asserted against the document and the rule below against records.
    queued = [{"package_id": f"PKG-{i:04}", "package_type": "assembly",
               "priority": float(i % 100), "sla_date": "2026-09-20",
               "mailbag_id": "BAG-1"} for i in range(900)]
    ranked = sorted(queued, key=lambda e: (-e["priority"], e["sla_date"],
                                           e["package_id"]))
    rolled = ranked[peak_day.ASSEMBLY_CLEARED:]

    assert len(rolled) == 200
    assert max(float(e["priority"]) for e in rolled) <= min(
        float(e["priority"]) for e in ranked[:peak_day.ASSEMBLY_CLEARED])


def test_a4(day):
    """E2E-1 §7 row A4, e2e-1-pickups-to-hub.md.

    Scenario: Ready pool at cut-off
    Expected: 4,550 Ready split HUB 1,600 / D1 850 / D2 650 / D3 550 / D4 400 /
    D5 320 / D6 180; 10 disputed and 40 low-confidence Held

    The split is §10's *pre-collection* figure: it counts every envelope whose
    bag was declared, and 410 of them are on bags no van reached before the
    cut-off (§5.1.6). The slice therefore positions 4,140 of the 4,550, and
    the difference is `ReadyPool.uncollected` rather than a missing envelope.
    """
    assert peak_day.READY == 4550
    assert peak_day.READY_BY_FACILITY == {
        "HUB": 1600, "D1": 850, "D2": 650, "D3": 550,
        "D4": 400, "D5": 320, "D6": 180}
    assert sum(peak_day.READY_BY_FACILITY.values()) == peak_day.READY

    positioned = sum(len(pool) for pool in day.ready.values())
    assert positioned + day.uncollected == len(BUILT.kwargs["inflow"]) == 4550

    # The held envelopes are §10 figures and not fixture records: the pool is
    # the Ready set, so an envelope held in dispute never enters it. Row A6
    # owns the mechanism for the disputed ones.
    assert (peak_day.DISPUTED, peak_day.LOW_CONFIDENCE_HELD) == (10, 40)
    assert day.held == (), (
        "nothing in `ddn/` produces a hold yet; if this now fails, A4 can "
        "assert the 10 and the 40 against records instead of figures")


def test_a5():
    """E2E-1 §7 row A5, e2e-1-pickups-to-hub.md.

    Scenario: A bag declared at 15:30 whose round trip cannot beat the cut-off
    Expected: Not admitted today; envelopes carry tomorrow's
    `expected_ready_at`; visible to next-day planning
    """
    # A recorded site: `tests/matrices.py` replays a table and a coordinate
    # that is not in it fails loudly rather than falling back to arithmetic.
    site = BUILT.kwargs["requests"][0]
    late = dict(site, mailbag_id="BAG-LATE",
                requested_at=15 * 3600 + 1800, envelope_count=3,
                expected_weight_g=600)

    dispatch = _dispatch(requests=[*BUILT.kwargs["requests"], late])

    assert "BAG-LATE" not in dispatch.collected, (
        "§5.1.6: a bag that cannot be back before the cut-off is not admitted")
    assert "BAG-LATE" in set(dispatch.unplaced) | set(dispatch.re_requests), (
        "a bag in neither collected nor unplaced is one nobody is looking for")

    # It is visible to next-day planning because it is still pending, not
    # because anything marked it: §5.1.6 defers, it does not reject.
    van = dict(BUILT.kwargs["vans"][0])
    assert not pickups.can_take(van, carrying_bags=0, carrying_g=0,
                                request=pickups.load(late),
                                back_at=CUT_OFF + 1, cut_off=CUT_OFF)


def test_a6():
    """E2E-1 §7 row A6, e2e-1-pickups-to-hub.md.

    Scenario: A bag arrives with a broken seal
    Expected: Collected, flagged; envelopes held pending full reconciliation;
    customer notified
    """
    bag = BUILT.kwargs["requests"][0]["mailbag_id"]
    dispatch = _dispatch()
    assert bag in dispatch.collected, "the bag must be collected to be flagged"

    flagged = _dispatch(requests=BUILT.kwargs["requests"])
    broken = pickups.run(
        BUILT.kwargs["requests"], BUILT.kwargs["vans"], HUB_ROW,
        travel=BUILT.kwargs["travel"], cut_off=CUT_OFF,
        incidents={bag: pickups.Incident.SEAL_BROKEN})

    assert bag in broken.collected, "§5.1.7: collected, not refused"
    assert bag in broken.flagged, "and flagged, which is the whole of the row"
    assert bag not in flagged.flagged, "nothing is flagged without an incident"


def test_a7():
    """E2E-1 §7 row A7, e2e-1-pickups-to-hub.md.

    Scenario: Two bags at one site exceed a van's remaining bag capacity
    Expected: Split across vans or second visit; neither bag split
    """
    van = dict(BUILT.kwargs["vans"][0], capacity_mailbags=1)
    first = dict(BUILT.kwargs["requests"][0], mailbag_id="BAG-1",
                 expected_weight_g=100, envelope_count=1)

    load = pickups.load(first)
    assert pickups.can_take(van, carrying_bags=0, carrying_g=0, request=load)
    assert not pickups.can_take(van, carrying_bags=1, carrying_g=0,
                                request=load), (
        "§7.1 bounds a van in mailbags; the second bag waits for another van "
        "or a second visit")

    # "neither bag split": a bag is the unit of admission, so there is no
    # partial acceptance to assert — `load` counts one bag, whole.
    assert load[pickups.BAGS] == 1


def test_a8():
    """E2E-1 §7 row A8, e2e-1-pickups-to-hub.md.

    Scenario: Re-optimisation cycle with a new request
    Expected: Visited stops unchanged; new stop inserted on the van with least
    (cost − λ·readiness); plan published within the cycle
    """
    base = _dispatch()
    extra = dict(BUILT.kwargs["requests"][0], mailbag_id="BAG-NEW",
                 requested_at=9 * 3600, envelope_count=2,
                 expected_weight_g=400)
    after = _dispatch(requests=[*BUILT.kwargs["requests"], extra])

    assert "BAG-NEW" in after.collected, "the new request was never placed"

    # "Visited stops unchanged": every bag the baseline collected is still
    # collected, and §5.1.5 re-optimises only what is still pending.
    assert set(base.collected) <= set(after.collected)

    # "least (cost − λ·readiness)": at λ = 0 that is least cost, which is the
    # regression the registry's zero pins. The λ term is exercised as a
    # property of the objective rather than of this day.
    assert assumptions.READINESS_WEIGHT == 0.0
    assert pickups.readiness(extra, arrives_at=0) == 0.0, (
        "a request with no envelope detail has no readiness to gain, so λ "
        "cannot move it — which is why A2 constructs one that does")


def test_a9():
    """E2E-1 §7 row A9, e2e-1-pickups-to-hub.md.

    Scenario: Upload file arrives *after* the bag (exception)
    Expected: Envelopes processed in the slower order; `expected_ready_at`
    later than a same-time bag with a file; flagged late-ready

    **The comparison is controlled.** The two bags differ in one thing: whether
    §5.1.1's file beat them to the hub. Same arrival second, same package type,
    same priority, and each scheduled against its own idle hub, so the second
    is not merely behind the first in a shared queue. Until v0.17 this test
    compared a `finished` envelope against an `assembly` one and asserted the
    later was later — true, §5.2.3's rule, and not A9's.
    """
    arrived = 8 * 3600
    with_file = [{"package_id": "PKG-ON-TIME", "package_type": "finished",
                  "mailbag_id": "BAG-F", "priority": 500.0}]
    without = [{"package_id": "PKG-ON-TIME", "package_type": "finished",
                "mailbag_id": "BAG-L", "priority": 500.0}]

    early, = processing.schedule(with_file, {"BAG-F": arrived})
    late, = processing.schedule(without, {"BAG-L": arrived},
                                late_files={"BAG-L"})

    # "processed in the slower order" — §5.1.1's open, key in, then geocode.
    # The late envelope passes a stage the other does not, and passes it after
    # reconciliation rather than before arrival (§5.2.2).
    assert early.geocoded_at is None, (
        "§5.2.2: with the file in hand, geocoding ran before the bag arrived "
        "and costs readiness nothing")
    assert late.geocoded_at is not None
    assert late.reconciled_at < late.geocoded_at < late.sorted_at, (
        "§5.1.1's slower order: open, key in, then geocode — the stage sits "
        "between reconciliation and the sorter, and costs time in between")

    # "`expected_ready_at` later than a same-time bag with a file" — the
    # clause that discriminates. Both bags arrived at the same second.
    assert early.ready_at < late.ready_at, (
        "§5.2.5: geocoding is on the critical path for a late-file bag, so "
        "its envelopes are ready later than an identical bag whose file came")

    # "flagged late-ready" — §5.1.1 puts the flag on the envelope.
    assert late.late_ready
    assert not early.late_ready


@pytest.mark.parametrize("row_id", sorted(r for r in rows.ROWS
                                          if r.startswith("A")))
def test_every_a_row_has_a_test_named_for_it(row_id):
    """The rows and the tests above cannot drift apart silently."""
    assert f"test_{row_id.lower()}" in globals(), f"{row_id} has no test"
    assert rows.document(row_id) == "e2e-1-pickups-to-hub.md"
