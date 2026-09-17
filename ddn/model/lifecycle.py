"""§5.2.6's envelope status lifecycle, as a state machine.

The diagram in §5.2.6 is the whole specification of legal movement, and
`TRANSITIONS` is a transcription of it -- written from the document rather than
from what any other module happens to do with `status`, so that it can disagree
with the code if the code is wrong.

Four readings were needed where the diagram is drawn rather than stated. Each
is recorded here rather than settled silently (CLAUDE.md, "when the spec is
ambiguous"):

1. **Ready -> Dispatched.** §5.2.6 parenthesises `(Line-haul -> At depot)`, so
   the depot leg is optional: §3.3's hub-direct envelopes are dispatched from
   the hub without ever being line-hauled.
2. **Ready -> Return run.** The third line reads
   `Rejected / Returned / SLA expired: Return run`, but "SLA expired" is not a
   status -- it is the condition in §6.1 under which an envelope that is still
   Ready stops being retried. So the edge leaves Ready.
3. **Return run is a status**, not just an arrow: the diagram names it in the
   flow, and an envelope sits in it between the facility and the customer.
4. **At depot -> Dispatched only.** Nothing in §5.3 or §5.5 moves an envelope
   back out of a depot except by being dispatched; the depot's own rejects
   travel back on the van's return leg as new return-run stops (§5.5).

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
    Status.DISPATCHED: frozenset(
        {Status.DELIVERED, Status.REJECTED, Status.RETURNED, Status.POSTPONED}
    ),
    Status.POSTPONED: frozenset({Status.READY}),
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
