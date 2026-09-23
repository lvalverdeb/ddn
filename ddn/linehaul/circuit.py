"""§5.3.2's circuits: a van's trip as an ordered sequence of facility legs.

§5.3.1 was an assignment -- one van, one depot, one leg. §5.3.2 generalises it:
"the line-haul planner builds each van's trip as an ordered sequence of
facility legs starting and ending at the hub ... carrying on each leg:
hub-origin loads for facilities still ahead on the circuit, transfer loads
picked up at earlier depots, and returns bound for the hub."

**Not solved.** §5.3.2 calls this "a small pickup-and-delivery VRP over at most
seven nodes with per-load deadlines" and then says what matters: "node count is
tiny; the difficulty is timing against release deadlines and van-hours, not
combinatorics." `docs/solver-capabilities.md` confirms five capabilities and
says nothing about shipments or pickup-and-delivery, and CLAUDE.md is explicit
that an unconfirmed capability gets the spec's documented fallback rather than
an assumption. So this is a deterministic planner over seven nodes, and the
thing it is careful about is deadlines.

**Inter-depot transit is the caller's.** §3.1 gives transit from the hub and
nothing between depots -- §12 Q8 asks only for the hub figures -- so a circuit
that visits two depots needs a `transit` the caller supplies from road data.
Without one, a transfer is refused rather than run on a guess, the same refusal
`pickups.dispatch` and `runner` already make.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: seconds between two facilities, by id. From the caller's road matrix.
Transit = Callable[[str, str], int]

#: §7.1: "A van's combined load across hub-origin, transfer and return
#: envelopes never exceeds 500 kg on any leg."
MAX_LOAD_G = 500_000


@dataclass(frozen=True, slots=True)
class Leg:
    """One facility-to-facility movement, and everything aboard for it."""

    from_facility: str
    to_facility: str
    departure: int
    arrival: int
    #: Hub-origin envelopes still to be dropped, by destination facility.
    hub_loads: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: §9.1 transfer ids picked up at an earlier facility on this circuit.
    transfer_ids: tuple[str, ...] = ()
    #: Envelopes riding back to the hub (§5.5's depot rejects).
    return_ids: tuple[str, ...] = ()
    weight_g: int = 0

    @property
    def overloaded(self) -> bool:
        """§7.1's combined-load bullet, for this leg."""
        return self.weight_g > MAX_LOAD_G


@dataclass(frozen=True, slots=True)
class Declined:
    """A transfer the night could not carry, and why (§9.2)."""

    transfer_id: str
    reason: str


#: Seconds in a day. §5.3's clock runs from the *line-haul* day's midnight and
#: a depot's release is the next morning, so a release sits past 24 h and a
#: deadline's time of day is `release % DAY`. Defined here rather than in
#: `plan`, which imports from this module: one definition, and the comparison
#: in `_ranked` needs it.
DAY = 24 * 3600

#: §9.2 asks for "transfers not carried, with reason". These are the three a
#: planner can find; §5.3.2's own wording supplies the words.
NO_VAN_LEG = "no van circuit reaches the destination depot tonight"
MISSES_DEADLINE = "cannot arrive before the deadline"
OVER_CAPACITY = "the circuit is already at 500 kg"


def _key(hard: bool, priority: float | None, ident: str) -> tuple[bool, float, str]:
    """§5.3.2's ranking key, for a transfer or a hub-origin envelope alike.

    "Both are ranked by the same priority score as delivery (§8.1)" -- so there
    is one key and both sides are sorted by it. Hard inclusions first, then
    descending score, then the id so that two runs of one night agree.
    """
    return (not hard, -float(priority or 0), ident)


def _due(transfer: Any, release_of: Mapping[str, int]) -> bool:
    """Whether this transfer is §5.3.2's hard inclusion. See `_ranked`."""
    release = release_of.get(transfer.to_facility_id)
    if release is None:
        return False
    at = transfer.deadline
    return (at.hour * 3600 + at.minute * 60 + at.second) != release % DAY


def _ranked(transfers: Sequence[Any],
            release_of: Mapping[str, int]) -> list[Any]:
    """§5.3.2's order when the circuit cannot carry everything.

    "Both are ranked by the same priority score as delivery (§8.1); a transfer
    whose envelope is due on the receiving depot's next morning release is a
    hard inclusion." So the due ones go first whatever they score, and the
    rest descend by score with the transfer id settling a tie -- the same
    shape as `lastmile.select`, and for the same reason: two runs of one night
    must decline the same transfers.

    This used to be the caller's list order, which `simulation.day` made
    "yesterday's queue first" the moment v0.15 gave it a queue. That is a
    policy, and it was not one anybody chose.

    **Due** is read off the deadline, which §9.1 defines as `min(receiving
    depot's next morning release, SLA date)`. When the SLA date is what set
    that minimum -- the deadline falls *before* the release -- the envelope is
    out of days after this circuit, and that is the hard inclusion v0.16's
    rewording names. When the release set it, there is slack.

    So the test is "the deadline is not the release", and it is a time of day
    because the release is one: an envelope due three days out and one due
    tomorrow both sit at the release's hour, and comparing dates would call
    the first urgent. An envelope due *today* is not here at all -- §7.1
    refuses to raise it, because line-haul runs on D and delivery on D+1.

    The comparison degenerates if a depot ever releases at 00:00: an SLA-set
    deadline landing on midnight would read as slack. `ROUTE_RELEASE` is one
    invented placeholder at 07:00 for every facility (§3.1's own column is
    still [TBD]), so no such depot exists today; a real §3.1 that gives one a
    midnight release needs the deadline compared against its own date instead.
    """
    return sorted(transfers,
                  key=lambda t: _key(_due(t, release_of), t.priority,
                                     t.transfer_id))


def sequence_departure(stops: Sequence[str], *, hub_transit: Mapping[str, int],
                       transit: Transit, release_of: Mapping[str, int],
                       unload_seconds: int) -> int:
    """The latest departure from the hub that still makes every stop's release.

    §5.3 says a van should leave as late as it can, because "a van that leaves
    early strands everything still in the clean room". With one stop that is
    the depot's own latest departure. With a circuit it is the *tightest* stop
    on it: a van that departs late enough for D1 and then cannot reach D3 by
    07:00 has not served D3, and §5.3.2's whole constraint is arrival "before
    the receiving depot's morning release".
    """
    latest = None
    elapsed = 0
    previous = None
    for stop in stops:
        elapsed += (hub_transit[stop] if previous is None
                    else transit(previous, stop))
        allowed = release_of[stop] - unload_seconds - elapsed
        latest = allowed if latest is None else min(latest, allowed)
        previous = stop
    return 0 if latest is None else latest


def legs_for(stops: Sequence[str], departure: int, *,
             hub_id: str, hub_transit: Mapping[str, int],
             transit: Transit) -> list[tuple[str, str, int, int]]:
    """`(from, to, departure, arrival)` for each leg of a circuit.

    §5.3.2: "an ordered sequence of facility legs starting **and ending** at the
    hub". The closing hop is one of those legs, not bookkeeping: it is the leg
    §5.5's depot rejects ride home on, and a circuit that stopped at its last
    depot would have nowhere to put them. `Trip.returns` still reports when the
    van is back and unloaded, which is a different question from where it went.
    """
    legs = []
    at, clock = hub_id, departure
    for stop in stops:
        travel = hub_transit[stop] if at == hub_id else transit(at, stop)
        legs.append((at, stop, clock, clock + travel))
        at, clock = stop, clock + travel
    # Home again. §3.1 gives transit from the hub and the return is the same
    # road, which is the symmetry `Trip.returns` has always assumed.
    legs.append((at, hub_id, clock, clock + hub_transit[at]))
    return legs


def choose(stops: list[str], transfers: Sequence[Any], *,
           transit: Transit | None, hub_transit: Mapping[str, int],
           release_of: Mapping[str, int], unload_seconds: int,
           earliest_departure: int,
           carried_g: int) -> tuple[list[str], list[Any], list[Declined]]:
    """Which transfers this circuit can take, and where it must go to take them.

    Each candidate is added provisionally and the whole circuit is re-timed. If
    the van would then have to leave before it is even back from pickups, the
    transfer does not fit tonight and is declined rather than forcing a
    departure nobody can make.
    """
    if not transfers:
        return stops, [], []
    if transit is None:
        return stops, [], [
            Declined(t.transfer_id, "no inter-depot transit supplied; §3.1 "
                                    "gives none and this planner guesses none")
            for t in transfers]

    carried: list[Any] = []
    declined: list[Declined] = []

    for transfer in _ranked(transfers, release_of):
        if transfer.from_facility_id not in stops:
            declined.append(Declined(transfer.transfer_id, NO_VAN_LEG))
            continue
        if carried_g + transfer.weight_g > MAX_LOAD_G:
            declined.append(Declined(transfer.transfer_id, OVER_CAPACITY))
            continue

        extended = ([*stops, transfer.to_facility_id]
                    if transfer.to_facility_id not in stops else list(stops))
        departure = sequence_departure(
            extended, hub_transit=hub_transit, transit=transit,
            release_of=release_of, unload_seconds=unload_seconds)
        if departure < earliest_departure:
            declined.append(Declined(transfer.transfer_id, MISSES_DEADLINE))
            continue

        stops = extended
        carried.append(transfer)
        carried_g += transfer.weight_g

    return stops, carried, declined


@dataclass(frozen=True, slots=True)
class Loaded:
    """What one circuit carries once §5.3.2's competition has been run."""

    stops: list[str]
    #: hub-origin envelopes that won capacity, and those that lost it.
    boarded: list[dict[str, Any]]
    bumped: list[dict[str, Any]]
    carried: list[Any]
    declined: list[Declined]


def compete(stops: list[str], envelopes: Sequence[dict[str, Any]],
            transfers: Sequence[Any], *,
            transit: Transit | None, hub_transit: Mapping[str, int],
            release_of: Mapping[str, int], unload_seconds: int,
            earliest_departure: int, reserved_g: int) -> Loaded:
    """§5.3.2's Priority paragraph: one 500 kg, two kinds of load.

    "When van capacity or van-hours are short, hub-origin loads and transfers
    compete. Both are ranked by the same priority score as delivery (§8.1)."

    Before this, `plan` weighed the *whole* hub-origin pool and handed `choose`
    only what was left: a depot's envelopes boarded unconditionally and a
    transfer could have the remainder. That is not a competition. On a van that
    is short it is also the wrong answer, because §8.1's score is what decides
    which load rides, and a hub-origin envelope scoring 1 has no claim over a
    transfer scoring 900 simply for having started at the hub.

    So both sides are ranked by `_key` and walked in that one order.

    **Feasibility first, capacity second**, because they are different
    questions. `choose` settles which transfers this circuit can reach in time
    at all -- a transfer with no leg or no hours is declined there and never
    reaches the walk, where it would otherwise displace an envelope over a seat
    it could not use. Only the feasible ones compete.

    **The hard inclusion is one-sided, deliberately.** A transfer keeps the one
    `_ranked` gives it. §6.1's SLA-today has no envelope equivalent here: `plan`
    holds no reference date to compare `sla_date` against, and by the same D/D+1
    reasoning that removed the transfer's SLA-today case in v0.16, an envelope
    due *today* cannot be served by a line-haul that arrives tomorrow morning
    either. An envelope due tomorrow is the one that would qualify, and saying
    so needs a `today` this planner is not given -- recorded rather than
    guessed.

    Args:
        reserved_g: weight already committed and outside the contest -- §5.5's
            returns, which ride back to the hub and which §5.3.2 does not rank
            against anything. It is deliberately *not* bounded by `MAX_LOAD_G`:
            returns over 500 kg leave nothing for anyone, so every envelope is
            bumped and every feasible transfer declined `OVER_CAPACITY` -- whose
            wording, "already at 500 kg", stays true of that circuit. The
            planner has no authority to refuse a return, so the breach it makes
            is real and is left for §7.1's post-check to report rather than
            hidden by dropping load the spec never ranked.
    """
    stops, feasible, declined = choose(
        stops, transfers, transit=transit, hub_transit=hub_transit,
        release_of=release_of, unload_seconds=unload_seconds,
        earliest_departure=earliest_departure, carried_g=reserved_g)

    candidates: list[tuple[tuple[bool, float, str], int, Any, Any]] = [
        (_key(_due(t, release_of), t.priority, t.transfer_id),
         int(t.weight_g), t, None)
        for t in feasible
    ] + [
        (_key(False, e.get("priority"), e["package_id"]),
         int(e.get("weight_g", 200)), None, e)
        for e in envelopes
    ]

    weight = reserved_g
    carried: list[Any] = []
    boarded: list[dict[str, Any]] = []
    bumped: list[dict[str, Any]] = []
    # Sorted on the key alone: two candidates that tie would otherwise have
    # Python compare the payloads, and a transfer does not order against a dict.
    for _, grams, transfer, envelope in sorted(candidates, key=lambda c: c[0]):
        if weight + grams > MAX_LOAD_G:
            if transfer is None:
                bumped.append(envelope)
            else:
                declined.append(Declined(transfer.transfer_id, OVER_CAPACITY))
            continue
        weight += grams
        if transfer is None:
            boarded.append(envelope)
        else:
            carried.append(transfer)

    # A transfer that lost the walk may have pulled its destination into the
    # circuit on the way in. Drop the stops nothing is left for: the van has no
    # reason to drive there, and a shorter circuit only ever departs later, so
    # no deadline `choose` already cleared can be missed by the pruning.
    # Both ends of a carried transfer are kept, not just its destination: today
    # every transfer starts at `stops[0]`, but a keep-set that assumed so would
    # silently drop a pickup the first time this is called with two origins.
    keep = ({stops[0]} | {t.from_facility_id for t in carried}
            | {t.to_facility_id for t in carried})
    return Loaded(stops=[s for s in stops if s in keep], boarded=boarded,
                  bumped=bumped, carried=carried, declined=declined)
