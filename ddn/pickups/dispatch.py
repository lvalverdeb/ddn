"""§5.1.5 — running the pickup day, and §5.1.7's exceptions.

`admission` answers *which van may take this bag*. This runs the day around
that answer: requests arrive through the morning, vans fill, the plan is redone
on a cadence, and what has already been visited cannot be redone.

**What is frozen is what has happened.** §5.1.5: "re-optimisation runs at a
fixed cadence (e.g. every 30 minutes); stops already visited are frozen." A
stop whose van reached it before the current tick is history -- it stays on that
van's route whatever a later cycle would prefer.

**Assignment here, sequencing elsewhere.** §5.1.4 makes this "only a question of
which van ... decided by remaining bag capacity, remaining weight capacity and
least additional route cost", and `admission` says the same from its side: the
order within a van is `vrp.quote.quote_insertion`'s to decide against a live
route, which `docs/solver-capabilities.md` confirms the platform supports. So a
bag is appended at the end of its van's run and the clock advances by the leg
it adds -- enough to make the cut-off and the line-haul release bind, and no
more. A cheaper order is an improvement on this, not a contradiction of it.

**No speed is invented here.** `travel` is required rather than defaulted: how
long a leg takes is the gateway's to answer from road data, and a module that
guessed would put a made-up number underneath every release-time decision.

Standing pickups for regular customers (§5.1.5, Open Question 3) are not
modelled: nobody has said whether they exist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ddn import assumptions
from ddn.pickups.admission import BAGS, WEIGHT, can_take, load

Travel = Callable[[float, float, float, float], int]


class Incident(StrEnum):
    """§5.1.7's table, as the five things that go wrong at a site."""

    BAG_NOT_READY = "bag not ready"
    SEAL_BROKEN = "seal broken"
    MORE_BAGS = "more bags than expected"
    SITE_CLOSED = "site closed"
    CANCELLED = "cancelled after dispatch"


@dataclass(frozen=True, slots=True)
class Visit:
    """One van at one site, and what came of it."""

    van_id: str
    mailbag_id: str
    at: int
    collected: bool
    incident: Incident | None = None
    #: §5.1.7: "Collect but flag; hub performs full reconciliation."
    flagged: bool = False
    #: §5.1.7: "re-request" -- tomorrow, or later today.
    re_request: bool = False


@dataclass(frozen=True)
class Dispatch:
    """A pickup day: what was collected, by whom, and what was not."""

    visits: tuple[Visit, ...] = ()
    routes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    returned_at: Mapping[str, int] = field(default_factory=dict)
    #: Bags no van could take before the cut-off or its line-haul release.
    unplaced: tuple[str, ...] = ()
    #: §5.1.7: bags to ask for again.
    re_requests: tuple[str, ...] = ()
    #: §5.1.7: collected with a broken or missing seal.
    flagged: tuple[str, ...] = ()

    @property
    def collected(self) -> tuple[str, ...]:
        return tuple(v.mailbag_id for v in self.visits if v.collected)


@dataclass(slots=True)
class _Van:
    """A van's state through the day: where it is, when it is free, what it holds."""

    record: dict[str, Any]
    lat: float
    lon: float
    clock: int
    bags: int = 0
    grams: int = 0
    route: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.record["vehicle_id"]


def run(
    requests: Sequence[Mapping[str, Any]],
    vans: Sequence[Mapping[str, Any]],
    hub: Mapping[str, Any], *,
    travel: Travel,
    cut_off: int,
    cadence_seconds: int = assumptions.REOPT_CADENCE_MIN * 60,
    incidents: Mapping[str, Incident] | None = None,
    surplus_bags: Mapping[str, int] | None = None,
) -> Dispatch:
    """Run one pickup day on a re-optimisation cadence.

    Args:
        requests: §9.1 mailbag records, each with `requested_at`. They arrive
            through the day and are only visible to a cycle that has reached
            their time.
        vans: §9.1 vehicle records earmarked for pickups (§5.1.3). A record
            that is not a van is refused by `admission.can_take`, so §7.1's
            van-only rule is enforced by the same code that enforces capacity.
        hub: the §3.1 hub row, where every van starts and returns.
        travel: seconds between two points. Required; see the module docstring.
        cut_off: §5.1.6's processing cut-off, as a second.
        cadence_seconds: §5.1.5's re-optimisation interval.
        incidents: mailbag id -> what happened at that site (§5.1.7).
        surplus_bags: mailbag id -> bags found beyond the manifest, for
            `Incident.MORE_BAGS`. The surplus re-enters the pool at the next
            cycle, so §5.1.7's "another van or a second visit" is decided by
            the same admission rule as any other bag.

    Returns:
        A `Dispatch`. Every bag appears exactly once across `collected`,
        `unplaced` and `re_requests` -- a bag in none of them is one nobody is
        looking for.
    """
    incidents = dict(incidents or {})
    surplus = dict(surplus_bags or {})

    fleet = [_Van(record=dict(v), lat=hub["lat"], lon=hub["lon"],
                  clock=int(v["shift_start"])) for v in vans]
    pending = {r["mailbag_id"]: dict(r) for r in requests}
    log = _Log()

    times = [int(r["requested_at"]) for r in requests] or [cut_off]
    tick = min(times)
    while tick <= cut_off and pending:
        _cycle(tick, fleet, pending, log, incidents=incidents,
               surplus=surplus, travel=travel, hub=hub, cut_off=cut_off)
        tick += cadence_seconds

    for van in fleet:
        van.clock += travel(van.lat, van.lon, hub["lat"], hub["lon"])

    return Dispatch(
        visits=tuple(log.visits),
        routes={van.id: tuple(van.route) for van in fleet},
        returned_at={van.id: van.clock for van in fleet},
        unplaced=tuple(sorted(pending)),
        re_requests=tuple(log.re_requests),
        flagged=tuple(log.flagged))


@dataclass(slots=True)
class _Log:
    """What the day produced, gathered as it happens rather than returned up."""

    visits: list[Visit] = field(default_factory=list)
    re_requests: list[str] = field(default_factory=list)
    flagged: list[str] = field(default_factory=list)


def _cycle(tick: int, fleet: Sequence[_Van],
           pending: dict[str, dict[str, Any]], log: _Log, *,
           incidents: Mapping[str, Incident], surplus: Mapping[str, int],
           travel: Travel, hub: Mapping[str, Any], cut_off: int) -> None:
    """One re-optimisation cycle (§5.1.5), at `tick`.

    Only requests the cycle has reached are visible, and only bags still
    pending can be placed -- a stop already visited belongs to the van that
    visited it, whatever a later cycle would prefer.
    """
    # §5.1.7: "Request cancelled after dispatch -- stop removed at next cycle."
    # A cancellation is the one incident that resolves without a visit, so it
    # is applied before any van is offered the stop.
    for mailbag_id in [m for m, r in pending.items()
                       if incidents.get(m) is Incident.CANCELLED
                       and int(r["requested_at"]) <= tick]:
        del pending[mailbag_id]

    arrived = sorted(
        (r for r in pending.values() if int(r["requested_at"]) <= tick),
        key=lambda r: (int(r["requested_at"]), r["mailbag_id"]))

    for request in arrived:
        bag = load(request)
        chosen = _cheapest(fleet, bag, travel=travel, hub=hub, cut_off=cut_off)
        if chosen is None:
            continue

        leg = travel(chosen.lat, chosen.lon, bag["lat"], bag["lon"])
        at = max(chosen.clock + leg, tick)
        del pending[bag["mailbag_id"]]
        log.visits.append(_visit(chosen, bag, at, incidents, surplus, pending,
                                 log.re_requests, log.flagged))
        chosen.clock = at + assumptions.PICKUP_STOP_MIN * 60
        chosen.lat, chosen.lon = bag["lat"], bag["lon"]


def _cheapest(fleet: Sequence[_Van], bag: Mapping[str, Any], *,
              travel: Travel, hub: Mapping[str, Any],
              cut_off: int) -> _Van | None:
    """§5.1.4: the van that can take it for the least additional route cost."""
    candidates = []
    for van in fleet:
        leg = travel(van.lat, van.lon, bag["lat"], bag["lon"])
        home = travel(bag["lat"], bag["lon"], hub["lat"], hub["lon"])
        back_at = van.clock + leg + assumptions.PICKUP_STOP_MIN * 60 + home
        if can_take(van.record, carrying_bags=van.bags, carrying_g=van.grams,
                    request=bag, back_at=back_at, cut_off=cut_off):
            candidates.append((leg, van.id, van))
    if not candidates:
        return None
    return min(candidates)[2]


def _visit(van: _Van, bag: Mapping[str, Any], at: int,
           incidents: Mapping[str, Incident], surplus: Mapping[str, int],
           pending: dict[str, dict[str, Any]], re_requests: list[str],
           flagged: list[str]) -> Visit:
    """Apply §5.1.7 at the doorstep, and update what the van is carrying."""
    mailbag_id = bag["mailbag_id"]
    incident = incidents.get(mailbag_id)

    if incident in (Incident.BAG_NOT_READY, Incident.SITE_CLOSED):
        # §5.1.7: the visit happened and nothing was collected. The van keeps
        # its capacity -- reserving it for a bag that does not exist would turn
        # one empty doorstep into a second bag nobody could take.
        re_requests.append(mailbag_id)
        return Visit(van.id, mailbag_id, at, collected=False, incident=incident,
                     re_request=True)

    van.bags += int(bag[BAGS])
    van.grams += int(bag[WEIGHT])
    van.route.append(mailbag_id)

    if incident is Incident.SEAL_BROKEN:
        flagged.append(mailbag_id)
        return Visit(van.id, mailbag_id, at, collected=True, incident=incident,
                     flagged=True)

    if incident is Incident.MORE_BAGS:
        # §5.1.7: "Collect what fits; remainder assigned to another van or a
        # second visit." The surplus re-enters the pool as its own request, so
        # the next cycle decides which by the ordinary admission rule.
        extra = int(surplus.get(mailbag_id, 0))
        for n in range(extra):
            spare_id = f"{mailbag_id}+{n + 1}"
            pending[spare_id] = dict(bag, mailbag_id=spare_id,
                                     expected_weight_g=int(bag[WEIGHT]),
                                     requested_at=at)
        return Visit(van.id, mailbag_id, at, collected=True, incident=incident)

    return Visit(van.id, mailbag_id, at, collected=True)


def uncollected(dispatch: Dispatch | None,
                inflow: Sequence[Mapping[str, Any]]) -> int:
    """Envelopes on a bag no van brought back before §5.1.6's cut-off.

    A day with no pickups collected nothing, so every envelope of the inflow is
    uncollected -- not zero, which would read as "all collected" and is the
    answer that makes the conservation identity silently true.
    """
    if dispatch is None:
        return len(inflow)
    collected = set(dispatch.collected)
    return sum(1 for e in inflow if e["mailbag_id"] not in collected)
