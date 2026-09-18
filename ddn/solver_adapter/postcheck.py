"""§7.1's hard constraints, checked against what the solver returned.

CLAUDE.md: "Every solver output must be validated against all of them before it
is accepted. A violation is a failing test, never a warning." This module is
that validation, and it is split in two because §7.1 is.

**Seven bullets are answerable from one solve. Four are not.** "An envelope is
assigned to at most one vehicle *per day*" spans seven facility solves;
"depot-bound envelopes are dispatched only after physical arrival" needs the
line-haul plan; "a van does not depart on line-haul until back from any pickup
route and unloaded" needs the pickup routes *and* the line-haul plan. A single
`check(routes, problem)` would silently pass those three, which is worse than
not offering them -- so `check_route_constraints` takes one solve and
`check_day_constraints` takes the day.

**The platform's verifier is used, not reimplemented.** `vrp.verify` checks
seventeen invariants and is a separate package sharing no code with the solver,
which is exactly the independence a check needs. Duration against the shift
(INV-6), skills (INV-10), release times (INV-17), locks (INV-8) and window
arithmetic (INV-3, INV-4, INV-7) are its work; this module translates what it
reports into §7.1's language and adds the bullets it cannot see, because they
are facts about the operation rather than about the route.

Capacity is the exception, and deliberately: it is checked here as well as
there, because §7.1 states it twice over as a rule about vehicle *types* and a
disagreement between the two readings is worth a failing test.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from vrp.model import Problem, Solution
from vrp.verify import verify

from ddn import assumptions, pickups
from ddn.contract import CLASS_OF, COUNT, READY, WEIGHT
from ddn.linehaul import LinehaulPlan

# §7.1's eleven bullets, transcribed. `tests/test_postcheck.py` reads the
# section and fails if one of these is no longer its words.
MOTORBIKE_CAPACITY = "A motorbike never carries more than 35 envelopes."
VAN_WEIGHT = "A van never carries more than 500 kg."
ROUTE_WITHIN_SHIFT = (
    "A vehicle's route starts and ends at its home facility within its shift "
    "(service time + travel time ≤ shift)."
)
ONE_VEHICLE_PER_DAY = "An envelope is assigned to at most one vehicle per day."
READY_ONLY = "Only ready envelopes are assigned to line-haul or delivery."
AFTER_ARRIVAL = (
    "Depot-bound envelopes are dispatched from a depot only after physical "
    "arrival."
)
VAN_UNLOADED_FIRST = (
    "A van does not depart on line-haul until back from any pickup route and "
    "unloaded."
)
NOT_PAST_SLA = "An envelope is not dispatched for delivery after its SLA date."
PICKUPS_VAN_ONLY = (
    "Mailbags are collected by vans only; motorbikes are never assigned pickup "
    "stops."
)
VAN_MAILBAGS = "A van never carries more than [TBD] mailbags."
BAGS_WHOLE = (
    "Sealed mailbags are collected whole; bags are never split at the customer "
    "site."
)

BULLETS = (
    MOTORBIKE_CAPACITY, VAN_WEIGHT, ROUTE_WITHIN_SHIFT, ONE_VEHICLE_PER_DAY,
    READY_ONLY, AFTER_ARRIVAL, VAN_UNLOADED_FIRST, NOT_PAST_SLA,
    PICKUPS_VAN_ONLY, VAN_MAILBAGS, BAGS_WHOLE,
)

#: Which §7.1 bullet a platform invariant is evidence for. An invariant absent
#: from this table is still reported -- it is a real failure that §7.1 simply
#: does not name.
INVARIANT_BULLET = {
    "INV-6": ROUTE_WITHIN_SHIFT,
    "INV-3": ROUTE_WITHIN_SHIFT,
    "INV-4": ROUTE_WITHIN_SHIFT,
    "INV-7": ROUTE_WITHIN_SHIFT,
    "INV-10": PICKUPS_VAN_ONLY,
    "INV-17": AFTER_ARRIVAL,
    "INV-1": ONE_VEHICLE_PER_DAY,
    "INV-2": ONE_VEHICLE_PER_DAY,
}

#: Not a §7.1 bullet: a plan that names a vehicle the problem does not contain
#: is malformed rather than infeasible, and `vrp.verify` raises on it.
UNKNOWN_VEHICLE = "a route names a vehicle the problem does not contain"

DELIVERY, PICKUP = "delivery", "pickup"


@dataclass(frozen=True, slots=True)
class Violation:
    """One broken bullet, with what broke it."""

    bullet: str
    detail: str
    vehicle_id: str | None = None
    order_id: str | None = None

    def __str__(self) -> str:
        where = " / ".join(x for x in (self.vehicle_id, self.order_id) if x)
        return f"§7.1 {self.bullet} — {self.detail}" + (f" [{where}]" if where else "")


@dataclass(frozen=True)
class Day:
    """One operating day, as the four cross-stage bullets need to see it."""

    today: date
    #: facility id -> that facility's last-mile solution (§5.4).
    solutions: Mapping[str, Solution] = field(default_factory=dict)
    #: §9.1 package records, for status and SLA date.
    packages: Sequence[dict[str, Any]] = ()
    #: §5.3's plan, whose trips carry what arrived where and when.
    linehaul: LinehaulPlan | None = None
    #: van id -> the second it was back at the hub from pickups (§5.1.5).
    van_back_at: Mapping[str, int] = field(default_factory=dict)
    unload_seconds: int = assumptions.FACILITY_UNLOAD_MIN * 60


def _served(solution: Solution) -> dict[str, list[str]]:
    """order id -> the vehicles that served it, in this solution."""
    served: dict[str, list[str]] = {}
    for route in solution.routes:
        for step in route.steps:
            if step.order_id:
                served.setdefault(step.order_id, []).append(route.vehicle_id)
    return served


def _peak(route: Any) -> dict[str, int]:
    peak: dict[str, int] = {}
    for step in route.steps:
        for dimension, amount in step.load_after.items():
            peak[dimension] = max(peak.get(dimension, 0), amount)
    return peak


def _capacity_bullet(dimension: str, is_van: bool) -> str:
    """Which bullet a breach of this dimension breaks.

    §7.1 states capacity per type and per dimension: envelopes bound a
    motorbike, kilograms and mailbags bound a van. A breach on the dimension
    the other type is bounded by is still a breach, and is reported against
    the bullet that vehicle answers to.
    """
    if dimension == pickups.BAGS:
        return VAN_MAILBAGS
    if dimension == COUNT and not is_van:
        return MOTORBIKE_CAPACITY
    if dimension == WEIGHT and is_van:
        return VAN_WEIGHT
    return VAN_WEIGHT if is_van else MOTORBIKE_CAPACITY


def check_route_constraints(
    problem: Problem, solution: Solution, *,
    packages: Sequence[dict[str, Any]] = (),
    vehicles: Sequence[dict[str, Any]] = (),
    today: date | None = None,
    stage: str = DELIVERY,
) -> list[Violation]:
    """The seven §7.1 bullets one solve can answer, plus what the verifier says.

    Args:
        problem: what was put to the solver.
        solution: what it returned.
        packages: the §9.1 records behind the orders, for status and SLA date.
            Without them the two record-level bullets cannot be tested and are
            not claimed to have been.
        vehicles: the §9.1 vehicle records, for `type`. Without them a capacity
            breach is reported against the dimension rather than the bullet.
        today: the planning date, needed for the SLA bullet.
        stage: `"delivery"` (§5.4) or `"pickup"` (§5.1); the mailbag bullets
            only apply to the second.

    Returns:
        Every violation found, in bullet order. Empty means the solve passed
        the bullets that this much information can test.
    """
    found: list[Violation] = []
    by_id = {p["package_id"]: p for p in packages} if packages else {}
    kind_of = {v["vehicle_id"]: v.get("type") for v in vehicles}

    # A plan may name a vehicle the problem never had, and `vrp.verify` raises
    # on it rather than reporting it. A post-check that dies on a malformed
    # plan is no use against the plans it exists to catch, so the strays are
    # reported here and the verifier is not asked about them.
    known = {vehicle.id for vehicle in problem.vehicles}
    stray = sorted({route.vehicle_id for route in solution.routes} - known)
    for vehicle_id in stray:
        found.append(Violation(
            bullet=UNKNOWN_VEHICLE,
            detail=f"is not one of the problem's {len(known)} vehicles",
            vehicle_id=vehicle_id))

    if not stray:
        for reported in verify(problem, solution).violations:
            found.append(Violation(
                bullet=INVARIANT_BULLET.get(
                    reported.invariant,
                    f"platform invariant {reported.invariant}"),
                detail=f"{reported.invariant}: {reported.detail}",
                vehicle_id=reported.vehicle_id, order_id=reported.order_id))

    limits = {vehicle.id: vehicle.capacities for vehicle in problem.vehicles}
    homes = {vehicle.id: (vehicle.start_location_id, vehicle.end_location_id)
             for vehicle in problem.vehicles}

    for route in solution.routes:
        is_van = CLASS_OF.get(kind_of.get(route.vehicle_id, "")) in pickups.COLLECTS
        for dimension, amount in _peak(route).items():
            allowed = limits.get(route.vehicle_id, {}).get(dimension)
            if allowed is not None and amount > allowed:
                detail = f"carries {dimension}={amount}, above its {allowed}"
                if dimension == pickups.BAGS:
                    detail += (f"; the limit is the placeholder "
                               f"MAILBAGS_PER_VAN={assumptions.MAILBAGS_PER_VAN}, "
                               "Open Question 1")
                found.append(Violation(_capacity_bullet(dimension, is_van), detail,
                                       vehicle_id=route.vehicle_id))

        if not route.steps:
            continue
        start, end = homes.get(route.vehicle_id, (None, None))
        first, last = route.steps[0].location_id, route.steps[-1].location_id
        if start is not None and first != start:
            found.append(Violation(
                ROUTE_WITHIN_SHIFT, f"starts at {first}, not its home {start}",
                vehicle_id=route.vehicle_id))
        if end is not None and last != end:
            found.append(Violation(
                ROUTE_WITHIN_SHIFT, f"ends at {last}, not its home {end}",
                vehicle_id=route.vehicle_id))

    for order_id, carried_by in _served(solution).items():
        if len(carried_by) > 1:
            found.append(Violation(
                ONE_VEHICLE_PER_DAY,
                f"served by {', '.join(sorted(set(carried_by)))} in one solve",
                order_id=order_id))

        package = by_id.get(order_id)
        if package is None:
            continue
        status = package.get("status")
        if status is not None and status != READY:
            found.append(Violation(READY_ONLY, f"is {status}, not {READY}",
                                   order_id=order_id))
        raw = package.get("sla_date")
        if today is not None and raw and date.fromisoformat(raw) < today:
            found.append(Violation(
                NOT_PAST_SLA, f"SLA date {raw} is before {today.isoformat()}",
                order_id=order_id))

    if stage == PICKUP:
        found.extend(_pickup_bullets(problem, solution, kind_of))
    return found


def _pickup_bullets(problem: Problem, solution: Solution,
                    kind_of: Mapping[str, str | None]) -> list[Violation]:
    """§7.1's three mailbag bullets, which only a §5.1 solve can break."""
    found: list[Violation] = []
    for route in solution.routes:
        declared = kind_of.get(route.vehicle_id)
        if declared is not None and CLASS_OF.get(declared) not in pickups.COLLECTS:
            found.append(Violation(
                PICKUPS_VAN_ONLY,
                f"is a {declared} and was given {sum(1 for s in route.steps if s.order_id)} "
                "pickup stops", vehicle_id=route.vehicle_id))

    for order in problem.orders:
        count = order.quantities.get(pickups.BAGS)
        if count is not None and count != 1:
            found.append(Violation(
                BAGS_WHOLE, f"is {count} bags in one order, so it can be part-collected",
                order_id=order.id))

    for order_id, carried_by in _served(solution).items():
        if len(carried_by) > 1:
            found.append(Violation(
                BAGS_WHOLE, f"collected by {', '.join(sorted(set(carried_by)))}",
                order_id=order_id))
    return found


def check_day_constraints(day: Day) -> list[Violation]:
    """The four §7.1 bullets that need more than one solve to test.

    Args:
        day: the day's solves and plans. Anything absent is not checked and is
            not claimed to have been -- a `Day` with no line-haul plan cannot
            test arrival ordering, and says so by returning nothing for it.

    Returns:
        Every violation found across the day.
    """
    found: list[Violation] = []
    by_id = {p["package_id"]: p for p in day.packages}

    carried: dict[str, list[str]] = {}
    for facility_id, solution in day.solutions.items():
        for order_id, vehicles in _served(solution).items():
            carried.setdefault(order_id, []).extend(
                f"{facility_id}/{vehicle}" for vehicle in vehicles)

    for order_id, places in sorted(carried.items()):
        if len(places) > 1:
            found.append(Violation(
                ONE_VEHICLE_PER_DAY,
                f"served {len(places)} times today, by {', '.join(sorted(places))}",
                order_id=order_id))

        package = by_id.get(order_id)
        if package is None:
            continue
        status = package.get("status")
        if status is not None and status != READY:
            found.append(Violation(READY_ONLY, f"is {status}, not {READY}",
                                   order_id=order_id))
        raw = package.get("sla_date")
        if raw and date.fromisoformat(raw) < day.today:
            found.append(Violation(
                NOT_PAST_SLA,
                f"SLA date {raw} is before {day.today.isoformat()}", order_id=order_id))

    if day.linehaul is not None:
        found.extend(_arrival_bullets(day))
        found.extend(_van_release_bullets(day))
    return found


def _arrival_bullets(day: Day) -> list[Violation]:
    """§7.1: a depot dispatches an envelope only after the van brought it."""
    assert day.linehaul is not None
    arrived: dict[str, tuple[str, int]] = {}
    for trip in day.linehaul.trips:
        for package_id in trip.package_ids:
            arrived[package_id] = (trip.destination, trip.arrival)

    found: list[Violation] = []
    for facility_id, solution in day.solutions.items():
        # §3.3: the hub dispatches its own envelopes and line-hauls nothing to
        # itself, so only a depot's routes have an arrival to wait for.
        if facility_id == "HUB":
            continue
        for route in solution.routes:
            for step in route.steps:
                if not step.order_id:
                    continue
                landed = arrived.get(step.order_id)
                if landed is None:
                    found.append(Violation(
                        AFTER_ARRIVAL,
                        f"dispatched from {facility_id} but no line-haul trip carried it",
                        vehicle_id=route.vehicle_id, order_id=step.order_id))
                elif step.start_service < landed[1]:
                    found.append(Violation(
                        AFTER_ARRIVAL,
                        f"dispatched at {step.start_service} but arrived at "
                        f"{landed[1]} on the {landed[0]} run",
                        vehicle_id=route.vehicle_id, order_id=step.order_id))
    return found


def _van_release_bullets(day: Day) -> list[Violation]:
    """§7.1: a van on pickups departs for a depot only once back and unloaded."""
    assert day.linehaul is not None
    found: list[Violation] = []
    for trip in day.linehaul.trips:
        back = day.van_back_at.get(trip.van_id)
        if back is None:
            continue
        earliest = back + day.unload_seconds
        if trip.departure < earliest:
            found.append(Violation(
                VAN_UNLOADED_FIRST,
                f"departs for {trip.destination} at {trip.departure}, back from "
                f"pickups at {back} and unloading takes {day.unload_seconds}s "
                f"(placeholder FACILITY_UNLOAD_MIN="
                f"{assumptions.FACILITY_UNLOAD_MIN}), so not before {earliest}",
                vehicle_id=trip.van_id))
    return found
