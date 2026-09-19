"""§5.6's day, run end to end, and chained so the backlog is visible.

§3.1's one-day lag is the whole shape of this module. A day does two unrelated
things at once: it *delivers* the pool that was positioned yesterday, and it
*collects and positions* the pool that will be delivered tomorrow. They share a
date and a van fleet and nothing else, and conflating them is how a plan ends up
line-hauling envelopes it has already delivered.

**What carries into tomorrow is the point.** §8.3 expects a structural gap, and
§10 ends by showing one: its next-day pool is 4,820 against a fleet that can
serve 3,000. A single day hides that; `run_days` is what makes it accumulate.

**Delivery is not solved here by default.** `deliver` is injected, and the
default serves everything offered, because `lastmile.select` has already cut
the pool to what the facility's bikes can carry. A real solver refuses more on
top -- travel the arithmetic does not see -- so a default run is the optimistic
bound, and the honest reading of any figure from it is "before routing". Pass a
solver-backed `deliver` to close that gap.

**Outcome rates come from §10.** They are read off its end of day, so a run that
reproduces §10's 2,750 has confirmed the stages are wired together and nothing
about the operation. `docs/assumptions.md` says this under its own heading and
it is worth repeating wherever a total is produced.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from ddn import assumptions, linehaul, pickups, processing, returns
from ddn.allocation import EFFECTIVE_PER_BIKE, FleetPlan, allocate, place
from ddn.contract import Excluded
from ddn.lastmile import select
from ddn.linehaul import Transit
from ddn.model import TransferReason, TransferRequest
from ddn.simulation.capacity import Checks, check
from ddn.simulation.metrics import Metrics, Tally, measure
from ddn.solver_adapter import Day, check_day_constraints

HOUR = 3600

DELIVERED, REJECTED, DEFECTIVE, POSTPONED = (
    "Delivered", "Rejected", "Returned", "Postponed")

#: §6's three reasons an attempt is not completed, and the invented split
#: between them. The middle one is the one with a consequence: §6 sends an
#: incorrect address back for re-geocoding.
#: §5.3.2's first trigger needs a corrected facility, which only the caller
#: can supply: given a postponed envelope, the facility its real address
#: belongs to, or `None` if the correction changes nothing.
Regeocode = Callable[[dict[str, Any]], str | None]

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
    #: §5.3.2: raised today, carried tonight, and declined with §9.2's reason.
    transfers_raised: int = 0
    transfers_carried: int = 0
    transfers_declined: dict[str, str] = field(default_factory=dict)
    #: §7.1's day-spanning bullets, over this day's own plans (§13.1).
    violations: tuple[Any, ...] = ()


def _served_everything(offered: Sequence[dict[str, Any]], _facility: str,
                       _bikes: int) -> tuple[list[dict[str, Any]],
                                             list[dict[str, Any]]]:
    """The default `deliver`: everything selection offered is attempted."""
    return list(offered), []


@dataclass(frozen=True, slots=True)
class _Attempted:
    """§5.4 and §8: who the facilities took out, and who they left."""

    attempted: list[dict[str, Any]]
    unassigned: list[Excluded]
    expired: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _Doorstep:
    """§6: what came of the attempts."""

    outcomes: dict[str, int]
    reasons: dict[str, int]
    postponed: list[dict[str, Any]]
    going_back: list[dict[str, Any]]
    within_sla: int
    first_attempt: int


@dataclass(frozen=True, slots=True)
class _Collection:
    """§5.1 to §5.3: the day's inflow, positioned for tomorrow."""

    dispatch: Any
    night: linehaul.LinehaulPlan
    positioned: dict[str, list[dict[str, Any]]]


def _attempt(pools: Mapping[str, list[dict[str, Any]]],
             per_facility: Mapping[str, int], *, today: date,
             deliver: Callable[..., tuple[list, list]]) -> _Attempted:
    """Each facility's pool, cut to capacity and attempted.

    §6.1 comes first: an envelope past its SLA date is not dispatched at all,
    it goes back. What remains is §8's decision -- `lastmile.select` trims to
    what the bikes can carry -- and only then is anything attempted.
    """
    attempted: list[dict[str, Any]] = []
    unassigned: list[Excluded] = []
    expired: list[dict[str, Any]] = []

    for facility, pool in pools.items():
        live = [e for e in pool if not _expired(e, today)]
        expired.extend(e for e in pool if _expired(e, today))

        bikes = per_facility.get(facility, 0)
        offered, declined = select(live, capacity=bikes * EFFECTIVE_PER_BIKE,
                                   today=today)
        unassigned.extend(declined)
        served, refused = deliver(offered, facility, bikes)
        attempted.extend(served)
        unassigned.extend(Excluded(e["package_id"], "time") for e in refused)

    return _Attempted(attempted, unassigned, expired)


def _doorstep(attempt: _Attempted, *, rates: Rates, rng: random.Random,
              today: date, hub_id: str) -> _Doorstep:
    """§6's outcomes, drawn per envelope in a fixed order so a day replays."""
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    postponed: list[dict[str, Any]] = []
    # §6.1: "after that it is returned to the customer via the return run".
    # The flag is what `returns.goes_back` reads; without it an expired
    # envelope reached the return run and was filtered straight back out,
    # which is an envelope destroyed rather than returned.
    going_back: list[dict[str, Any]] = [dict(e, sla_expired=True)
                                        for e in attempt.expired]
    within_sla = first_attempt = 0

    for envelope in attempt.attempted:
        outcome = rates.draw(rng)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if outcome == DELIVERED:
            if not _expired(envelope, today):
                within_sla += 1
            if int(envelope.get("attempt_number", 0)) == 0:
                first_attempt += 1
        elif outcome == POSTPONED:
            reason = sub_reason(rng)
            reasons[reason] = reasons.get(reason, 0) + 1
            postponed.append(replace_record(envelope, reason))
        else:
            # §5.5: the envelope is where it was refused, which for a depot
            # reject is the depot. Stamping the hub here made every rejection
            # hub-resident the instant it happened, so it joined *tonight's*
            # return run -- the one thing §5.5 says it must not do.
            going_back.append(dict(envelope, previous_outcome=outcome))

    return _Doorstep(outcomes, reasons, postponed, going_back, within_sla,
                     first_attempt)


def _raise_transfers(doorstep: _Doorstep, facilities: Sequence[dict[str, Any]],
                     *, today: date, regeocode: Regeocode | None
                     ) -> list[TransferRequest]:
    """§5.3.2's first trigger: an address correction moves the facility.

    §6 sends a postponement with sub-reason "incorrect address" back for
    re-geocoding, "which may change facility". The correction itself is not
    this module's to make -- it has no ground truth to correct *to* -- so
    `regeocode` is the caller's, and without one no transfer is raised. A
    simulator that invented corrected addresses would be inventing the very
    thing the trigger is about.

    §9.1 defines the deadline as min(receiving depot's next morning release,
    SLA date), and §7.1 forbids raising one that cannot make it -- so a
    correction whose SLA has already passed produces no transfer, and the
    envelope goes back through §5.5 instead.
    """
    if regeocode is None:
        return []

    # The facility's own release where it states one; the registry's where it
    # does not. A literal here was a second copy of `ROUTE_RELEASE` that no
    # guard could see -- it is a BinOp inside a comprehension, so the
    # duplication check reads neither it nor the `time` it duplicates.
    default_release = _seconds(assumptions.ROUTE_RELEASE)
    releases = {f["id"]: f.get("route_release_time", default_release)
                for f in facilities}
    raised: list[TransferRequest] = []
    for envelope in doorstep.postponed:
        if envelope.get("postponed_reason") != BAD_ADDRESS:
            continue
        corrected = regeocode(envelope)
        if corrected is None or corrected == envelope["facility_id"]:
            continue

        release = datetime.combine(today + timedelta(days=1), time()) + timedelta(
            seconds=int(releases.get(corrected, default_release)))
        sla = envelope.get("sla_date")
        deadline = min(release, datetime.combine(date.fromisoformat(sla), time())
                       ) if sla else release
        if deadline <= datetime.combine(today, time()):
            continue

        raised.append(TransferRequest(
            transfer_id=f"TR-{envelope['package_id']}",
            package_id=envelope["package_id"],
            from_facility_id=envelope["facility_id"],
            to_facility_id=corrected,
            reason=TransferReason.ADDRESS_CORRECTION,
            created_at=datetime.combine(today, time()),
            deadline=deadline,
            weight_g=int(envelope.get("weight_g", 200))))
    return raised


def _collect(facilities: Sequence[dict[str, Any]],
             requests: Sequence[dict[str, Any]],
             inflow: Sequence[dict[str, Any]],
             vans: Sequence[dict[str, Any]], *,
             travel: Callable[[float, float, float, float], int] | None,
             hub_id: str,
             transfers: Sequence[TransferRequest] = (),
             returning: Sequence[dict[str, Any]] = (),
             transit: Transit | None = None) -> _Collection:
    """§5.1 collects, §5.2 times, §5.3 positions. The other half of the day."""
    positioned: dict[str, list[dict[str, Any]]] = {f["id"]: [] for f in facilities}
    if not requests:
        # §5.3.2's circuits still run for transfers alone: a van goes out for
        # them whether or not the hub has anything to send.
        night = linehaul.plan([f for f in facilities if f["id"] != hub_id],
                              [], vans, transfers=transfers,
                              returning=returning, transit=transit,
                              unload_seconds=assumptions.FACILITY_UNLOAD_MIN * 60)
        return _Collection(None, night, positioned)

    if travel is None:
        raise ValueError(
            "a pickup day needs travel times; §5.1 cannot be run on a "
            "guessed speed and this package invents none")

    dispatch = pickups.run(
        requests, vans, next(f for f in facilities if f["id"] == hub_id),
        travel=travel, cut_off=_seconds(assumptions.PROCESSING_CUTOFF))
    ready_at = _ready_times(dispatch, inflow)
    _position(positioned, ready_at, requests, inflow)

    depot_bound = [dict(e, expected_ready_at=ready_at[e["package_id"]])
                   for e in inflow
                   if e["package_id"] in ready_at and e["facility_id"] != hub_id]
    night = linehaul.plan([f for f in facilities if f["id"] != hub_id],
                          depot_bound, vans, transfers=transfers,
                          returning=returning, transit=transit,
                          unload_seconds=assumptions.FACILITY_UNLOAD_MIN * 60)

    rolled = {pid for ids in night.rolled.values() for pid in ids}
    for facility, pool in positioned.items():
        if facility != hub_id:
            positioned[facility] = [e for e in pool
                                    if e["package_id"] not in rolled]
    return _Collection(dispatch, night, positioned)


def _ready_times(dispatch: Any,
                 inflow: Sequence[dict[str, Any]]) -> dict[str, int]:
    """§5.2.5, over the envelopes whose bags actually reached the hub."""
    collected = set(dispatch.collected)
    arrival_of = {bag: dispatch.returned_at.get(van, 0)
                  for van, route in dispatch.routes.items() for bag in route}
    return {r.package_id: r.ready_at for r in processing.schedule(
        [e for e in inflow if e["mailbag_id"] in collected], arrival_of)}


def _position(positioned: dict[str, list[dict[str, Any]]],
              ready_at: Mapping[str, int],
              requests: Sequence[dict[str, Any]],
              inflow: Sequence[dict[str, Any]]) -> None:
    """Put each ready envelope at its facility, carrying the customer's site.

    §5.5's stop is the sender, not the recipient's address, and the hub knows
    it from the upload file that brought the bag -- so it is attached now,
    because by the time an envelope is rejected the request it arrived in is
    long gone.
    """
    site_of = {r["mailbag_id"]: (r["lat"], r["lon"]) for r in requests}
    by_id = {e["package_id"]: e for e in inflow}
    for package_id, when in ready_at.items():
        envelope = by_id[package_id]
        site = site_of.get(envelope["mailbag_id"])
        positioned.setdefault(envelope["facility_id"], []).append(
            dict(envelope, expected_ready_at=when,
                 customer_lat=site[0] if site else envelope["lat"],
                 customer_lon=site[1] if site else envelope["lon"]))


def _return_run(going_back: Sequence[dict[str, Any]], hub_id: str):
    """§5.5, and a refusal to guess where an envelope came from.

    Takes everything going back and returns stops for the ones at the hub
    tonight. `returns.sites` applies §5.5's own gate, so a depot reject handed
    here is not returned and not lost -- it is simply not at the hub yet.
    """
    missing = [e["package_id"] for e in going_back if "customer_lat" not in e]
    if missing:
        raise ValueError(
            f"{len(missing)} envelope(s) going back carry no customer site "
            f"({', '.join(missing[:3])}...); §5.5 returns them to the sender, "
            "which is not the address they were delivered to")
    return returns.sites(going_back, hub_id=hub_id)


def _handover(pools: Mapping[str, list[dict[str, Any]]],
              collection: _Collection, doorstep: _Doorstep,
              unassigned: Sequence[Excluded],
              transfers: Sequence[TransferRequest] = ()
              ) -> dict[str, tuple[dict[str, Any], ...]]:
    """Tomorrow's pools: what was positioned, what was held, what moved depot.

    §5.2.6 ends a transfer at "Ready (at new depot)", so an envelope whose
    transfer was carried starts tomorrow at its destination. One that was
    declined stays where it is -- §7.1: an envelope in transfer is not routable
    until it arrives, and one that never left has not moved either.
    """
    declined = {u.package_id for u in unassigned}
    carried = {t.package_id: t.to_facility_id for t in transfers
               if t.transfer_id in {tid for trip in collection.night.trips
                                    for tid in trip.transfer_ids}}

    def held(facility: str) -> list[dict[str, Any]]:
        moved = []
        for envelope in doorstep.postponed:
            destination = carried.get(envelope["package_id"],
                                      envelope["facility_id"])
            if destination == facility:
                moved.append(dict(envelope, facility_id=destination)
                             if destination != envelope["facility_id"]
                             else envelope)
        return moved

    return {
        facility: (*collection.positioned.get(facility, ()),
                   *held(facility),
                   *[e for e in yesterdays if e["package_id"] in declined])
        for facility, yesterdays in pools.items()}


def _count(state: State, attempt: _Attempted, doorstep: _Doorstep,
           collection: _Collection, requests: Sequence[dict[str, Any]],
           inflow: Sequence[dict[str, Any]],
           per_facility: Mapping[str, int]) -> Tally:
    """The day's raw counts, which §11's ratios are all derived from."""
    dispatch = collection.dispatch
    return Tally(
        ready_pool=state.pool_size,
        dispatched=len(attempt.attempted),
        delivered=doorstep.outcomes.get(DELIVERED, 0),
        delivered_first_attempt=doorstep.first_attempt,
        delivered_within_sla=doorstep.within_sla,
        postponed=doorstep.outcomes.get(POSTPONED, 0),
        rejected=doorstep.outcomes.get(REJECTED, 0),
        defective=doorstep.outcomes.get(DEFECTIVE, 0),
        sla_expired=len(attempt.expired),
        unassigned=len(attempt.unassigned),
        received=len(inflow),
        received_before_cut_off=len(inflow),
        ready_by_cut_off=sum(len(p) for p in collection.positioned.values()),
        pickups=len(dispatch.visits) if dispatch else 0,
        pickup_wait_seconds=_waiting(dispatch, requests),
        bikes_deployed=sum(per_facility.values()))


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
    regeocode: Regeocode | None = None,
    transit: Transit | None = None,
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
        regeocode: §5.3.2's first trigger -- given a postponed envelope whose
            address was wrong, the facility it really belongs to. Without one
            no transfer is raised, because the correction is not this module's
            to invent.
        transit: seconds between two facilities, for §5.3.2's inter-depot
            legs. §3.1 gives hub transit only, so without this every transfer
            is declined with that as its reason.
        seed: fixes the outcome sampling, so a day replays identically.

    Returns:
        The day's report and the state it hands tomorrow.
    """
    rates = rates or Rates()
    rng = random.Random(seed + state.day.toordinal())

    # §4.2: today's allocation, and what it cost to change yesterday's.
    pools = {f["id"]: list(state.pools.get(f["id"], ())) for f in facilities}
    targets = (dict(allocation) if allocation is not None
               else allocate({f: len(p) for f, p in pools.items()}, len(bikes))
               if bikes else dict.fromkeys(pools, 0))
    fleet = place(targets, bikes, previous=state.placement)
    per_facility = {f: len(v) for f, v in fleet.by_facility().items()}

    attempt = _attempt(pools, per_facility, today=state.day, deliver=deliver)
    doorstep = _doorstep(attempt, rates=rates, rng=rng, today=state.day,
                         hub_id=hub_id)
    transfers = _raise_transfers(doorstep, facilities, today=state.day,
                                 regeocode=regeocode)
    # §5.5: yesterday's depot rejects ride tonight's circuits home. The
    # line-haul plan is built before the return run is solved, which is the
    # order the operation runs in and the reason this works in one day.
    collection = _collect(facilities, requests, inflow, vans, travel=travel,
                          hub_id=hub_id, transfers=transfers,
                          returning=state.returns_queue, transit=transit)
    rode_home = set(collection.night.returned)
    arrived = [dict(e, facility_id=hub_id) for e in state.returns_queue
               if e["package_id"] in rode_home]
    stops = _return_run([*doorstep.going_back, *arrived], hub_id)
    tomorrow = _handover(pools, collection, doorstep, attempt.unassigned,
                         transfers)

    dispatch, night = collection.dispatch, collection.night
    tally = _count(state, attempt, doorstep, collection, requests, inflow,
                   per_facility)

    # §7.1's day-spanning bullets. Computed here rather than by the caller
    # because this is where the night's plan, the vans' return times and
    # the day's transfers are all in one scope -- `DayReport` reduces each
    # of them to a count on its way out, and a check cannot be run on a
    # count. The caller reports what this decides; it does not decide it.
    violations = check_day_constraints(Day(
        today=state.day,
        linehaul=night,
        transfers=transfers,
        van_back_at=dispatch.returned_at if dispatch else {},
        unload_seconds=assumptions.FACILITY_UNLOAD_MIN * 60))

    report = DayReport(
        day=state.day,
        tally=tally,
        metrics=measure(tally),
        checks=_checks(state, per_facility, inflow, dispatch, night, vans),
        allocation=fleet,
        outcomes=doorstep.outcomes,
        postponed_reasons=doorstep.reasons,
        unassigned=tuple(attempt.unassigned),
        positioned={f: len(p) for f, p in collection.positioned.items()},
        rolled=dict(night.reasons),
        return_stops=len(stops),
        carried_into_tomorrow=sum(len(p) for p in tomorrow.values()),
        transfers_raised=len(transfers),
        transfers_carried=sum(len(trip.transfer_ids) for trip in night.trips),
        transfers_declined={d.transfer_id: d.reason
                            for d in night.declined},
        violations=tuple(violations))

    return report, State(
        day=state.day + timedelta(days=1),
        pools=tomorrow,
        # §5.5: refused at a depot today, home on a van tomorrow. Plus
        # anything queued yesterday that found no circuit tonight.
        returns_queue=tuple(
            [e for e in doorstep.going_back
             if e.get("facility_id") != hub_id]
            + [e for e in state.returns_queue
               if e["package_id"] not in rode_home]),
        placement={a.vehicle_id: a.facility_id for a in fleet.allocations})


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


def _hub_hours() -> float:
    """§5.6: the hub works from shift start to the processing cut-off."""
    return (_seconds(assumptions.PROCESSING_CUTOFF)
            - _seconds(assumptions.SHIFT_START)) / HOUR


def _shift_hours(vehicle: Mapping[str, Any]) -> float:
    """A vehicle's own §9.1 shift, rather than a number invented here."""
    start, end = vehicle.get("shift_start"), vehicle.get("shift_end")
    if start is None or end is None:
        return _hub_hours()
    return (int(end) - int(start)) / HOUR


def _checks(state: State, per_facility: Mapping[str, int],
            inflow: Sequence[Mapping[str, Any]], dispatch: Any,
            night: linehaul.LinehaulPlan,
            vans: Sequence[Mapping[str, Any]]) -> Checks:
    """§8.3's three, from what the day actually did.

    Van-hours come from the vans' own §9.1 shifts. They were two invented
    constants until a linter pointed at them, which is exactly the hard-coded
    `[TBD]` the assumptions policy asks nobody to write: §4.3 makes van-hours
    the contested resource and §8.3 calls them the likely bottleneck, so a
    made-up shift length would have decided the check it feeds.
    """
    # Hours worked, not the clock they came back at. `returned_at` is a second
    # of the day, so summing it counted a van that finished at 18:00 as having
    # worked eighteen hours -- which made the day's van demand look far larger
    # than it was, in the one check §8.3 says is most likely to bind.
    started = {van["vehicle_id"]: int(van.get("shift_start", 0)) for van in vans}
    pickup_hours = 0.0
    if dispatch is not None:
        pickup_hours = sum(
            max(back - started.get(van_id, 0), 0)
            for van_id, back in dispatch.returned_at.items()) / HOUR
    linehaul_hours = sum((trip.returns - trip.departure)
                         for trip in night.trips) / HOUR

    assembly = sum(1 for e in inflow if e.get("package_type") == "assembly")
    return check(
        pool=state.pool_size,
        motorbikes=sum(per_facility.values()),
        inflow=len(inflow),
        van_hours_available=sum(_shift_hours(van) for van in vans),
        pickup_hours=pickup_hours,
        linehaul_hours=linehaul_hours,
        processing_hours=_hub_hours(),
        assembly_share=(assembly / len(inflow)) if inflow else 0.0)
