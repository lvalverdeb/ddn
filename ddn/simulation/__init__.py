"""§5.6, §10 and §11 — the day, run end to end, and what it measures.

`on_road` is the one entry point that needs real geography: it spawns a gateway
and an OSRM graph and routes §5.4 across every facility. Everything else here
runs on whatever travel the caller supplies, so a day can be simulated without
a graph and the tests need neither.
"""

from ddn.simulation.capacity import Check, Checks, check
from ddn.simulation.day import DayReport, Rates, State, run_day, run_days
from ddn.simulation.metrics import Metrics, Tally, measure
from ddn.simulation.report import render

__all__ = ["Check", "Checks", "DayReport", "Metrics", "Rates", "State", "Tally",
           "check", "measure", "render", "run_day", "run_days"]
