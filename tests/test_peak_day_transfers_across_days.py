"""T9's fourth bullet, run through the simulator: the 28 ride, the 2 do not.

`tests/test_peak_day_baseline.py` pins the control -- §10's day with no
transfers in it, where every transfer field reads zero. This file runs the same
day with `peak_day_transfers`' thirty candidates in hand and follows the
twenty-eight §7.1 permits, one envelope at a time, until they are routed from
the depot they were moved to.

Per package, and not by tally. Twenty-eight is a number several wrong days
produce: twenty-eight routed from the old depot, twenty-eight from a mixture of
both, twenty-eight counted twice on one day and none on the next. So every
assertion below is keyed by `package_id`, and the counts are derived from the
keys rather than asserted beside them.

## Which day is day one

`State(day=fx.clock_day)` -- the collection day -- and this is the
load-bearing choice in the file.

Nothing in `linehaul/` reads the *date* of a deadline. `circuit._due` compares
only its time of day against the receiving depot's release, and the
`MISSES_DEADLINE` branch beside it is a van-feasibility test -- whether a van
can leave late enough and still arrive by then -- so `linehaul.plan` will never
decline a transfer for being a day stale. The one date-sensitive transfer check
in the repository is `postcheck`'s §7.1 bullet, which turns a leg's arrival
second into a moment through `Day.at`, counting from midnight of `state.day`,
and compares it to the deadline stamped when the transfer was raised. `run_day`
runs that check on every day it simulates.

`peak_day_transfers` stamps its deadlines at the delivery day's morning release
-- the day *after* the collection day -- so that arithmetic closes only when day
one is the collection day. Run as `delivery_day` instead, the same twenty-eight
arrive a day after their deadlines: twenty-seven carried, three declined, and
§7.1 violations in the report. That is measured, not predicted.

## About "D+2"

T9's prompt asks for the twenty-eight "delivered from their new depot on D+2".
No section of the problem definition fixes that frame. §5.6 puts line-haul on
day D and delivery on D+1, and that is the frame this repository implements:
raised on the evening of D, flown that night, in the new depot's pool on the
morning of D+1. Per CLAUDE.md's rule for an ambiguous spec, this file asserts
the repository's frame and states the interpretation here rather than bending
the run by a day to reproduce the prompt's wording.

## About "2 in the return run"

The prompt's other half asks that the two §7.1 refuses turn up in the return
run. They do not, and the reason is worth more than the assertion would have
been.

Both are ordinary envelopes sitting at D1 whose SLA date is the day being run.
Three things could route them to a return run and each is shut:
`_raise_transfers` reaches them only behind a doorstep `BAD_ADDRESS` draw that
no seam lets a test nominate; `circuit.choose` declines on van feasibility and
never on a stale deadline, so Step 3's decline-time branch cannot see them; and
`returns.eligible` takes only envelopes already at the hub, which a D1-resident
envelope is not. What the day does instead is deliver them from D1, because on
this day they are still deliverable from D1. The last test below therefore
asserts §7.1's actual guarantee -- that neither is ever transferred -- where
the simulator decides it.

The two halves of the prompt's sentence are not simultaneously satisfiable
against this fixture: the frame that would expire these two into the return
path is the same frame that lands the twenty-eight past their deadlines.

## One discrepancy found on the way, for §12

The two modules bound an SLA date differently.
`tests/fixtures/peak_day_transfers.py:187` takes the **end** of it, arguing
from §6.1 that a date is a day an envelope may still be delivered on;
`ddn/simulation/day.py:401` takes the **start** of it, and refuses anything
whose deadline has reached midnight of the day being run. For these two
candidates the two arithmetics happen to agree, so nothing here fails -- but
§9.1 does not say which is meant, and the answer decides whether an envelope is
still transferable on its SLA date.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from ddn.simulation import State, run_day
from ddn.simulation.day import _served_everything
from tests import peak_day_inputs
from tests.fixtures import peak_day_transfers
from tests.test_peak_day_baseline import TALLY as CONTROL

#: The seed the control day runs at. Two seeds would make this file's figures
#: unrelatable to `test_peak_day_baseline.py`'s.
SEED = 7

#: Three: the twenty-eight reach their last round on day three. A fourth adds
#: no envelope and pins more incidental state.
DAYS = 3

#: §8.3's marginal cost of the night -- the plan as flown less the same plan
#: with no transfers in it.
TRANSFER_VAN_SECONDS = 31159

#: Of the twenty-eight, how many reach a round inside `DAYS`. The other two are
#: D6-bound, and D6 runs two bikes: they are queued behind capacity at the
#: right depot, which the test below asserts rather than takes on trust.
ROUTED = 26


@dataclass(frozen=True, slots=True)
class _Run:
    fixture: Any
    reports: list
    states: list
    #: package_id -> every facility that offered it to a round, in order.
    routed: dict[str, list[str]]


def _pools_saying_what_the_fixture_says(built, fixture) -> dict[str, list[dict]]:
    """§10's pools, made to agree with `peak_day_transfers` about the same rows.

    Two edits, both reconstructions of the fixture's own inputs rather than
    adjustments to make a number come out:

    * the twenty-eight are stamped `Transfer requested`, which is what §5.2.6
      calls an envelope whose transfer has been raised and exactly what
      `outcomes.transfer_requested` stamps when the simulator raises one
      itself. Left `Ready`, the envelope is routable, and its old depot
      delivers it the morning before its own transfer flies.
    * the two §7.1 refuses carry the `sla_date` the fixture gave them --
      `peak_day_transfers.build` re-dates them to the collection day, and a
      pool row that kept the original date would disagree with the candidate
      about one envelope.
    """
    carried = {request.package_id for request in fixture.requests}
    refused = {candidate.envelope.package_id: candidate.envelope.sla_date
               for candidate in fixture.to_returns}
    pools = {}
    for facility, rows in built.pools.items():
        rebuilt = []
        for row in rows:
            package_id = row["package_id"]
            if package_id in carried:
                row = {**row, "status": "Transfer requested"}
            elif package_id in refused:
                row = {**row, "sla_date": refused[package_id].isoformat()}
            rebuilt.append(row)
        pools[facility] = rebuilt
    return pools


@pytest.fixture(scope="module")
def run():
    fixture = peak_day_transfers.build()
    built = peak_day_inputs.build()
    routed: dict[str, list[str]] = {}

    def recording_deliver(offered, facility, bikes):
        """The default `deliver`, wrapped to record who offered what.

        Wrapped rather than replaced: `_served_everything` is `run_day`'s own
        default, so the day measured here is the day the control measures.
        """
        for envelope in offered:
            routed.setdefault(envelope["package_id"], []).append(facility)
        return _served_everything(offered, facility, bikes)

    state = State(day=fixture.clock_day,
                  pools=_pools_saying_what_the_fixture_says(built, fixture),
                  transfers=fixture.requests)
    reports, states = [], []
    for number in range(DAYS):
        # Days two and three take no new work: the question they answer is
        # where the twenty-eight are routed from, and fresh inflow would only
        # add envelopes nobody is following. Nothing below reads their tallies.
        kwargs = built.kwargs if number == 0 else {
            **built.kwargs, "requests": (), "inflow": ()}
        report, state = run_day(state, seed=SEED,
                                deliver=recording_deliver, **kwargs)
        reports.append(report)
        states.append(state)
    return _Run(fixture=fixture, reports=reports, states=states, routed=routed)


def test_the_night_carries_every_transfer_the_fixture_raised(run):
    """§5.3.2's twenty-eight, on the day `linehaul.plan` measures from."""
    day_one = run.reports[0]
    assert day_one.day == run.fixture.clock_day
    assert day_one.transfers_offered == len(run.fixture.requests) == 28
    assert day_one.transfers_carried == 28
    assert day_one.transfers_declined == {}
    assert day_one.transfers_deferred == 0
    assert day_one.transfers_returned == 0
    # No `regeocode` is supplied, so the day raises none of its own: the
    # twenty-eight are the fixture's. That is what makes this a test of the
    # carry rather than of the raiser, which `tests/test_simulation.py` holds.
    assert day_one.transfers_raised == 0
    # §7.1, and the reason day one is the collection day. Under the other
    # frame this reads three violations.
    assert day_one.violations == ()


def test_the_transfers_add_their_van_hours_to_the_control_days(run):
    """§8.3: line-haul hours "including inter-depot legs ... transfers add to it".

    Stated as the control's van-seconds *plus* the marginal cost, so the
    addition is checked rather than a total restated.
    """
    tally = run.reports[0].tally
    assert CONTROL["transfer_van_seconds"] == 0, "the control must have none"
    assert tally.transfer_van_seconds == TRANSFER_VAN_SECONDS
    assert tally.van_seconds == CONTROL["van_seconds"] + TRANSFER_VAN_SECONDS
    assert run.reports[0].metrics.transfer_van_hour_share == pytest.approx(
        TRANSFER_VAN_SECONDS / tally.van_seconds)


def test_every_transferred_envelope_starts_tomorrow_at_its_new_depot(run):
    """§5.2.6's "Ready (at new depot)", per package and not by count."""
    destinations = run.fixture.destinations
    arrived = {row["package_id"]: facility
               for facility, rows in run.states[0].pools.items()
               for row in rows if row["package_id"] in destinations}
    assert arrived == destinations


def test_no_transferred_envelope_is_ever_routed_from_anywhere_else(run):
    """The claim T9 makes, measured across all three days.

    A count of twenty-eight delivered would pass on a day that routed half of
    them from the depot they left, so this asserts the set of facilities each
    envelope was ever offered by -- and then asserts that they were offered at
    all, because "routed from nowhere else" is satisfied by routing nothing.
    """
    destinations = run.fixture.destinations
    elsewhere = {package_id: sorted(set(facilities))
                 for package_id, facilities in run.routed.items()
                 if package_id in destinations
                 and set(facilities) != {destinations[package_id]}}
    assert elsewhere == {}

    reached = {package_id for package_id in destinations
               if package_id in run.routed}
    assert len(reached) == ROUTED

    # And the two that did not reach a round are waiting at the depot they
    # were moved to, rather than lost on the way to it.
    waiting = {row["package_id"]: facility
               for facility, rows in run.states[-1].pools.items()
               for row in rows
               if row["package_id"] in set(destinations) - reached}
    assert waiting == {package_id: destinations[package_id]
                       for package_id in set(destinations) - reached}


def test_the_two_that_cannot_meet_sla_are_never_transferred(run):
    """§7.1's guarantee about `fx.to_returns`, where the simulator decides it.

    Not "they appear in the return run": the module docstring records why they
    cannot, and this asserts what is true instead -- neither is carried on any
    night, and neither is ever offered by a facility other than the one it
    started at.
    """
    origins = {candidate.envelope.package_id: candidate.envelope.facility_id
               for candidate in run.fixture.to_returns}
    assert len(origins) == 2
    assert not set(origins) & set(run.fixture.destinations)

    for report in run.reports:
        assert report.transfers_declined == {}
    assert {package_id: sorted(set(run.routed.get(package_id, [])))
            for package_id in origins} == {
        package_id: [facility] for package_id, facility in origins.items()}
