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

from ddn import assumptions, lastmile, linehaul, pickups, processing, returns
from ddn.allocation import FleetPlan, allocate, capacity_for, place
from ddn.contract import Excluded
from ddn.lastmile import select
from ddn.linehaul import Transit, circuit
from ddn.model import (
    Status,
    TransferReason,
    TransferRequest,
    outcomes,
)
from ddn.model.outcomes import postponed as record_postponement
from ddn.simulation.capacity import Checks, check
from ddn.simulation.metrics import Metrics, Tally, measure
from ddn.solver_adapter import Day, check_day_constraints

HOUR = 3600

#: §7.1: "an envelope in transfer is not routable until it arrives at the
#: destination depot". §6's own table says where that starts -- "a transfer
#: request is raised (§5.3.2) and the envelope enters *In transfer* until it
#: arrives" -- so the raise, not the loading, is what takes it off the round.
#: `postcheck._across_the_day` refuses any *served* order whose status is not
#: `Ready`; this is the same bullet one stage earlier, where the simulator
#: decides what to offer rather than where the solver's answer is checked.
AWAITING_TRANSFER = frozenset({Status.TRANSFER_REQUESTED, Status.IN_TRANSFER})

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
    #: §5.3.2: "Requests arising after departures wait for the next day's
    #: plan." A transfer no circuit could carry was counted in
    #: `DayReport.transfers_declined` and then dropped, so the correction had
    #: to be rediscovered by the envelope being postponed again for the same
    #: reason. Only the two reasons tomorrow can answer are held --
    #: `MISSES_DEADLINE` cannot, because §9.1 fixes the deadline at raising.
    transfers: tuple[TransferRequest, ...] = ()
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
    #: §5.1.6: envelopes on a bag no van could collect and return before
    #: the processing cut-off. The largest loss channel in a road-routed
    #: day, and the report had no line for it -- they left silently.
    uncollected: int = 0
    #: §5.3: reached the hub, missed every van. `rolled` names the depot
    #: and the reason; this counts the envelopes.
    rolled_envelopes: int = 0
    #: §5.3.2, as one ledger. `transfers_offered` is every request tonight's
    #: plan was shown -- yesterday's deferred plus today's raises -- and is
    #: what the other rows account for; `transfers_raised` is today's new ones
    #: alone. They were the same field until this task, under the second name
    #: and with the first meaning, so every figure derived from "raised"
    #: silently included a queue that had been waiting for days.
    transfers_offered: int = 0
    transfers_raised: int = 0
    transfers_carried: int = 0
    transfers_declined: dict[str, str] = field(default_factory=dict)
    #: What became of the declines, read off the day rather than subtracted
    #: from it -- `LinehaulPlan.returned`'s rule, that a count kept alongside
    #: can disagree with what the plan says. `deferred` is `_still_waiting`,
    #: `returned` is §5.3.2's "otherwise" as `_refused_transfers` sends it, and
    #: `spent` is neither: §6.1's clock reached the envelope while its request
    #: waited for a van, so there is nothing left to move and nothing to send
    #: back -- §5.5 already has it. The three partition the declines.
    transfers_deferred: int = 0
    transfers_returned: int = 0
    transfers_spent: int = 0
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
    #: §7.1's envelopes held off today's round for a transfer already raised.
    #: A fourth bucket rather than a reason on `unassigned`, because that list
    #: is §8's "the bikes could not take it" and this is "§7.1 says not from
    #: here" -- and §10 pins the count of the first one.
    waiting: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Doorstep:
    """§6: what came of the attempts."""

    outcomes: dict[str, int]
    reasons: dict[str, int]
    postponed: list[dict[str, Any]]
    going_back: list[dict[str, Any]]
    within_sla: int
    first_attempt: int
    #: §6.1 postponements with no day left to be retried on. They join
    #: `going_back` and they are an SLA expiry, so §11's expiry rate counts
    #: them — `attempt.expired` alone is only the dispatch-time sweep, and
    #: reporting zero while envelopes go back for expiry is a metric that
    #: reads as a clean day.
    spent: int = 0


@dataclass(frozen=True, slots=True)
class _Collection:
    """§5.1 to §5.3: the day's inflow, positioned for tomorrow."""

    dispatch: Any
    night: linehaul.LinehaulPlan
    positioned: dict[str, list[dict[str, Any]]]
    #: §8.3's marginal transfer hours, in seconds. Computed here because this
    #: is the only scope holding the night's inputs *before* `strip_rolled`
    #: consumes them, and the counterfactual has to be planned from the same
    #: ones. `DayReport` reduces the night to counts on its way out.
    transfer_van_seconds: int = 0


def _attempt(pools: Mapping[str, list[dict[str, Any]]],
             per_facility: Mapping[str, int], *, today: date,
             deliver: Callable[..., tuple[list, list]]) -> _Attempted:
    """Each facility's pool, cut to capacity and attempted.

    §6.1 comes first: an envelope past its SLA date is not dispatched at all,
    it goes back. Then §7.1's `AWAITING_TRANSFER`, because an envelope with a
    transfer raised against it is not this facility's to deliver -- its own
    corrected address says so. What remains is §8's decision --
    `lastmile.select` trims to what the bikes can carry -- and only then is
    anything attempted.

    **The order of those two is §6.1's and not a preference.** An envelope
    waiting for a transfer whose SLA passes goes back like any other; holding
    it for a transfer that can no longer help would be the "queue that never
    empties" one bullet down, built out of the bullet above it.

    Offering them, as this used to, cost two things at once. A rider was sent
    each morning to the address the correction had already ruled wrong, so §6
    drew a fresh postponement and `_raise_transfers` raised a *second* request
    with the same `TR-{package_id}` id -- and when the draw came up delivered
    or expired instead, the request outlived its envelope and waited for ever
    on something no pool held. Five simulated days of it left eight requests
    circling with nothing to move.
    """
    attempted: list[dict[str, Any]] = []
    unassigned: list[Excluded] = []
    expired: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []

    for facility, pool in pools.items():
        live = [e for e in pool if not lastmile.expired(e, today)]
        expired.extend(e for e in pool if lastmile.expired(e, today))
        held = [e for e in live if e.get("status") in AWAITING_TRANSFER]
        waiting.extend(held)
        live = [e for e in live if e.get("status") not in AWAITING_TRANSFER]

        bikes = per_facility.get(facility, 0)
        offered, declined = select(live, capacity=capacity_for(bikes),
                                   today=today)
        unassigned.extend(declined)
        served, refused = deliver(offered, facility, bikes)
        attempted.extend(served)
        unassigned.extend(Excluded(e["package_id"], "time") for e in refused)

    return _Attempted(attempted, unassigned, expired, waiting)


def _doorstep(attempt: _Attempted, *, rates: Rates, rng: random.Random,
              today: date, hub_id: str) -> _Doorstep:
    """§6's outcomes, drawn per envelope in a fixed order so a day replays."""
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    spent = 0
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
            if not lastmile.expired(envelope, today):
                within_sla += 1
            if int(envelope.get("attempt_number", 0)) == 0:
                first_attempt += 1
        elif outcome == POSTPONED:
            reason = sub_reason(rng)
            reasons[reason] = reasons.get(reason, 0) + 1
            held = record_postponement(envelope, reason)
            if lastmile.retryable(envelope, today):
                postponed.append(held)
            else:
                # §6.1: retried *until* its SLA date, and after that returned.
                # A postponement on the SLA date itself has no next attempt.
                # This used to hold it anyway and let the following morning's
                # expiry sweep find it, so the envelope spent a day in a pool
                # it could not be dispatched from and reached §5.5 a day late.
                going_back.append(dict(held, sla_expired=True))
                spent += 1
        else:
            # §5.5: the envelope is where it was refused, which for a depot
            # reject is the depot. Stamping the hub here made every rejection
            # hub-resident the instant it happened, so it joined *tonight's*
            # return run -- the one thing §5.5 says it must not do.
            going_back.append(dict(envelope, previous_outcome=outcome))

    return _Doorstep(outcomes, reasons, postponed, going_back, within_sla,
                     first_attempt, spent)


def _raise_transfers(doorstep: _Doorstep, facilities: Sequence[dict[str, Any]],
                     *, today: date, regeocode: Regeocode | None
                     ) -> tuple[list[TransferRequest], list[dict[str, Any]],
                                list[dict[str, Any]]]:
    """§5.3.2's first trigger: an address correction moves the facility.

    §6 sends a postponement with sub-reason "incorrect address" back for
    re-geocoding, "which may change facility". The correction itself is not
    this module's to make -- it has no ground truth to correct *to* -- so
    `regeocode` is the caller's, and without one no transfer is raised. A
    simulator that invented corrected addresses would be inventing the very
    thing the trigger is about.

    §9.1 defines the deadline as min(receiving depot's next morning release,
    SLA date), and §7.1 forbids raising one that cannot make it -- so a
    correction whose SLA has already passed produces no transfer.

    **And the envelope then goes back, which is the half this used to skip.**
    §5.3.2: "the envelope is transferred if it can reach the correct depot
    before its SLA date; otherwise it is returned to the customer via the
    hub." This `continue`d instead, leaving the envelope in tomorrow's pool at
    a depot its own corrected address says is the wrong one, until §6.1's
    clock eventually swept it. So the second return value is those envelopes,
    for the caller to hand to §5.5.

    They carry `sla_expired`, which is the honest flag available rather than
    an exact one: the envelope cannot reach a facility that could deliver it
    in time, which is an SLA failure in substance. §6 has no "transfer
    refused" outcome and inventing one here would be inventing a §9.2
    vocabulary entry; `docs/spec-proposals/v0.16-depot-capacity.md` carries the
    request.

    **The third return value is §5.2.6's branch, taken once.** Postponed
    reaches either Ready or Transfer requested, and only this function knows
    which -- `_doorstep` records the postponement and stops. So it returns the
    postponed envelopes as they now stand: `Transfer requested` for the ones a
    request was raised against, `Ready` for the rest, and **absent** for the
    ones going back. That last is why it is a list and not a stamp: an
    envelope in `going_back` must leave the pool, and the first version of
    this left it in both.
    """
    if regeocode is None:
        return [], [], [outcomes.for_retry(e) for e in doorstep.postponed]

    # The facility's own release where it states one; the registry's where it
    # does not. A literal here was a second copy of `ROUTE_RELEASE` that no
    # guard could see -- it is a BinOp inside a comprehension, so the
    # duplication check reads neither it nor the `time` it duplicates.
    default_release = _seconds(assumptions.ROUTE_RELEASE)
    releases = {f["id"]: f.get("route_release_time", default_release)
                for f in facilities}
    raised: list[TransferRequest] = []
    refused: list[dict[str, Any]] = []
    staying: list[dict[str, Any]] = []
    for envelope in doorstep.postponed:
        if envelope.get("postponed_reason") != BAD_ADDRESS:
            staying.append(outcomes.for_retry(envelope))
            continue
        corrected = regeocode(envelope)
        if corrected is None or corrected == envelope["facility_id"]:
            staying.append(outcomes.for_retry(envelope))
            continue

        release = datetime.combine(today + timedelta(days=1), time()) + timedelta(
            seconds=int(releases.get(corrected, default_release)))
        # §6.1 makes "SLA date = today" a must-deliver-*today* constraint, so
        # the date names a day the envelope may still be delivered on and not a
        # moment it expires at: the bound is the **end** of it.
        # `tests/fixtures/peak_day_transfers.py:187` has always read it that
        # way; this line read the start, and the two disagreed silently because
        # every candidate on the peak day gets the same verdict either way.
        sla = envelope.get("sla_date")
        expiry = (datetime.combine(date.fromisoformat(sla), time())
                  + timedelta(days=1)) if sla else None
        # §5.3.2 is a pair: "transferred if it can reach the correct depot
        # before its SLA date; otherwise it is returned to the customer via the
        # hub". Reaching happens at `release`, so that is what the SLA is asked
        # about. The old test -- is the deadline behind *today* -- gave the same
        # answer only while the bound was the start of the date; with the end of
        # it, an envelope due today has a deadline of tonight's midnight, which
        # is ahead of today and still behind any next-morning arrival.
        # `>=` and not `>` so a depot releasing at 00:00 refuses rather than
        # arriving at the instant the SLA date ends (§3.1's column is [TBD];
        # `ddn/linehaul/circuit.py:130` records the same degeneracy).
        if expiry is not None and release >= expiry:
            refused.append(dict(envelope, sla_expired=True))
            continue
        # §9.1's formula as stated. It now always resolves to the release --
        # see the guard above -- which is a consequence of the end-of-date
        # bound worth knowing about, not an invariant to lean on.
        deadline = min(release, expiry) if expiry else release
        staying.append(outcomes.transfer_requested(envelope))

        raised.append(TransferRequest(
            transfer_id=f"TR-{envelope['package_id']}",
            package_id=envelope["package_id"],
            from_facility_id=envelope["facility_id"],
            to_facility_id=corrected,
            reason=TransferReason.ADDRESS_CORRECTION,
            created_at=datetime.combine(today, time()),
            deadline=deadline,
            weight_g=int(envelope.get("weight_g", 200)),
            # §9.1 v0.16: the envelope's own §8.1 score rides with it, so
            # §5.3.2 can rank the transfer against a hub-origin load.
            priority=float(envelope.get("priority", 0) or 0)))
    return raised, refused, staying


#: §5.3.2's declines that tomorrow can still answer. `MISSES_DEADLINE` is not
#: among them: §9.1 fixes a transfer's deadline when it is raised, so one that
#: cannot arrive tonight cannot arrive tomorrow either, and queueing it would
#: build a queue that never empties. Those envelopes go back by
#: `_raise_transfers`' second return value instead -- the same sentence of
#: §5.3.2 answering both halves.
RETRIABLE_DECLINES = frozenset({circuit.NO_VAN_LEG, circuit.OVER_CAPACITY})


def _still_waiting(transfers: Sequence[TransferRequest], night: Any,
                   tomorrow: Mapping[str, tuple[dict[str, Any], ...]]
                   ) -> tuple[TransferRequest, ...]:
    """§5.3.2: "Requests arising after departures wait for the next day's plan."

    Read off the plan rather than accumulated beside it, for the reason
    `LinehaulPlan.returned` gives: a count kept alongside can disagree with
    what the plan says it carried.

    **And only while tomorrow still holds the envelope.** A request is a
    request to move something; once the something has left the operation there
    is nothing for tomorrow's plan to do with it, and keeping it builds the
    same never-emptying queue that `RETRIABLE_DECLINES` excludes
    `MISSES_DEADLINE` to avoid. `_attempt` now holds these envelopes off the
    round, which closes the everyday way one used to disappear -- delivered
    from the depot its own corrected address ruled wrong. What is left is
    §6.1's clock: an envelope can wait for a van until its SLA passes, and
    then it goes back whatever the transfer wanted. That is a real outcome and
    the request is simply spent, so it is dropped rather than carried or
    counted as refused -- §5.5 already has the envelope.
    """
    retry = {d.transfer_id for d in night.declined
             if d.reason in RETRIABLE_DECLINES}
    holds = {e["package_id"] for pool in tomorrow.values() for e in pool}
    return tuple(t for t in transfers
                 if t.transfer_id in retry and t.package_id in holds)


def _marginal_van_seconds(night: linehaul.LinehaulPlan,
                          replan: Callable[..., linehaul.LinehaulPlan],
                          bound: Sequence[dict[str, Any]],
                          transfers: Sequence[TransferRequest]) -> int:
    """§8.3's "transfers **add to it**", taken as the marginal question it is.

    §8.3 compares van-hours available against pickup hours plus line-haul
    hours "including inter-depot legs for transfers ... transfers add to it".
    *Add to* is a difference, so this is the night as planned less the same
    night planned with nothing to transfer -- same depots, same hub-origin
    load, same returns queue, same vans.

    It reads **zero** on a night where every transfer rode a leg the circuits
    were flying anyway, which is the honest answer to "what did transfers cost"
    and the one an apportioned reading gets wrong. The tempting apportionment
    -- bill a leg to transfers when its `hub_loads` is empty -- is wrong twice
    over: `plan` attaches a trip's whole hub manifest to its first leg only, so
    later legs carrying hub-origin envelopes report `hub_loads == {}`, and
    `return_ids` accumulate homewards so a returns-carrying leg reads the same.

    No counterfactual is planned when there is nothing to take out of it: the
    marginal cost of no transfers is zero by arithmetic, not by measurement.
    """
    if not transfers:
        return 0
    return night.van_seconds - replan(bound, ()).van_seconds


def _spent(transfers: Sequence[TransferRequest], night: Any,
           tomorrow: Mapping[str, tuple[dict[str, Any], ...]],
           refused: Sequence[Mapping[str, Any]]
           ) -> tuple[TransferRequest, ...]:
    """The declines that are neither tomorrow's nor §5.3.2's "otherwise".

    `_still_waiting` and `_refused_transfers` were built to partition tonight's
    declines between them, and since §7.1 began holding a transferring envelope
    off the round they no longer quite do. Both read tomorrow's pools, and an
    envelope can leave the operation while its request waits for a van: §6.1's
    clock runs on it like any other, and then §5.5 has the envelope and the
    request has nothing left to move. It is not deferred -- there is no subject
    -- and it is not returned, because §5.5 was handed the envelope by the
    expiry sweep and not by this rule; counting it as returned would credit
    §5.3.2 with a channel §6.1 used.

    Read off the day rather than subtracted from the other two, so that
    `declined == deferred + returned + spent` stays a check on three
    independently-derived numbers instead of a definition that cannot fail.
    The reading is the absent subject: tomorrow does not hold the envelope and
    §5.5 was not handed it by this rule. Defining it as "the declines the
    other two did not claim" would make the equality true however wrong they
    were -- and would silently absorb a fourth outcome, if one ever arose,
    into §6.1's name.
    """
    declined = {d.transfer_id for d in night.declined}
    holds = {e["package_id"] for pool in tomorrow.values() for e in pool}
    home = {e["package_id"] for e in refused}
    return tuple(t for t in transfers
                 if t.transfer_id in declined and t.package_id not in holds
                 and t.package_id not in home)


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
    positioned = processing.at_facilities(facilities)
    depots = linehaul.depots(facilities, hub_id=hub_id)

    def night_over(bound: Sequence[dict[str, Any]],
                   carrying: Sequence[TransferRequest]) -> linehaul.LinehaulPlan:
        return linehaul.plan(depots, bound, vans, transfers=carrying,
                             returning=returning, transit=transit,
                             unload_seconds=assumptions.FACILITY_UNLOAD_MIN * 60)

    if not requests:
        # §5.3.2's circuits still run for transfers alone: a van goes out for
        # them whether or not the hub has anything to send.
        night = night_over([], transfers)
        return _Collection(None, night, positioned,
                           _marginal_van_seconds(night, night_over, [],
                                                 transfers))

    if travel is None:
        raise ValueError(
            "a pickup day needs travel times; §5.1 cannot be run on a "
            "guessed speed and this package invents none")

    dispatch = pickups.run(
        requests, vans, next(f for f in facilities if f["id"] == hub_id),
        travel=travel, cut_off=_seconds(assumptions.PROCESSING_CUTOFF))
    ready_at = processing.ready_times(dispatch, inflow)
    processing.position(positioned, ready_at, requests, inflow)

    # Before `strip_rolled`, and reused: the counterfactual has to be planned
    # from the same list this one was, or the difference is two plans of two
    # different nights.
    bound = linehaul.depot_bound(positioned, hub_id=hub_id)
    night = night_over(bound, transfers)
    marginal = _marginal_van_seconds(night, night_over, bound, transfers)

    positioned = linehaul.strip_rolled(positioned, night, hub_id=hub_id)
    return _Collection(dispatch, night, positioned, marginal)


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
              collection: _Collection, staying: Sequence[dict[str, Any]],
              unassigned: Sequence[Excluded],
              transfers: Sequence[TransferRequest] = (),
              waiting: Sequence[dict[str, Any]] = ()
              ) -> dict[str, tuple[dict[str, Any], ...]]:
    """Tomorrow's pools: what was positioned, what was held, what moved depot.

    §5.2.6 ends a transfer at "Ready (at new depot)", so an envelope whose
    transfer was carried starts tomorrow at its destination. One that was
    declined stays where it is -- §7.1: an envelope in transfer is not routable
    until it arrives, and one that never left has not moved either.

    `staying` is the postponed pool as `_raise_transfers` left it: each
    envelope on whichever §5.2.6 edge its own case took, and the ones going
    back to the customer **absent**. It used to take `doorstep.postponed`,
    which still held the refused ones -- so an envelope could be in tonight's
    return load and in tomorrow's pool at once.

    `waiting` is §7.1's other set: envelopes `_attempt` did not offer because a
    transfer was already raised against them. They take the same route as
    `staying` and not a separate clause, because the question tomorrow asks of
    both is the same one -- did tonight's circuit carry your transfer? A
    request declined on the night it was raised and carried on the next is the
    case that separating them would get wrong, and it is the common one: that
    is what a retriable decline *is*.
    """
    declined = {u.package_id for u in unassigned}
    carried = {t.package_id: t.to_facility_id for t in transfers
               if t.transfer_id in {tid for trip in collection.night.trips
                                    for tid in trip.transfer_ids}}

    def held(facility: str) -> list[dict[str, Any]]:
        moved = []
        for envelope in [*staying, *waiting]:
            destination = carried.get(envelope["package_id"],
                                      envelope["facility_id"])
            if destination != facility:
                continue
            if destination != envelope["facility_id"]:
                # §5.2.6 ends a transfer at "Ready (at new depot)", and §7.1
                # makes it routable the moment it arrives and not before -- so
                # the leg and the arrival are both written and the facility
                # moves with the status, never ahead of it.
                moved.append(outcomes.arrived(
                    outcomes.in_transfer(envelope), destination))
            else:
                moved.append(envelope)
        return moved

    return {
        facility: (*collection.positioned.get(facility, ()),
                   *held(facility),
                   *[e for e in yesterdays if e["package_id"] in declined])
        for facility, yesterdays in pools.items()}


def _refused_transfers(
        tomorrow: Mapping[str, tuple[dict[str, Any], ...]],
        transfers: Sequence[TransferRequest], night: Any
) -> tuple[dict[str, tuple[dict[str, Any], ...]], list[dict[str, Any]]]:
    """§5.3.2: "otherwise it is returned to the customer via the hub".

    `_still_waiting` keeps the declines tomorrow can answer; this is the other
    half of the same sentence, and the two partition tonight's declines so that
    no request can evaporate between them. The rule is the complement of
    `RETRIABLE_DECLINES` rather than a list of final reasons, because a reason
    in neither set is a request that leaves no trace -- its envelope stranded
    at a depot it does not belong to, waiting for a correction nobody would
    raise a second time.

    Two reasons reach here. `MISSES_DEADLINE` is final because §9.1 fixes the
    deadline when the transfer is raised, so no later night beats one tonight
    missed. `circuit.choose`'s refusal when no inter-depot transit is supplied
    is final for a duller reason: tomorrow has the same table, which is none --
    and since `run_day`'s `transit` defaults to `None`, that is the decline an
    operation without §3.1's table meets every single night.

    Reads tomorrow's pools rather than today's postponed envelopes, because
    that is the one place the day still holds one. The case that forces it is a
    request carried over from yesterday: its envelope was not postponed today,
    it sat in `pools` and may have been attempted since. Anything that left the
    operation today is not in tomorrow's pools either, so nothing is sent back
    twice and nothing is both in tonight's return load and in tomorrow's pool
    -- the disjointness `_handover` records having lost once already.

    They carry `sla_expired` and no new status. The flag is `_raise_transfers`'
    argument at raising, unchanged by the decline coming later: §6 has no
    "transfer refused" outcome and inventing one is a change to §9.2 first, for
    which `docs/spec-proposals/v0.16-depot-capacity.md` carries the request.
    §5.2.6 does draw `Transfer requested -> Return run`, but every other
    envelope in `going_back` travels on a flag alone, including the rejects and
    the expiries that are unambiguously bound for the return run; stamping the
    status here and nowhere else would make this one look special.

    Returns:
        Tomorrow's pools without those envelopes, and the envelopes.
    """
    refused = {d.transfer_id for d in night.declined
               if d.reason not in RETRIABLE_DECLINES}
    leaving = {t.package_id for t in transfers if t.transfer_id in refused}
    return ({facility: tuple(e for e in pool
                             if e["package_id"] not in leaving)
             for facility, pool in tomorrow.items()},
            [outcomes.expired(e) for pool in tomorrow.values() for e in pool
             if e["package_id"] in leaving])


def _count(state: State, attempt: _Attempted, doorstep: _Doorstep,
           collection: _Collection, requests: Sequence[dict[str, Any]],
           inflow: Sequence[dict[str, Any]],
           per_facility: Mapping[str, int], checks: Checks) -> Tally:
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
        sla_expired=len(attempt.expired) + doorstep.spent,
        unassigned=len(attempt.unassigned),
        received=len(inflow),
        received_before_cut_off=len(inflow),
        ready_by_cut_off=sum(len(p) for p in collection.positioned.values()),
        pickups=len(dispatch.visits) if dispatch else 0,
        pickup_wait_seconds=_waiting(dispatch, requests),
        bikes_deployed=sum(per_facility.values()),
        # §8.3's own used-side -- `Check.required`, which for the van check is
        # `pickup_hours + linehaul_hours` -- so the share moves when the day's
        # work moves and not when a shift length does. `Check.load` is the
        # ratio against hours *available* and is a different question. The
        # numerator is a subset of this: `linehaul_hours` is the same night's
        # `LinehaulPlan.van_seconds`, inter-depot legs included, and the
        # transfer share is a difference of two of those, not a second charge.
        van_seconds=round(checks.vans.required * HOUR),
        transfer_van_seconds=collection.transfer_van_seconds)


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
    raised, unreachable, staying = _raise_transfers(doorstep, facilities,
                                                    today=state.day,
                                                    regeocode=regeocode)
    # §5.3.2: yesterday's undelivered requests are tonight's, ahead of the
    # ones raised today, because they have already waited a day.
    transfers = [*state.transfers, *raised]
    # §5.5: yesterday's depot rejects ride tonight's circuits home. The
    # line-haul plan is built before the return run is solved, which is the
    # order the operation runs in and the reason this works in one day.
    collection = _collect(facilities, requests, inflow, vans, travel=travel,
                          hub_id=hub_id, transfers=transfers,
                          returning=state.returns_queue, transit=transit)
    rode_home = set(collection.night.returned)
    arrived = [dict(e, facility_id=hub_id) for e in state.returns_queue
               if e["package_id"] in rode_home]
    # The hand-over is built before the return run, not after it: §5.3.2's
    # "otherwise" needs tomorrow's pools, because that is the one place the day
    # still holds the envelope of a transfer no circuit will ever carry.
    tomorrow = _handover(pools, collection, staying, attempt.unassigned,
                         transfers, attempt.waiting)
    tomorrow, refused = _refused_transfers(tomorrow, transfers,
                                           collection.night)
    # §5.3.2's three answers to a declined request, each read off the day:
    # tomorrow's queue, tonight's return run, and neither. See `_spent`.
    deferred = _still_waiting(transfers, collection.night, tomorrow)
    spent = _spent(transfers, collection.night, tomorrow, refused)
    going_back = [*doorstep.going_back, *unreachable, *refused]
    stops = _return_run([*going_back, *arrived], hub_id)

    dispatch, night = collection.dispatch, collection.night
    checks = _checks(state, per_facility, inflow, dispatch, night, vans)
    tally = _count(state, attempt, doorstep, collection, requests, inflow,
                   per_facility, checks)

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
        checks=checks,
        allocation=fleet,
        outcomes=doorstep.outcomes,
        postponed_reasons=doorstep.reasons,
        unassigned=tuple(attempt.unassigned),
        positioned={f: len(p) for f, p in collection.positioned.items()},
        rolled=dict(night.reasons),
        return_stops=len(stops),
        carried_into_tomorrow=sum(len(p) for p in tomorrow.values()),
        uncollected=pickups.uncollected(dispatch, inflow),
        rolled_envelopes=sum(len(ids) for ids in night.rolled.values()),
        transfers_offered=len(transfers),
        transfers_raised=len(raised),
        transfers_carried=sum(len(trip.transfer_ids) for trip in night.trips),
        transfers_declined={d.transfer_id: d.reason
                            for d in night.declined},
        transfers_deferred=len(deferred),
        transfers_returned=len(refused),
        transfers_spent=len(spent),
        violations=tuple(violations))

    return report, State(
        day=state.day + timedelta(days=1),
        pools=tomorrow,
        # §5.5: refused at a depot today, home on a van tomorrow. Plus
        # anything queued yesterday that found no circuit tonight.
        returns_queue=tuple(
            [e for e in going_back
             if e.get("facility_id") != hub_id]
            + [e for e in state.returns_queue
               if e["package_id"] not in rode_home]),
        transfers=deferred,
        placement={a.vehicle_id: a.facility_id for a in fleet.allocations})


def run_days(days: int, state: State, **kwargs: Any) -> list[DayReport]:
    """Chain `days` days, so §8.3's gap accumulates instead of averaging away."""
    reports = []
    for _ in range(days):
        report, state = run_day(state, **kwargs)
        reports.append(report)
    return reports


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
    linehaul_hours = night.van_seconds / HOUR

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
