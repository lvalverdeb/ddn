"""§5.6's day, run end to end, and chained so the backlog is visible.

§3.1's one-day lag is the whole shape of this module. A day does two unrelated
things at once: it *delivers* the pool that was positioned yesterday, and it
*collects and positions* the pool that will be delivered tomorrow. They share a
date and a van fleet and nothing else, and conflating them is how a plan ends up
line-hauling envelopes it has already delivered.

**What carries into tomorrow is the point.** §8.3 expects a structural gap, and
§10 ends by showing one: its next-day pool is 4,690 against a fleet that can
serve 3,000. A single day hides that; `run_days` is what makes it accumulate.

**Delivery is not solved here by default.** `deliver` is injected, and the
default serves everything offered, because `lastmile.select` has already cut
the pool to what the facility's bikes can carry. A real solver refuses more on
top -- travel the arithmetic does not see -- so a default run is the optimistic
bound, and the honest reading of any figure from it is "before routing". Pass a
solver-backed `deliver` to close that gap.

**Outcome rates come from §10.** They are read off its end of day, so a run that
reproduces §10's 2,880 has confirmed the stages are wired together and nothing
about the operation. `docs/assumptions.md` says this under its own heading and
it is worth repeating wherever a total is produced.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from ddn import assumptions, linehaul, pickups, processing, returns
from ddn.allocation import EFFECTIVE_PER_BIKE, FleetPlan, allocate, place
from ddn.contract import Excluded
from ddn.lastmile import select
from ddn.simulation.capacity import Checks, check
from ddn.simulation.metrics import Metrics, Tally, measure

HOUR = 3600

DELIVERED, REJECTED, DEFECTIVE, POSTPONED = (
    "Delivered", "Rejected", "Returned", "Postponed")

#: §6's three reasons an attempt is not completed, and the invented split
#: between them. The middle one is the one with a consequence: §6 sends an
#: incorrect address back for re-geocoding.
UNAVAILABLE = "recipient unavailable"
BAD_ADDRESS = "incorrect address"
OUT_OF_TIME = "driver out of time"


@dataclass(frozen=True, slots=True)
class Rates:
    """§6's outcomes as per-mille shares, defaulting to §10's own day."""

    delivered: int = assumptions.DELIVERED_PER_MILLE
    rejected: int = assumptions.REJECTED_PER_MILLE
    defective: int = assumptions.RETURNED_PER_MILLE
    postponed: int = assumptions.POSTPONED_PER_MILLE

    def __post_init__(self) -> None:
        total = self.delivered + self.rejected + self.defective + self.postponed
        if total != 1000:
            raise ValueError(
                f"§6's four outcomes must account for every dispatched "
                f"envelope; these sum to {total} per mille, not 1000")

    def draw(self, rng: random.Random) -> str:
        roll = rng.randrange(1000)
        if roll < self.delivered:
            return DELIVERED
        if roll < self.delivered + self.rejected:
            return REJECTED
        if roll < self.delivered + self.rejected + self.defective:
            return DEFECTIVE
        return POSTPONED


def sub_reason(rng: random.Random) -> str:
    """§6: "recipient unavailable, incorrect address, driver out of time"."""
    roll = rng.randrange(1000)
    if roll < assumptions.POSTPONED_UNAVAILABLE_PER_MILLE:
        return UNAVAILABLE
    if roll < (assumptions.POSTPONED_UNAVAILABLE_PER_MILLE
               + assumptions.POSTPONED_BAD_ADDRESS_PER_MILLE):
        return BAD_ADDRESS
    return OUT_OF_TIME


@dataclass(frozen=True)
class State:
    """What one day hands the next."""

    day: date
    #: facility id -> the pool it will route tomorrow morning.
    pools: Mapping[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    #: §5.5: rejected and defective envelopes waiting for a return run.
    returns_queue: tuple[dict[str, Any], ...] = ()
    #: §7.2: where each vehicle was yesterday, so relocation can be counted.
    placement: Mapping[str, str] = field(default_factory=dict)

    @property
    def pool_size(self) -> int:
        return sum(len(pool) for pool in self.pools.values())


@dataclass(frozen=True)
class DayReport:
    """One day, as §11 measures it and §8.3 checks it."""

    day: date
    tally: Tally
    metrics: Metrics
    checks: Checks
    allocation: FleetPlan
    outcomes: dict[str, int] = field(default_factory=dict)
    postponed_reasons: dict[str, int] = field(default_factory=dict)
    unassigned: tuple[Excluded, ...] = ()
    positioned: dict[str, int] = field(default_factory=dict)
    rolled: dict[str, str] = field(default_factory=dict)
    return_stops: int = 0
    carried_into_tomorrow: int = 0


def _served_everything(offered: Sequence[dict[str, Any]], _facility: str,
                       _bikes: int) -> tuple[list[dict[str, Any]],
                                             list[dict[str, Any]]]:
    """The default `deliver`: everything selection offered is attempted."""
    return list(offered), []


def run_day(
    state: State, *,
    facilities: Sequence[dict[str, Any]],
    bikes: Sequence[str],
    vans: Sequence[dict[str, Any]] = (),
    requests: Sequence[dict[str, Any]] = (),
    inflow: Sequence[dict[str, Any]] = (),
    travel: Callable[[float, float, float, float], int] | None = None,
    hub_id: str = "HUB",
    rates: Rates | None = None,
    deliver: Callable[..., tuple[list, list]] = _served_everything,
    allocation: Mapping[str, int] | None = None,
    seed: int = 0,
) -> tuple[DayReport, State]:
    """One D / D+1 cycle: deliver yesterday's pool, position tomorrow's.

    Args:
        state: yesterday's hand-over.
        facilities: §3.1 rows, the hub first.
        bikes: every motorbike id; §4.2 divides them across the facilities.
        vans: §9.1 van records for pickups and line-haul.
        requests: §9.1 mailbag records arriving today (§5.1).
        inflow: the §9.1 envelopes those bags contain (§5.2).
        travel: seconds between two points, for the pickup day. Required when
            `requests` are given; this package invents no speed.
        hub_id: §3.3's hub, which line-hauls nothing to itself.
        rates: §6's outcome shares. Defaults to §10's.
        deliver: how a facility's offered pool is attempted. See the module
            docstring for why the default serves all of it.
        allocation: facility -> bikes, when the day's allocation is already
            decided. §4.2 allocates "based on forecast or known ready-envelope
            counts", and the forecast belongs to the day *before* the one being
            delivered -- so a caller replaying a known day supplies what was
            decided then rather than letting today's pool re-derive it.
        seed: fixes the outcome sampling, so a day replays identically.

    Returns:
        The day's report and the state it hands tomorrow.
    """
    rates = rates or Rates()
    rng = random.Random(seed + state.day.toordinal())
    tally = Tally()

    # ---- §4.2: today's allocation, and what it cost to change yesterday's.
    pools = {f["id"]: list(state.pools.get(f["id"], ())) for f in facilities}
    demand = {facility: len(pool) for facility, pool in pools.items()}
    targets = (dict(allocation) if allocation is not None
               else allocate(demand, len(bikes)) if bikes
               else dict.fromkeys(pools, 0))
    allocation = place(targets, bikes, previous=state.placement)
    per_facility = {f: len(v) for f, v in allocation.by_facility().items()}

    # ---- §5.4 and §8: who is attempted, and who is left.
    unassigned: list[Excluded] = []
    attempted: list[dict[str, Any]] = []
    expired: list[dict[str, Any]] = []
    for facility, pool in pools.items():
        # §6.1: past its SLA date it is not dispatched; it goes back instead.
        live = [e for e in pool if not _expired(e, state.day)]
        expired.extend(e for e in pool if _expired(e, state.day))

        capacity = per_facility.get(facility, 0) * EFFECTIVE_PER_BIKE
        offered, declined = select(live, capacity=capacity, today=state.day)
        unassigned.extend(declined)
        served, refused = deliver(offered, facility, per_facility.get(facility, 0))
        attempted.extend(served)
        unassigned.extend(Excluded(e["package_id"], "time") for e in refused)

    # ---- §6: what happened at the door.
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    postponed: list[dict[str, Any]] = []
    going_back: list[dict[str, Any]] = list(expired)
    delivered_within_sla = first_attempt = 0

    for envelope in attempted:
        outcome = rates.draw(rng)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome == DELIVERED:
            if not _expired(envelope, state.day):
                delivered_within_sla += 1
            if int(envelope.get("attempt_number", 0)) == 0:
                first_attempt += 1
        elif outcome == POSTPONED:
            reason = sub_reason(rng)
            reasons[reason] = reasons.get(reason, 0) + 1
            postponed.append(replace_record(envelope, reason))
        else:
            going_back.append(dict(envelope, previous_outcome=outcome,
                                   facility_id=hub_id))

    # ---- §5.1 to §5.3: today's collection, positioned for tomorrow.
    dispatch = None
    night = linehaul.LinehaulPlan()
    positioned: dict[str, list[dict[str, Any]]] = {f["id"]: [] for f in facilities}
    if requests:
        if travel is None:
            raise ValueError(
                "a pickup day needs travel times; §5.1 cannot be run on a "
                "guessed speed and this package invents none")
        dispatch = pickups.run(
            requests, vans, next(f for f in facilities if f["id"] == hub_id),
            travel=travel,
            cut_off=_seconds(assumptions.PROCESSING_CUTOFF))
        collected = set(dispatch.collected)
        arrival_of = {bag: dispatch.returned_at.get(van, 0)
                      for van, route in dispatch.routes.items() for bag in route}
        ready = processing.schedule(
            [e for e in inflow if e["mailbag_id"] in collected], arrival_of)
        ready_at = {r.package_id: r.ready_at for r in ready}

        # §5.5's stop is the customer's site, not the recipient's address, and
        # the hub knows it from the upload file that brought the bag. Carried
        # on to the envelope now, because by the time an envelope is rejected
        # the request it arrived in is long gone.
        site_of = {r["mailbag_id"]: (r["lat"], r["lon"]) for r in requests}
        by_id = {e["package_id"]: e for e in inflow}
        for package_id, when in ready_at.items():
            envelope = by_id[package_id]
            site = site_of.get(envelope["mailbag_id"])
            positioned.setdefault(envelope["facility_id"], []).append(
                dict(envelope, expected_ready_at=when,
                     customer_lat=site[0] if site else envelope["lat"],
                     customer_lon=site[1] if site else envelope["lon"]))

        depot_bound = [dict(e, expected_ready_at=ready_at[e["package_id"]])
                       for e in inflow
                       if e["package_id"] in ready_at and e["facility_id"] != hub_id]
        night = linehaul.plan([f for f in facilities if f["id"] != hub_id],
                              depot_bound, vans,
                              unload_seconds=assumptions.FACILITY_UNLOAD_MIN * 60)
        rolled_ids = {pid for ids in night.rolled.values() for pid in ids}
        for facility, pool in positioned.items():
            if facility != hub_id:
                positioned[facility] = [e for e in pool
                                        if e["package_id"] not in rolled_ids]

    # ---- §5.5: tonight's return run.
    missing = [e["package_id"] for e in going_back if "customer_lat" not in e]
    if missing:
        raise ValueError(
            f"{len(missing)} envelope(s) going back carry no customer site "
            f"({', '.join(missing[:3])}...); §5.5 returns them to the sender, "
            "which is not the address they were delivered to")
    stops = returns.sites([dict(e, facility_id=hub_id) for e in going_back])

    # ---- tomorrow.
    tomorrow: dict[str, tuple[dict[str, Any], ...]] = {}
    declined_ids = {u.package_id for u in unassigned}
    for facility, yesterdays in pools.items():
        held = [e for e in postponed if e["facility_id"] == facility]
        left = [e for e in yesterdays if e["package_id"] in declined_ids]
        tomorrow[facility] = (*positioned.get(facility, ()), *held, *left)

    tally = Tally(
        ready_pool=state.pool_size,
        dispatched=len(attempted),
        delivered=outcomes.get(DELIVERED, 0),
        delivered_first_attempt=first_attempt,
        delivered_within_sla=delivered_within_sla,
        postponed=outcomes.get(POSTPONED, 0),
        rejected=outcomes.get(REJECTED, 0),
        defective=outcomes.get(DEFECTIVE, 0),
        sla_expired=len(expired),
        unassigned=len(unassigned),
        received=len(inflow),
        received_before_cut_off=len(inflow),
        ready_by_cut_off=sum(len(p) for p in positioned.values()),
        pickups=len(dispatch.visits) if dispatch else 0,
        pickup_wait_seconds=_waiting(dispatch, requests),
        bikes_deployed=sum(per_facility.values()))

    report = DayReport(
        day=state.day,
        tally=tally,
        metrics=measure(tally),
        checks=_checks(state, per_facility, inflow, dispatch, night),
        allocation=allocation,
        outcomes=outcomes,
        postponed_reasons=reasons,
        unassigned=tuple(unassigned),
        positioned={f: len(p) for f, p in positioned.items()},
        rolled=dict(night.reasons),
        return_stops=len(stops),
        carried_into_tomorrow=sum(len(p) for p in tomorrow.values()))

    return report, State(
        day=state.day + timedelta(days=1),
        pools=tomorrow,
        returns_queue=(),
        placement={a.vehicle_id: a.facility_id for a in allocation.allocations})


def run_days(days: int, state: State, **kwargs: Any) -> list[DayReport]:
    """Chain `days` days, so §8.3's gap accumulates instead of averaging away."""
    reports = []
    for _ in range(days):
        report, state = run_day(state, **kwargs)
        reports.append(report)
    return reports


def replace_record(envelope: dict[str, Any], reason: str) -> dict[str, Any]:
    """§6: a postponed envelope is held Ready at its facility for another go."""
    return dict(envelope, status="Ready", previous_outcome=POSTPONED,
                postponed_reason=reason,
                attempt_number=int(envelope.get("attempt_number", 0)) + 1)


def _expired(envelope: Mapping[str, Any], today: date) -> bool:
    raw = envelope.get("sla_date")
    return bool(raw) and date.fromisoformat(raw) < today


def _seconds(clock: Any) -> int:
    return clock.hour * HOUR + clock.minute * 60


def _waiting(dispatch: Any, requests: Sequence[Mapping[str, Any]]) -> int:
    if dispatch is None:
        return 0
    asked = {r["mailbag_id"]: int(r["requested_at"]) for r in requests}
    return sum(max(visit.at - asked.get(visit.mailbag_id, visit.at), 0)
               for visit in dispatch.visits)


def _checks(state: State, per_facility: Mapping[str, int],
            inflow: Sequence[Mapping[str, Any]], dispatch: Any,
            night: linehaul.LinehaulPlan) -> Checks:
    """§8.3's three, from what the day actually did."""
    van_hours = 0.0
    pickup_hours = 0.0
    if dispatch is not None:
        pickup_hours = sum(dispatch.returned_at.values()) / HOUR
        van_hours = len(dispatch.routes) * 11
    linehaul_hours = sum((trip.returns - trip.departure)
                         for trip in night.trips) / HOUR
    van_hours += len(night.trips) * 12

    assembly = sum(1 for e in inflow if e.get("package_type") == "assembly")
    return check(
        pool=state.pool_size,
        motorbikes=sum(per_facility.values()),
        inflow=len(inflow),
        van_hours_available=van_hours,
        pickup_hours=pickup_hours,
        linehaul_hours=linehaul_hours,
        processing_hours=11,
        assembly_share=(assembly / len(inflow)) if inflow else 0.0)
