"""E2E-3, run: one facility's pool, attempted, and what each outcome leaves.

`docs/e2e/e2e-3-depot-delivery-and-outcomes.md` §1 gives the boundary — it ends
when every dispatched envelope has an outcome recorded, its next state
assigned, and the facility's end-of-day hand-offs produced. This file is that
sequence.

**It calls; it does not compute.** §6.1's expiry sweep is `lastmile`'s, §8's
capacity cut is `lastmile.select`'s, the routing is the injected `deliver`,
and §6's consequences are `model.outcomes.record`'s. Nothing is decided here.

**Outcomes arrive; they are not drawn.** e2e-3 §3 lists them under the driver
app (§13.2) and puts outcome *rates* under "(simulation only)". That is also
what keeps this package free of any `ddn.simulation` import: a slice that drew
its own outcomes would need the simulator's `Rates`, and the chain would then
be comparing the simulator against itself.

What e2e-3 asks for and this does not produce: transfer requests (§4 row 5),
which §5.3.2 raises from a *re-geocode* the pool does not carry — row C7 owns
it; and the §7.1 violation list of §4 row 1, which needs the facility's
`Solution`, and the injected `deliver` returns envelopes rather than routes.
Both are left empty rather than filled with something that would read as
checked.
"""

from __future__ import annotations

from ddn import lastmile
from ddn.allocation import capacity_for
from ddn.e2e.depot_delivery.scenario import Scenario
from ddn.e2e.handoff import DayOutcomes, PositionedPool
from ddn.model import outcomes

__all__ = ["run"]


def run(scenario: Scenario, positioned: PositionedPool) -> DayOutcomes:
    """One facility's delivery day, from its morning pool to its hand-offs."""
    live, swept = lastmile.sweep_expired(
        positioned.at(scenario.facility_id),
        scenario.delivery_day)
    offered, declined = lastmile.select(
        live,
        capacity=capacity_for(scenario.bikes),
        today=scenario.delivery_day)
    attempted, refused = scenario.deliver(offered, scenario.facility_id,
                                          scenario.bikes)
    recorded = outcomes.record(attempted, swept,
                               outcome=scenario.outcome,
                               reason=scenario.reason,
                               retryable=scenario.retryable)
    return DayOutcomes.of(recorded, declined, refused,
                          pool=live, swept=swept,
                          bikes=scenario.bikes,
                          facility_id=scenario.facility_id,
                          day=scenario.delivery_day)
