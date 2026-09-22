"""§5.2.6's envelope status lifecycle, as a state machine.

The diagram in §5.2.6 is the whole specification of legal movement, and
`TRANSITIONS` is a transcription of it -- written from the document rather than
from what any other module happens to do with `status`, so that it can disagree
with the code if the code is wrong.

Three readings are still needed where the diagram is drawn rather than
stated. Each is recorded here rather than settled silently (CLAUDE.md, "when
the spec is ambiguous"):

1. **Ready -> Dispatched.** §5.2.6 parenthesises `(Line-haul -> At depot)`, so
   the depot leg is optional: §3.3's hub-direct envelopes are dispatched from
   the hub without ever being line-hauled.
2. **Return run is a status**, not just an arrow: the diagram names it in the
   flow, and an envelope sits in it between the facility and the customer.
3. **At depot -> Dispatched only.** Nothing in §5.3 or §5.5 moves an envelope
   back out of a depot except by being dispatched; the depot's own rejects
   travel back on the van's return leg as new return-run stops (§5.5).

Three more were needed until v0.13 and are not any longer -- `Ready -> Return
run`, `Transfer requested -> Ready` and `Transfer requested -> Return run` are
now drawn in §5.2.6 itself. They were argued for here, the audit rated all
three `CONTRADICTS` against v0.12, and the document settled them the same way.
The arguments are gone; the edges stay, on the spec's authority rather than
this module's.

(The heading above said "Four readings" while six were listed, for as long as
there were six. A count in a sentence is not a count anything reads.)

Postponed returns to Ready "for next attempt, until SLA date" (§5.2.6, §6.1);
the SLA test itself is `Envelope.must_deliver_today` and the expiry edge is
reading 2 above, so this module enforces shape and the caller enforces dates.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """The statuses named in §5.2.6, spelled as the document spells them."""

    REQUESTED = "Requested"
    COLLECTED = "Collected"
    RECEIVED_AT_HUB = "Received at hub"
    RECONCILED = "Reconciled"
    ASSEMBLED = "Assembled"
    SORTED = "Sorted"
    READY = "Ready"
    LINE_HAUL = "Line-haul"
    AT_DEPOT = "At depot"
    DISPATCHED = "Dispatched"
    DELIVERED = "Delivered"
    REJECTED = "Rejected"
    RETURNED = "Returned"
    POSTPONED = "Postponed"
    #: §5.3.2's transfer, as §5.2.6 draws it: an envelope already at a depot
    #: that belongs at a different one.
    TRANSFER_REQUESTED = "Transfer requested"
    IN_TRANSFER = "In transfer"
    RETURN_RUN = "Return run"
    RETURNED_TO_CUSTOMER = "Returned to customer"


TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.REQUESTED: frozenset({Status.COLLECTED}),
    Status.COLLECTED: frozenset({Status.RECEIVED_AT_HUB}),
    Status.RECEIVED_AT_HUB: frozenset({Status.RECONCILED}),
    # §5.2.3: assembly applies to certain package types only, so Reconciled
    # reaches Sorted either directly or through Assembled.
    Status.RECONCILED: frozenset({Status.ASSEMBLED, Status.SORTED}),
    Status.ASSEMBLED: frozenset({Status.SORTED}),
    Status.SORTED: frozenset({Status.READY}),
    Status.READY: frozenset(
        {Status.LINE_HAUL, Status.DISPATCHED, Status.RETURN_RUN}
    ),
    Status.LINE_HAUL: frozenset({Status.AT_DEPOT}),
    Status.AT_DEPOT: frozenset({Status.DISPATCHED}),
    # §6 v0.15's cancellation reaches Return run from both sides of dispatch:
    # before it the envelope is still Ready, after it the stop comes off the
    # route. Without the second edge "the stop is removed if not yet visited"
    # had nowhere legal to go -- §5.2.6 drew nothing out of Dispatched but the
    # four attempt outcomes.
    Status.DISPATCHED: frozenset(
        {Status.DELIVERED, Status.REJECTED, Status.RETURNED, Status.POSTPONED,
         Status.RETURN_RUN}
    ),
    # §5.2.6: "Postponed: back to Ready for next attempt" and "Postponed with
    # facility change, or misassignment found: Transfer requested".
    Status.POSTPONED: frozenset({Status.READY, Status.TRANSFER_REQUESTED}),
    # A transfer that cannot beat its deadline is not raised at all (§7.1), and
    # one already raised that cannot be carried goes back to the customer --
    # §5.3.2: "otherwise it is returned to the customer via the hub".
    Status.TRANSFER_REQUESTED: frozenset(
        {Status.IN_TRANSFER, Status.READY, Status.RETURN_RUN}),
    # §5.2.6 ends the transfer at Ready, "at new depot". The envelope is
    # routable again the moment it arrives, and not before (§7.1).
    Status.IN_TRANSFER: frozenset({Status.READY}),
    Status.REJECTED: frozenset({Status.RETURN_RUN}),
    Status.RETURNED: frozenset({Status.RETURN_RUN}),
    Status.RETURN_RUN: frozenset({Status.RETURNED_TO_CUSTOMER}),
    Status.DELIVERED: frozenset(),
    Status.RETURNED_TO_CUSTOMER: frozenset(),
}

#: Statuses an envelope never leaves. §6's "Closed" and the end of the return
#: run; both remove the envelope from the next day's pool.
TERMINAL = frozenset({Status.DELIVERED, Status.RETURNED_TO_CUSTOMER})

#: The only status that is a solver input for delivery routing (§5.2.6).
ROUTABLE = frozenset({Status.READY})


class IllegalTransition(ValueError):
    """A move §5.2.6 does not draw. Not a warning: the transition does not exist."""

    def __init__(self, source: Status, target: Status) -> None:
        allowed = ", ".join(sorted(TRANSITIONS[source])) or "nothing (terminal)"
        super().__init__(
            f"§5.2.6 draws no {source} -> {target}; from {source} an envelope "
            f"may reach: {allowed}"
        )
        self.source = source
        self.target = target


def may(source: Status, target: Status) -> bool:
    """Whether §5.2.6 draws an edge from `source` to `target`."""
    return target in TRANSITIONS[source]


def advance(source: Status, target: Status) -> Status:
    """Return `target`, or raise `IllegalTransition` if §5.2.6 does not draw it."""
    if not may(source, target):
        raise IllegalTransition(source, target)
    return target


#: §6's outcome -> the §5.2.6 status a Dispatched envelope reaches by it.
#: `may`/`advance` *validate* a transition the caller has already chosen;
#: nothing here chose one, so every caller picked its own and they agreed by
#: coincidence. §6's table is that choice, written once.
#:
#: Keyed by §6's own spelling rather than by `records.Outcome`, because
#: `records` imports `Status` from this module and the other direction would
#: close the circle. `Outcome` is a `StrEnum`, so its members are accepted as
#: keys, and `tests/test_model.py` pins the two vocabularies to each other --
#: which is the guard that matters, since nothing else would notice them drift.
#: §6's outcome for a withdrawal. Not an attempt outcome: `record` never asks
#: its source for it, and `after_attempt` refuses it.
CANCELLED = "Cancelled"

AFTER_ATTEMPT: dict[str, Status] = {
    "Delivered": Status.DELIVERED,
    "Rejected": Status.REJECTED,
    "Returned": Status.RETURNED,
    "Postponed": Status.POSTPONED,
}


def after_attempt(outcome: str) -> Status:
    """Where §6 puts an envelope that was attempted and got this outcome.

    Validated against §5.2.6 rather than asserted: `advance` raises if the edge
    is not drawn, so this cannot quietly name a status Dispatched does not
    reach. That is the whole reason it goes through the table instead of
    returning `Status(outcome)` -- the two spellings coincide today, and a
    §5.2.6 change that broke the coincidence would surface here rather than in
    a pool three stages later.
    """
    try:
        target = AFTER_ATTEMPT[str(outcome)]
    except KeyError as unknown:
        raise IllegalTransition(Status.DISPATCHED, outcome) from unknown
    return advance(Status.DISPATCHED, target)


#: The outcomes a delivery attempt can produce — §6's table minus `Cancelled`.
ATTEMPT_OUTCOMES = frozenset(AFTER_ATTEMPT)


def after_cancellation(current: Status) -> Status:
    """§6 v0.15: where a withdrawn envelope goes, from wherever it is now.

    Legal from **Ready** (not yet dispatched) and from **Dispatched** (on a
    route, stop not yet visited). Illegal from Delivered, which §5.2.6 draws
    nothing out of — so a cancellation arriving after the stop was visited
    raises here and §13.1 answers it `409` with the current state. That
    refusal is `advance`'s, not a rule written twice.
    """
    return advance(current, Status.RETURN_RUN)


def settles(outcome: str) -> bool:
    """Whether §6 is finished with this envelope, or it comes round again.

    Delivered is terminal. A rejection or a defect goes to the return run, and
    a postponement comes back to Ready -- so neither is a day's end for it,
    and both leave something for tomorrow to carry.
    """
    return after_attempt(outcome) in TERMINAL
