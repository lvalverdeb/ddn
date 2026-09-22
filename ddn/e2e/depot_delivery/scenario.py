"""What E2E-3 needs beyond E2E-2's hand-off.

§3 of `docs/e2e/e2e-3-depot-delivery-and-outcomes.md` is the inputs table. Its
first row — the positioned envelopes — arrives as a `PositionedPool`; this is
the rest, for one facility.

The same slice runs at the hub and at a depot. Which one it is decides where
tonight's returns go (§2.5), and that is the only difference: `facility_id`
against `hub_id`, read at the boundary, not branched on inside a stage.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class Scenario:
    """One facility's delivery day, as e2e-3 §3's table."""

    facility_id: str
    #: §3.1's one-day lag: this pool was made ready yesterday (§5.2.6).
    delivery_day: date
    #: Motorbikes at this facility. e2e-3 §1 puts §4.2's allocation *outside*
    #: the boundary, so this arrives decided rather than being computed here.
    bikes: int
    #: §5.4's routing: offered envelopes -> (attempted, refused for time).
    #: No default — the non-routing stand-in belongs in a test, never here,
    #: where it would be a solver mocked outside `tests/`.
    deliver: Callable[[Sequence[dict[str, Any]], str, int],
                      tuple[list[dict[str, Any]], list[dict[str, Any]]]]
    #: §6's outcome for one envelope, from the driver app (§13.2). Called once
    #: per attempted envelope, in the order they were attempted.
    outcome: Callable[[Mapping[str, Any]], str]
    #: §6's sub-reason for a postponement. Called immediately after `outcome`
    #: and only when it was one — the order is `outcomes.record`'s contract.
    reason: Callable[[Mapping[str, Any]], str]
    hub_id: str = "HUB"
