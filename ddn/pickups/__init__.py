"""§5.1 — mailbag pickups, van-only, arising through the day.

`admission` answers which van may take a bag; `dispatch` runs the day, holding
visited stops and re-optimising on a cadence. The split is §5.1's own: §5.1.4
is "only a question of which van", and §5.1.5 is the route it goes on.
"""

from ddn.pickups.admission import (
    BAGS,
    COLLECTS,
    WEIGHT,
    Assignment,
    assign,
    can_take,
    load,
)
from ddn.pickups.dispatch import (
    Dispatch,
    Incident,
    Visit,
    run,
    uncollected,
)

__all__ = ["BAGS", "COLLECTS", "WEIGHT", "Assignment", "Dispatch", "Incident",
           "Visit", "assign", "can_take", "load", "run", "uncollected"]
