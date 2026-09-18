"""`from_solver`: a `Solution` as §9.2's output.

§9.2 asks for four things. Three are produced here -- routes with ETAs and
stop types, the unassigned list with a reason from its own vocabulary, and the
line-haul plan, which `ddn.linehaul.Trip` already is and is therefore carried
through rather than rebuilt. The fourth, fleet allocation, is marked "*if
solved by the tool*" and is not: `docs/solver-capabilities.md` found that a
vehicle's depot is fixed before the solve, so §4.2 is a planning step upstream
(`allocation/`) and has no solver output to report.

**An unassigned envelope is rarely in `Solution.unassigned`.** Measured: a pool
of six offered to one bike with room for two comes back `INFEASIBLE` with an
*empty* unassigned tuple -- PyVRP returns its best infeasible attempt rather
than declining work, and only declines when prizes make declining optimal. So
the unassigned list is computed as offered-minus-served, and an unaccepted plan
serves nothing at all. `ddn.lastmile.FacilityPlan.accepted` states that rule and
the capacity finding this repository once published by ignoring it: a stop on an
infeasible route was not served, it is a stop the fleet could not reach.

**"time" versus "count" is inferred, not reported.** §9.2's vocabulary splits
the two and the solver says neither. A route whose peak load reaches a capacity
limit was stopped by the load, and §7.4's arithmetic makes duration the usual
binding constraint, so "time" is the default and "count" is claimed only where
a dimension is actually full.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from vrp.model import Problem, Solution

from ddn.contract import (
    LOW_GEOCODE_CONFIDENCE,
    NOT_READY,
    SLA_EXPIRED,
    Excluded,
)
from ddn.linehaul import Trip

#: §9.2's two post-solve reasons, beside the three `ddn.contract` decides
#: before any routing happens.
NO_TIME = "time"
NO_CAPACITY = "count"
#: §9.2 lists it; §5.2.6 gives it no state, so nothing here can tell a disputed
#: envelope from the rest of the pipeline. Named so the vocabulary is complete.
IN_DISPUTE = "in dispute"

REASONS = frozenset({NO_TIME, NO_CAPACITY, NOT_READY, LOW_GEOCODE_CONFIDENCE,
                     SLA_EXPIRED, IN_DISPUTE})

#: `Step.type` as the platform spells it, in §9.2's vocabulary.
STOP_KIND = {"DELIVERY": "delivery", "PICKUP": "pickup",
             "START": "facility", "END": "facility"}


@dataclass(frozen=True, slots=True)
class Stop:
    """One stop of a route: what it is, what it serves, and when."""

    kind: str
    location_id: str
    eta: int
    service_start: int
    departure: int
    order_id: str | None = None


@dataclass(frozen=True, slots=True)
class Route:
    """§9.2: "vehicle_id; ordered stops ... total distance and time"."""

    vehicle_id: str
    stops: tuple[Stop, ...]
    distance_m: int
    duration_s: int

    @property
    def order_ids(self) -> tuple[str, ...]:
        return tuple(s.order_id for s in self.stops if s.order_id)


@dataclass(frozen=True, slots=True)
class Unassigned:
    """§9.2: "package_id; reason"."""

    package_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class Plan:
    """§9.2's output for one stage of one day."""

    routes: tuple[Route, ...] = ()
    unassigned: tuple[Unassigned, ...] = ()
    linehaul: tuple[Trip, ...] = ()

    @property
    def served(self) -> int:
        return len({oid for route in self.routes for oid in route.order_ids})


def _leg_distance(problem: Problem, previous: str, current: str) -> int:
    index = {location.id: location.matrix_index for location in problem.locations}
    return problem.matrix.distances[index[previous]][index[current]]


def _route(problem: Problem, route: Any) -> Route:
    stops = tuple(
        Stop(kind=STOP_KIND.get(step.type, step.type.lower()),
             location_id=step.location_id, eta=step.arrival,
             service_start=step.start_service, departure=step.departure,
             order_id=step.order_id)
        for step in route.steps
    )
    distance = sum(
        _leg_distance(problem, before.location_id, after.location_id)
        for before, after in zip(route.steps, route.steps[1:], strict=False)
    )
    duration = route.steps[-1].departure - route.steps[0].arrival if route.steps else 0
    return Route(vehicle_id=route.vehicle_id, stops=stops,
                 distance_m=distance, duration_s=duration)


def _full(problem: Problem, solution: Solution) -> bool:
    """Whether any route reached a capacity limit: §9.2's "count" rather than "time"."""
    limits = {vehicle.id: vehicle.capacities for vehicle in problem.vehicles}
    for route in solution.routes:
        for step in route.steps:
            for dimension, amount in step.load_after.items():
                if amount >= limits.get(route.vehicle_id, {}).get(dimension, 1 << 62):
                    return True
    return False


def from_solver(solution: Solution, problem: Problem, *,
                offered: Sequence[str] = (),
                excluded: Sequence[Excluded] = (),
                verified: bool = True,
                linehaul: Sequence[Trip] = ()) -> Plan:
    """§9.2's output for a solved stage.

    Args:
        solution: what the solver returned.
        problem: the problem it answered, for the matrix and the capacities.
        offered: the order ids put to the solver, so that what it could not
            place can be named. Defaults to the problem's own orders.
        excluded: what `ddn.contract.triage` refused before the solve, with the
            §9.2 reason it refused it for.
        verified: whether `vrp.verify` accepted the solution. An unverified or
            infeasible plan serves nothing; see the module docstring.
        linehaul: §9.2's line-haul plan, as `ddn.linehaul` produced it.

    Returns:
        The routes, the unassigned list with reasons, and the line-haul plan.
    """
    accepted = solution.status == "FEASIBLE" and verified
    routes = tuple(_route(problem, route) for route in solution.routes) if accepted else ()

    pool = tuple(offered) or tuple(order.id for order in problem.orders)
    served = {order_id for route in routes for order_id in route.order_ids}
    reason = NO_CAPACITY if accepted and _full(problem, solution) else NO_TIME

    unassigned = tuple(Unassigned(item.package_id, item.reason) for item in excluded)
    unassigned += tuple(Unassigned(order_id, reason)
                        for order_id in pool if order_id not in served)

    return Plan(routes=routes, unassigned=unassigned, linehaul=tuple(linehaul))
