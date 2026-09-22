"""§11's success metrics, per day and cumulative.

Every row of §11 is here. **No targets are**: §11 leaves all twelve `[TBD]`
except the one it supplies itself, and a target is a commitment rather than an
input -- inventing "SLA compliance ≥ 98%" would put a promise in the repository
that no customer made. `docs/assumptions.md` says so under "Deliberately
absent".

A metric whose inputs the day did not produce is `None` rather than zero.
Distance per envelope needs routes, and a simulated day that never called a
solver has none; reporting 0.0 km would read as an extraordinarily efficient
day rather than as an unanswered question.

Counts are kept separately from ratios so that a week's figures are the week's
and not an average of averages: `Tally` adds, and every ratio is derived from
the summed counts.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

#: §11's own expectation for the one row it fills in: 20-30 per bike per day.
EXPECTED_PER_BIKE = (20, 30)


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator <= 0 else numerator / denominator


@dataclass(frozen=True, slots=True)
class Tally:
    """One day's raw counts. Adds, so a week is a tally too."""

    ready_pool: int = 0
    dispatched: int = 0
    delivered: int = 0
    delivered_first_attempt: int = 0
    delivered_within_sla: int = 0
    postponed: int = 0
    rejected: int = 0
    defective: int = 0
    sla_expired: int = 0
    #: §6 v0.15's withdrawals. Counted here because §11 asks for a
    #: cancellation rate and a rate needs a numerator.
    cancelled: int = 0
    unassigned: int = 0
    received: int = 0
    received_before_cut_off: int = 0
    ready_by_cut_off: int = 0
    disputed: int = 0
    pickups: int = 0
    pickup_wait_seconds: int = 0
    bikes_deployed: int = 0
    distance_m: int = 0
    solver_seconds: float = 0.0

    def __add__(self, other: Tally) -> Tally:
        return Tally(**{f.name: getattr(self, f.name) + getattr(other, f.name)
                        for f in fields(self)})


@dataclass(frozen=True, slots=True)
class Metrics:
    """§11's table, computed. `None` where the day produced no input for it."""

    pickup_responsiveness_s: float | None
    same_day_readiness: float | None
    reconciliation_discrepancy_rate: float | None
    first_attempt_delivery_rate: float | None
    postponement_rate: float | None
    unassigned_rate: float | None
    sla_compliance: float | None
    sla_expiry_rate: float | None
    distance_per_envelope_m: float | None
    envelopes_per_bike: float | None
    solver_seconds: float | None
    #: §11 v0.15's twelfth row. It was added to the document when §6 gained
    #: the outcome and not to this table, so `Metrics` answered eleven of
    #: twelve and four prose counts went on saying "eleven".
    cancellation_rate: float | None

    @property
    def within_expected_per_bike(self) -> bool | None:
        """§11's one supplied figure: 20-30 envelopes per bike per day.

        A run outside it is not a capacity finding. `docs/capacity-finding.md`
        exists because 12.8 was read as one.
        """
        if self.envelopes_per_bike is None:
            return None
        low, high = EXPECTED_PER_BIKE
        return low <= self.envelopes_per_bike <= high


def measure(tally: Tally) -> Metrics:
    """§11's twelve rows from one tally, daily or cumulative."""
    return Metrics(
        pickup_responsiveness_s=_ratio(tally.pickup_wait_seconds, tally.pickups),
        same_day_readiness=_ratio(tally.ready_by_cut_off,
                                  tally.received_before_cut_off),
        # `None` rather than 0.0, for the reason this module states at the
        # top: nothing sets `Tally.disputed` yet, and a rate of zero reads as
        # a day with no discrepancies rather than a day nobody counted. §10
        # has ten. Same idiom as `distance_per_envelope_m` below.
        reconciliation_discrepancy_rate=(
            None if not tally.disputed
            else _ratio(tally.disputed, tally.received)),
        first_attempt_delivery_rate=_ratio(tally.delivered_first_attempt,
                                           tally.dispatched),
        postponement_rate=_ratio(tally.postponed, tally.dispatched),
        unassigned_rate=_ratio(tally.unassigned, tally.ready_pool),
        sla_compliance=_ratio(tally.delivered_within_sla, tally.delivered),
        sla_expiry_rate=_ratio(tally.sla_expired, tally.delivered + tally.rejected
                               + tally.defective + tally.sla_expired),
        distance_per_envelope_m=(None if not tally.distance_m
                                 else _ratio(tally.distance_m, tally.delivered)),
        envelopes_per_bike=_ratio(tally.delivered, tally.bikes_deployed),
        cancellation_rate=_ratio(tally.cancelled, tally.ready_pool),
        solver_seconds=(None if not tally.solver_seconds else tally.solver_seconds))
