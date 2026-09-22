"""E2E-2, run: one night of circuits, and where every envelope wakes up.

`docs/e2e/e2e-2-hub-to-depots.md` §1 gives the boundary — it starts when an
envelope becomes Ready at the hub, and ends when every Ready envelope is either
**positioned** at its dispatching facility before that facility's morning route
release, or explicitly **rolled** with a reason. This file is that sequence.

**It calls; it does not compute.** The keep-or-transport decision is
`linehaul.depot_bound`'s, the circuits are `linehaul.plan`'s, taking back what
did not travel is `linehaul.strip_rolled`'s, and §7.1's cross-stage half is
`check_day_constraints`'. Nothing is decided here.

§5.3.2's transfers are planned, not solved: `docs/solver-capabilities.md`
confirms no shipment support, so `linehaul` uses the documented fallback and
this slice does not reach for a solver it was told it does not have.

What e2e-2 §5 asks for and this does not produce: rebalancing proposals (row 5,
Task 16 owns them) and van-hours used against available (row 7, §8.3's check,
which `simulation.capacity` computes over a whole day rather than a night).
"""

from __future__ import annotations

from ddn import linehaul
from ddn.e2e.handoff import PositionedPool, ReadyPool, TransferOutcomes
from ddn.e2e.hub_to_depots.scenario import Scenario
from ddn.solver_adapter import Day, check_day_constraints

__all__ = ["run"]


def run(scenario: Scenario,
        ready: ReadyPool) -> tuple[PositionedPool, TransferOutcomes]:
    """One night, from the ready pool to tomorrow's positioned pool."""
    night = linehaul.plan(
        linehaul.depots(scenario.facilities, hub_id=scenario.hub_id),
        linehaul.depot_bound(ready.ready, hub_id=scenario.hub_id),
        scenario.vans,
        transfers=scenario.transfers,
        returning=scenario.returning,
        transit=scenario.transit,
        unload_seconds=scenario.unload_seconds,
    )
    positioned = linehaul.strip_rolled(ready.ready, night,
                                       hub_id=scenario.hub_id)
    violations = check_day_constraints(Day(
        today=scenario.collection_day,
        linehaul=night,
        transfers=scenario.transfers,
        van_back_at=scenario.van_back_at,
        unload_seconds=scenario.unload_seconds,
    ))
    return (PositionedPool.of(positioned, night, violations,
                              day=scenario.delivery_day,
                              held=scenario.straddling),
            TransferOutcomes.of(night))
