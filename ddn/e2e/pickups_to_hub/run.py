"""E2E-1, run: §5.1 collects, §5.2 times, and the pool is handed on.

`docs/e2e/e2e-1-pickups-to-hub.md` §1 gives the boundary — a customer declares
a bag ready, and the slice ends when every envelope in the collected bags is
Ready, Held, or Rolled. This file is that sequence and nothing else.

**It calls; it does not compute.** No loop, no comprehension, no condition, no
comparison. Every decision below belongs to a §5 module and is made there:
admission to `pickups`, readiness to `processing`, the loss count to
`pickups.uncollected`, the shape of the hand-off to `ReadyPool.of`.
`tests/e2e/test_run_purity.py` enforces that syntactically, which is a weaker
guarantee than it sounds — see its docstring — so the rule is also written
here, where someone editing this file will read it.

What the slice document asks for and nothing here produces: the held envelopes
of §4 row 4 (disputed, low-confidence, straddling) and the rolled assembly set
of §4 row 6. No function in `ddn/` computes either yet — `processing.schedule`
applies no cut-off at all, and §5.2.3's queue ordering arrived in Task 12
without an overflow rule. Rows A3, A4 and A6 own them. They are left at their
defaults rather than filled with something plausible.
"""

from __future__ import annotations

from ddn import processing
from ddn.e2e.handoff import ReadyPool
from ddn.e2e.pickups_to_hub.scenario import Scenario
from ddn.pickups import run as collect

__all__ = ["run"]


def run(scenario: Scenario) -> ReadyPool:
    """One pickup day, from declared bags to a ready-by-facility pool."""
    dispatch = collect(
        scenario.requests,
        scenario.vans,
        scenario.hub,
        travel=scenario.travel,
        cut_off=scenario.cut_off,
    )
    ready_at = processing.ready_times(dispatch, scenario.inflow)
    positioned = processing.at_facilities(scenario.facilities)
    processing.position(positioned, ready_at, scenario.requests, scenario.inflow)
    return ReadyPool.of(dispatch, positioned, scenario.inflow,
                        day=scenario.collection_day)
