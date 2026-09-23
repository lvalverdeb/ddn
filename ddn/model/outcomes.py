"""§6 — what an attempt does to the envelope it was made on.

§5.2.6 draws the edges and `lifecycle` validates them; §6 says which edge a
delivery attempt takes and what the envelope looks like afterwards. That is
this module, and it had no home before: the one implementation lived inside
`simulation.day._doorstep`, wrapped around the tallying and the RNG draw, so
anything else needing it had to reimplement it.

**The outcome is an input here, not a draw.** e2e-3 §3 lists outcomes under
"Driver app, operations (§13.2)" and puts outcome *rates* under "Assumptions …
(simulation only)". A real day is told what happened at the doorstep; only a
simulation invents it. So `record` takes two callables and the caller decides
whether they read a database or a `random.Random`.

**The order in which it asks them is part of the contract.** `outcome` is
called exactly once per envelope in the order it is given them, and `reason`
immediately after, only for a postponement. A caller that drew every outcome
first and then every reason would walk the same shared generator in a
different order and get a different day -- one with identical totals, which is
the kind of difference nothing downstream can see.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ddn.model.lifecycle import (
    IllegalTransition,
    Status,
    advance,
    after_attempt,
)

__all__ = ["Attempt", "Recorded", "arrived", "for_retry", "in_transfer",
           "record", "transfer_requested"]

#: §6's outcome for an envelope refused at the doorstep for the recipient's
#: own reason, as `records.Outcome` spells them. Named here because this module
#: branches on two of them and `records` cannot be imported (it reads `Status`
#: from `lifecycle`, which would close the circle).
DELIVERED = "Delivered"
POSTPONED = "Postponed"


@dataclass(frozen=True, slots=True)
class Attempt:
    """One envelope, what happened to it, and where §5.2.6 puts it next."""

    package_id: str
    outcome: str
    status: Status
    #: The envelope as it now stands: the postponed retry record, the
    #: stamped return, or the original where §6 changes nothing.
    record: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Recorded:
    """§6 applied to everything attempted at one facility on one day."""

    attempts: tuple[Attempt, ...] = ()
    #: §5.2.6: "Postponed: back to Ready for next attempt". Tomorrow's pool.
    postponed: tuple[dict[str, Any], ...] = ()
    #: §5.5's load: the expired, the rejected and the defective, stamped so
    #: `returns.goes_back` can recognise them.
    going_back: tuple[dict[str, Any], ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    #: §6.1 postponements with no day left. They are in `going_back`, and
    #: §11's expiry rate counts them alongside the dispatch-time sweep.
    spent: int = 0

    def within_sla(self, today, expired: Callable[..., bool]) -> int:
        """§11: delivered, and not past its SLA date when it was.

        The predicate is passed in rather than imported. §6.1's clock lives in
        `lastmile` beside `select`, its only other caller, and `ddn/model/` is
        the bottom of this package — importing a stage from here would invert
        the layering to save one argument.

        `today` is passed for the same reason it is passed everywhere else: a
        replay of a past day must answer what that day answered.
        """
        return sum(1 for a in self.attempts
                   if a.outcome == DELIVERED and not expired(a.record, today))

    @property
    def first_attempt(self) -> int:
        """Delivered without a previous try — §11's first-attempt rate."""
        return sum(1 for a in self.attempts
                   if a.outcome == DELIVERED
                   and int(a.record.get("attempt_number", 0)) == 0)


def for_retry(envelope: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """§6: a postponed envelope is held Ready at its facility for another go."""
    return dict(envelope, status="Ready", previous_outcome=POSTPONED,
                postponed_reason=reason,
                attempt_number=int(envelope.get("attempt_number", 0)) + 1)


def _move(envelope: Mapping[str, Any], target: Status) -> dict[str, Any]:
    """Advance a §9.1 record, refusing an edge §5.2.6 does not draw.

    The source is the record's own `status`; a record without one is refused
    rather than assumed, because assuming it is where the caller wanted it to
    be is how a check becomes decorative.
    """
    current = envelope.get("status")
    if current is None:
        raise IllegalTransition(Status.READY, target)
    return dict(envelope, status=str(advance(Status(current), target)))


def transfer_requested(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """§5.2.6: "Postponed with facility change ... Transfer requested".

    The edge was drawn and nothing took it, so an envelope with a transfer
    raised against it was indistinguishable from one sitting at its depot.
    That matters to §7.1, whose "an envelope in transfer is not routable until
    it arrives" is a statement about status: `postcheck._across_the_day`
    refuses any served order whose record is not `Ready`, so stamping this is
    what lets the existing check see a transfer at all.

    Validated through `advance` **from the record's own status**, not from a
    hard-coded source: the first version passed `Status.POSTPONED` in, so it
    would happily stamp a Delivered envelope as awaiting transfer and the
    check was decorative.
    """
    return _move(envelope, Status.TRANSFER_REQUESTED)


def in_transfer(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """§5.2.6: the leg has picked it up and it is not at either depot."""
    return _move(envelope, Status.IN_TRANSFER)


def arrived(envelope: Mapping[str, Any], facility_id: str) -> dict[str, Any]:
    """§5.2.6 ends a transfer at "Ready (at new depot)", and not before.

    §7.1: routable the moment it arrives and not one moment earlier, so the
    facility and the status move together — an envelope stamped with its new
    depot while still `In transfer` is exactly the thing the bullet forbids.
    """
    return dict(_move(envelope, Status.READY), facility_id=facility_id)


def sent_back(envelope: Mapping[str, Any], outcome: str) -> dict[str, Any]:
    """§5.5: an envelope refused at the door, stamped with why.

    It stays where it was refused. Stamping a facility here is what made every
    rejection hub-resident the instant it happened, so it joined *tonight's*
    return run -- the one thing §5.5 says it must not do.
    """
    return dict(envelope, previous_outcome=outcome)


def expired(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """§6.1: "after that it is returned to the customer via the return run".

    The flag is what `returns.goes_back` reads. Without it an expired envelope
    reached the return run and was filtered straight back out, which is an
    envelope destroyed rather than returned.
    """
    return dict(envelope, sla_expired=True)


def record(attempted: Sequence[Mapping[str, Any]],
           swept: Sequence[Mapping[str, Any]] = (), *,
           outcome: Callable[[Mapping[str, Any]], str],
           reason: Callable[[Mapping[str, Any]], str],
           retryable: Callable[[Mapping[str, Any]], bool] | None = None
           ) -> Recorded:
    """Ask what happened to each envelope, and say what it becomes.

    Args:
        attempted: the envelopes a vehicle actually went to, in the order it
            went to them. The order reaches `outcome` unchanged and is part of
            the contract -- see the module docstring.
        swept: envelopes §6.1 expired before dispatch. They were never
            attempted, so `outcome` is not asked about them, but they are
            §5.5's load tonight all the same.
        outcome: §6's outcome for one envelope. Called once per envelope.
        reason: §6's sub-reason for a postponement. Called immediately after
            `outcome`, and only when the outcome was one.
        retryable: whether a postponed envelope still has a day to be retried
            on. §6.1's clock lives in `lastmile`, so it is passed rather than
            imported -- `ddn/model/` is the bottom of this package. Omitted,
            every postponement is held for another go, which is the answer for
            a day that does not know its own SLA dates rather than a claim
            that none has run out.

    Returns:
        A `Recorded`: one `Attempt` per envelope attempted, tomorrow's
        postponed pool, tonight's return load, and the outcome counts. A
        postponement with no day left appears in `going_back` rather than in
        `postponed`, stamped so `returns.goes_back` recognises it.
    """
    attempts: list[Attempt] = []
    postponed: list[dict[str, Any]] = []
    going_back: list[dict[str, Any]] = [expired(e) for e in swept]
    counts: dict[str, int] = {}
    spent = 0

    for envelope in attempted:
        what = outcome(envelope)
        counts[what] = counts.get(what, 0) + 1
        status = after_attempt(what)

        if what == POSTPONED:
            became = for_retry(envelope, reason(envelope))
            if retryable is None or retryable(envelope):
                postponed.append(became)
            else:
                # §6.1: "an envelope may be retried until its SLA date; after
                # that it is returned to the customer via the return run". A
                # postponement on the SLA date itself has no next attempt, so
                # holding it in tomorrow's pool would offer it on a day it may
                # not be dispatched on -- and it would sit there until the
                # *next* morning's sweep noticed, a day late.
                became = expired(became)
                going_back.append(became)
                spent += 1
        elif what == DELIVERED:
            became = dict(envelope)
        else:
            became = sent_back(envelope, what)
            going_back.append(became)

        attempts.append(Attempt(envelope["package_id"], what, status, became))

    return Recorded(tuple(attempts), tuple(postponed), tuple(going_back),
                    counts, spent)
