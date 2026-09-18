"""§9.1 in, §9.2 out, and §7.1 checked on the way back.

`to_solver` is `last_mile` and `pickup`; `from_solver` produces §9.2's records;
`check_route_constraints` and `check_day_constraints` are §7.1's eleven bullets
split by how much of the day each needs to see.

`ddn.contract` is still where §5.4's mapping lives -- this package delegates to
it rather than restating it, and CLAUDE.md's architecture table records that
`solver_adapter/` and `contract.py` are the same boundary until it is moved.
"""

from ddn.solver_adapter.output import (
    IN_DISPUTE,
    NO_CAPACITY,
    NO_TIME,
    REASONS,
    Plan,
    Route,
    Stop,
    Unassigned,
    from_solver,
)
from ddn.solver_adapter.postcheck import (
    BULLETS,
    Day,
    Violation,
    check_day_constraints,
    check_route_constraints,
)
from ddn.solver_adapter.problem import last_mile, load_pickup_model, pickup

__all__ = [
    "BULLETS",
    "IN_DISPUTE",
    "NO_CAPACITY",
    "NO_TIME",
    "REASONS",
    "Day",
    "Plan",
    "Route",
    "Stop",
    "Unassigned",
    "Violation",
    "check_day_constraints",
    "check_route_constraints",
    "from_solver",
    "last_mile",
    "load_pickup_model",
    "pickup",
]
