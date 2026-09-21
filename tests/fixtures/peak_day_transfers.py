"""§5.3.2's transfers, layered on §10's peak day.

`docs/e2e/e2e-2-hub-to-depots.md` row **B3** is what this fixture is for:
"30 transfers layered on B1 (18 address corrections, 9 misassignments, 3
rebalancing), 2 of which cannot meet SLA". B1 is `peak_day` itself -- the 4,550
Ready, 1,600 hub-direct, 2,950 depot-bound -- so this module adds transfers and
changes nothing else. Every hub-origin figure still comes from `peak_day`.

**Where the 30 come from, since §10 does not mention transfers at all.** The
split is stated by row B3 and by Task 7 of `docs/claude-code-task-prompts.md`,
and by nothing in the spec. It is scenario data, the way `peak_day.SEED` is:
not a `[TBD]` stand-in that any module reads, so it does not belong in
`docs/assumptions.md`. The three reasons are §5.3.2's own three triggers.

**The 28/2 split is derived, not asserted.** §9.1 defines a transfer's deadline
as min(receiving depot's next morning release, SLA date), and §7.1 says "a
transfer is not raised for an envelope that cannot reach the destination before
its SLA date; it goes to the return run instead". Two of the thirty candidates
are drawn from envelopes `peak_day.morning_pool` already makes due today, so
their deadline is this morning's midnight -- already past when the evening plan
is built. The rule does the rest. Nothing here counts to 28 by hand.

**What this module does not do.** It builds no plan and asserts no outcome. It
does not call `linehaul.plan`, and it does not call whatever raises transfers
in `ddn/`: a fixture that read its partition out of the code under test would
agree with that code whether or not the code is right, which is the rule
`peak_day` states and the reason it exists.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from ddn import assumptions
from ddn.model import (
    DEFAULT_WEIGHT_G,
    Envelope,
    TransferReason,
    TransferRequest,
    VehicleType,
)
from ddn.model import travel as road
from ddn.model.travel import Transit
from tests.fixtures import peak_day
from tests.matrices import road_matrix, rows

HOUR = 3600

#: e2e-2 B3. Transcribed, not computed: `test_peak_day_transfers.py` checks
#: that they close (18 + 9 + 3 = 30, 30 - 2 = 28) rather than defining them so.
TRANSFERS = 30
ADDRESS_CORRECTIONS = 18
MISASSIGNMENTS = 9
REBALANCING = 3
CANNOT_MEET_SLA = 2
CARRIED = 28

#: When the transfers are raised: the evening of the day `peak_day` plans, at
#: the hour §5.6 puts the line-haul plan. §5.3.2: "Transfer requests known
#: before the evening line-haul plan are included in that night's circuits."
RAISED_AT = time(18)

#: (origin, destination, reason, count). Origins are depots, because §5.3.2 is
#: "an envelope **already at a depot**"; the envelopes themselves come from
#: `peak_day.morning_pool`, which is where the day's depot-resident envelopes
#: are. Pairs are neighbours on the recorded road network -- the central
#: cluster D1/D2/D3 (18-52 min apart) and D4/D6 on the Pacific side (142 min),
#: which is the one long leg and the only one where the deadline binds.
#:
#: The three rebalancing transfers run D1 -> D2 because §10 states that pair
#: outright: "D1 24 x 25 = 600 vs 620 -> 20 unassigned", against D2's 412 in
#: `peak_day.MORNING_BY_FACILITY` under 18 x 25 = 450. §5.3.2's third trigger
#: is exactly "a depot has more ready envelopes than its allocated motorbikes
#: can deliver before SLA, and a neighbouring depot has slack".
PAIRS: tuple[tuple[str, str, TransferReason, int], ...] = (
    ("D1", "D3", TransferReason.ADDRESS_CORRECTION, 6),
    ("D3", "D1", TransferReason.ADDRESS_CORRECTION, 5),
    ("D2", "D1", TransferReason.ADDRESS_CORRECTION, 4),
    ("D4", "D6", TransferReason.ADDRESS_CORRECTION, 3),
    ("D1", "D2", TransferReason.MISASSIGNMENT, 3),
    ("D3", "D2", TransferReason.MISASSIGNMENT, 3),
    ("D2", "D3", TransferReason.MISASSIGNMENT, 3),
    ("D1", "D2", TransferReason.REBALANCING, 3),
)

#: Which candidates are drawn from an envelope due today. Positions in the
#: expansion of `PAIRS`, chosen so the two fall on different reasons and
#: different destinations; §7.1 then sends both to the return run. Two, because
#: B3 says two.
DUE_TODAY_AT = (0, 18)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One envelope operations wants moved, before §7.1 has ruled on it."""

    envelope: Envelope
    to_facility_id: str
    reason: TransferReason
    transfer_id: str


@dataclass(frozen=True, slots=True)
class PeakDayTransfers:
    """B3's inputs: the peak day, plus thirty transfer candidates."""

    #: The day these envelopes are delivered: §10's D+1.
    day: date
    #: The day the circuits run. `linehaul.plan` measures its seconds from
    #: this midnight (§5.6: line-haul on D, delivery on D+1), so a caller
    #: mixing the two is a day out and the arithmetic still looks right.
    clock_day: date
    #: §3.1 rows for the six depots, in the shape `linehaul.plan` reads them:
    #: `route_release_time` in seconds from midnight, not a `time`.
    depots: tuple[dict, ...]
    #: The hub row as well, for callers that need all seven (§3.3, `presort`).
    hub: dict
    #: §9.1 van records with `linehaul_release_at` in seconds, as `plan` reads
    #: them. Straight from `peak_day`, so the 4-held/3-released split lands
    #: here the day `peak_day` grows it.
    vans: tuple[dict, ...]
    #: B1's 2,950 depot-bound hub-origin envelopes, as `plan` reads them.
    hub_loads: tuple[dict, ...]
    #: Seconds between two facilities by id, off the recorded road table.
    #: §3.1 gives hub transit only and `linehaul.plan` declines every transfer
    #: without this rather than guess one.
    transit: Transit
    #: All thirty, in `PAIRS` order.
    candidates: tuple[Candidate, ...]
    #: The twenty-eight §7.1 permits, ready for `linehaul.plan(transfers=...)`.
    requests: tuple[TransferRequest, ...]
    #: The two it refuses, as candidates: §5.3.2 returns these "to the customer
    #: via the hub", so they are return-run input, not line-haul input.
    to_returns: tuple[Candidate, ...]

    @property
    def envelopes(self) -> dict[str, Envelope]:
        """package_id -> the §9.1 record, for the stages after line-haul."""
        return {c.envelope.package_id: c.envelope for c in self.candidates}

    @property
    def destinations(self) -> dict[str, str]:
        """package_id -> where §5.2.6 leaves it Ready tomorrow.

        §5.2.6 ends a transfer at "Ready (at new depot)". Only the twenty-eight §7.1 raises; the two it
        refuses stay where they are and go to the return run.
        """
        raised = {r.package_id for r in self.requests}
        return {c.envelope.package_id: c.to_facility_id
                for c in self.candidates
                if c.envelope.package_id in raised}


def _seconds(when: time) -> int:
    return when.hour * HOUR + when.minute * 60


def _facility_row(facility) -> dict:
    """A `Facility` in the shape `linehaul.plan` reads.

    `plan` takes plain rows keyed `id` with `route_release_time` in seconds;
    `peak_day` builds `Facility` dataclasses with a `time`. Converted here once
    so the three test modules that each wrote their own stopped having to.
    """
    return {"id": facility.facility_id, "lat": facility.lat,
            "lon": facility.lon,
            "route_release_time": _seconds(facility.route_release_time),
            "transit_from_hub_min": facility.transit_from_hub_min}


def _deadline(envelope: Envelope, delivery_day: date) -> datetime:
    """§9.1: min(receiving depot's next morning release, SLA date).

    §5.6 puts line-haul on day D and delivery on D+1, so the plan is built on
    the evening of D and the receiving depot's *next* morning release is D+1's
    -- `delivery_day` at `ROUTE_RELEASE`, not the day after it. Getting that
    wrong by a day gives every transfer twenty-four hours of slack it does not
    have, and nothing downstream would notice.

    The SLA bound is the end of the SLA date: §6.1 makes "SLA date = today" a
    must-deliver-today constraint, so a date is a day an envelope may still be
    delivered on, not a moment it expires at.
    """
    release = datetime.combine(delivery_day, assumptions.ROUTE_RELEASE)
    expiry = datetime.combine(envelope.sla_date, time()) + timedelta(days=1)
    return min(release, expiry)


def build() -> PeakDayTransfers:
    """B3, deterministically. Same `peak_day`, same thirty candidates."""
    day = peak_day.load()
    today = day.delivery_day
    points = [{"id": f.facility_id, "lat": f.lat, "lon": f.lon}
              for f in day.facilities]

    at_depot: dict[str, list[Envelope]] = {}
    for envelope in peak_day.morning_pool():
        at_depot.setdefault(envelope.facility_id, []).append(envelope)

    wanted = [(frm, to, reason)
              for frm, to, reason, count in PAIRS
              for _ in range(count)]
    taken: set[str] = set()
    candidates: list[Candidate] = []
    for index, (frm, to, reason) in enumerate(wanted):
        due_today = index in DUE_TODAY_AT
        envelope = next(e for e in at_depot[frm] if e.package_id not in taken)
        taken.add(envelope.package_id)
        if due_today:
            # §7.1's refusal needs an envelope that genuinely cannot arrive:
            # the circuit lands on the delivery morning, so an SLA date already
            # behind that is unreachable however the van is routed. Constructed
            # rather than found -- `morning_pool` dates everything on or after
            # the delivery day, so no candidate in it can fail this rule, and a
            # fixture for B3 has to contain the case B3 names.
            envelope = replace(envelope, sla_date=day.collection_day)
        candidates.append(Candidate(envelope, to, reason,
                                    f"TR-{index + 1:02d}"))

    raised = datetime.combine(day.collection_day, RAISED_AT)
    requests: list[TransferRequest] = []
    to_returns: list[Candidate] = []
    for candidate in candidates:
        deadline = _deadline(candidate.envelope, today)
        if deadline <= datetime.combine(today, time()):
            # §7.1: it cannot reach the destination before its SLA date, so it
            # is not raised at all. §5.3.2 sends it back through the hub.
            to_returns.append(candidate)
            continue
        requests.append(TransferRequest(
            transfer_id=candidate.transfer_id,
            package_id=candidate.envelope.package_id,
            from_facility_id=candidate.envelope.facility_id,
            to_facility_id=candidate.to_facility_id,
            reason=candidate.reason,
            created_at=raised,
            deadline=deadline,
            # §4.1's default. `TransferRequest` already defaults to it; named
            # here because the 500 kg leg check reads it.
            weight_g=DEFAULT_WEIGHT_G))

    midnight = datetime.combine(day.collection_day, time())
    ready_at = _seconds(assumptions.PROCESSING_CUTOFF)
    return PeakDayTransfers(
        day=today,
        clock_day=day.collection_day,
        depots=tuple(_facility_row(f) for f in day.facilities if not f.is_hub),
        hub=_facility_row(next(f for f in day.facilities if f.is_hub)),
        vans=tuple(
            # §9.1-shaped, `role` included: `linehaul.available` reads it to
            # apply §4.3, and a record without it readmits the two vans §10
            # leaves collecting to the cut-off. Mapping their absent release
            # to 0 would be worse than readmitting them -- zero sorts first,
            # so the van that never came back would be chosen ahead of every
            # van waiting at the hub.
            {"vehicle_id": v.vehicle_id,
             "type": "van",
             "role": v.role.value,
             "linehaul_release_at": (
                 None if v.linehaul_release_at is None
                 else int((v.linehaul_release_at - midnight).total_seconds()))}
            for v in day.vehicles if v.type is VehicleType.VAN),
        hub_loads=tuple(
            {"package_id": e.package_id, "facility_id": e.facility_id,
             "expected_ready_at": ready_at,
             "weight_g": e.weight_g}
            for e in day.ready() if e.facility_id != "HUB"),
        transit=road.between(road_matrix(points), rows(points)),
        candidates=tuple(candidates),
        requests=tuple(requests),
        to_returns=tuple(to_returns))


@lru_cache(maxsize=1)
def load() -> PeakDayTransfers:
    """B3's inputs, built once per process."""
    return build()
