"""§5.4's last mile: one static batch per facility — `Stage 4`.

v0.10's one-day lag is what makes this shape possible. Pickup, hub processing,
sorting and line-haul all happen on day D; *all* delivery happens on D+1
(§5, §3.1). So by the time these routes are planned the pool is closed and
known, every envelope is already at the facility that will dispatch it, and
the problem is a static capacitated VRP per facility rather than anything
dynamic. §5.4 says so directly: "solved independently per facility".

**Two stages, because the solver cannot do it in one.** §4.2 offers an
integrated formulation where a vehicle's home facility is a decision variable;
`docs/solver-capabilities.md` shows it cannot be expressed --
`Vehicle.start_location_id` is fixed before the solve -- so the two-stage
approach §4.2 recommends is the only one, and this module is that shape:
allocate bikes to facilities, then route each facility independently.

**Allocation is proportional to demand, not to priority-weighted demand.**
§4.2 suggests either. Proportional-to-count is what
`allocation.EFFECTIVE_PER_BIKE` supports: the binding constraint is shift time (§7.4), which a low-priority
envelope consumes exactly as much of as an urgent one. Weighting by priority
would allocate capacity to where the valuable work is and leave the cheap work
unserved *in the same places every day*, which §7.2 already penalises through
"postponed packages not retried on the next available day".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from vrp.model import UNREACHABLE, Problem, Solution, TravelMatrix

from ddn import contract
from ddn.contract import Excluded
from ddn.solver_adapter.output import NO_CAPACITY


def reachable_subset(matrix: TravelMatrix, size: int) -> list[int]:
    """Stop indices mutually reachable with the facility and each other.

    Real road data has genuinely unreachable pairs -- an address snapped onto a
    disconnected fragment, a one-way system, an island. `MTX-5` keeps those as
    an explicit sentinel rather than a large number, and `TravelMatrix.duration`
    raises on one rather than returning a guess.

    They must be removed *before* solving. The PyVRP adapter passes the
    sentinel through unexamined, and because `UNREACHABLE` is -1 it is the
    cheapest arc in the matrix, so the solver is actively drawn to unreachable
    stops and visits them first. The verifier then refuses the plan, which is
    the gate working, but it works too late to produce a day's routes.

    Index 0 is the facility. Returns indices into the matrix, facility
    included, so the caller can slice the packages that survive.
    """
    def linked(a: int, b: int) -> bool:
        return (matrix.durations[a][b] != UNREACHABLE
                and matrix.durations[b][a] != UNREACHABLE)

    kept = [i for i in range(1, size) if linked(0, i)]
    changed = True
    while changed:
        changed = False
        for i in list(kept):
            if any(not linked(i, j) for j in kept if j != i):
                kept.remove(i)
                changed = True
                break
    return [0, *kept]


@dataclass(frozen=True)
class FacilityPlan:
    """One facility's day, and what it could not do."""

    facility_id: str
    bikes: int
    offered: int
    problem: Problem
    solution: Solution
    verified: bool

    @property
    def accepted(self) -> bool:
        """Whether this plan may be counted at all.

        `modelcheck` states the rule and the reason: "Only verifier-accepted
        plans score. Asked to serve 120 envelopes with one courier, PyVRP
        returns its best *infeasible* attempt with every arrival clamped to
        noon, and a naive count reports 120 letters delivered by a rider who
        could not have managed forty."

        This module made that mistake on its first run and reported a capacity
        finding from it. A stop on an infeasible route has not been served; it
        is a stop the fleet could not reach, drawn as though it had.
        """
        return self.solution.status == "FEASIBLE" and self.verified

    @property
    def served(self) -> int:
        if not self.accepted:
            return 0
        return len({step.order_id for route in self.solution.routes
                    for step in route.steps if step.order_id})

    @property
    def unassigned(self) -> int:
        return self.offered - self.served

    @property
    def bikes_used(self) -> int:
        if not self.accepted:
            return 0
        return sum(1 for route in self.solution.routes
                   if any(step.order_id for step in route.steps))


def select(packages: Sequence[dict[str, Any]], *, capacity: int,
           today: date) -> tuple[list[dict[str, Any]], tuple[Excluded, ...]]:
    """§8's first-class decision: which envelopes are not served today.

    §8 opens by saying it outright -- "on peak days not all ready envelopes
    will be deliverable; choosing which to leave unassigned is a first-class
    decision" -- and §8.3 expects the gap to be structural rather than
    occasional. This makes the choice explicitly, before the solver sees the
    pool, for two reasons.

    It is **explainable**. §8.1's prize mechanism lets the solver decline work,
    but it declines on prize against distance, so which envelopes fall out
    depends on where they happen to be. A planner asked why an envelope was not
    attempted should hear "it was the lowest-priority one on a facility short
    of twenty bikes", not an objective value.

    And it is **cheaper**: §5.4's own §10 arithmetic is bikes times ~25, so a
    facility offered a fifth more than it can carry does not need those stops
    priced into a matrix first.

    Two groups are never trimmed. §6.1 makes "SLA date = today" a hard
    must-deliver-today constraint rather than a weight, and §8.2 lets
    operations force an envelope into the plan with `locked_vehicle_id`. If
    those alone exceed capacity they all still go: they are constraints, and
    what the fleet genuinely cannot reach comes back from the solver as a
    time-based refusal instead.

    Args:
        packages: the facility's morning pool, §9.1 records.
        capacity: how many envelopes the facility's bikes can serve --
            typically `bikes * allocation.EFFECTIVE_PER_BIKE`.
        today: the delivery date, for the SLA test.

    Returns:
        The pool to offer the solver, in its original order so a matrix built
        over it keeps its indices, and the envelopes declined for want of
        capacity with §9.2's "count" reason.
    """
    forced, optional = [], []
    for package in packages:
        sla = package.get("sla_date")
        if (package.get("locked_vehicle_id")
                or (sla and date.fromisoformat(sla) == today)):
            forced.append(package)
        else:
            optional.append(package)

    # Highest priority first; package id breaks ties so that two runs of one
    # day decline the same twenty envelopes.
    optional.sort(key=lambda p: (-float(p.get("priority", 0)), p["package_id"]))
    room = max(capacity - len(forced), 0)
    kept = {p["package_id"] for p in (*forced, *optional[:room])}

    return ([p for p in packages if p["package_id"] in kept],
            tuple(Excluded(p["package_id"], NO_CAPACITY)
                  for p in optional[room:]))


def plan_facility(facility: dict[str, Any], packages: Sequence[dict[str, Any]],
                  bikes: int, matrix: TravelMatrix, *, today: date,
                  model: dict[str, Any], solve, verify) -> FacilityPlan:
    """Route one facility's pool. The whole of §5.4 for one site.

    Raises:
        ValueError: if the matrix is degraded. `NFR-04` lets a partial matrix
            through so a plan can still be made when the provider is failing,
            and every arc it could not fetch stays `UNREACHABLE` -- which is
            honest, and useless here. A day's routes built on one are not a
            worse plan, they are a plan for a different road network: the
            first attempt at this run silently dropped 1,271 of the hub's
            stops as "no road path" when the truth was that the gateway had
            shed 305 tiles to its own rate limit. Refusing is the only way
            that surfaces as a fact rather than as a capacity finding.
    """
    if matrix.degraded:
        raise ValueError(
            f"{facility['id']}'s travel matrix is degraded and a plan built on "
            f"it would describe a road network that does not exist: "
            f"{matrix.degraded}")
    # §4.1 gives capacity per type and the model owns it, so the synthesised
    # rows are read off the model rather than repeated here. `contract`
    # cross-checks the two and refuses a disagreement, which is a check that
    # could only ever have passed while both copies were edited together.
    declared = next(spec["capacities"] for spec in model["fleet"]
                    if spec["class"] == contract.CLASS_OF["motorbike"])
    vehicles = [{"vehicle_id": f"{facility['id']}-MOTO-{n}",
                 "type": "motorbike", "facility_id": facility["id"],
                 "role": "delivery",
                 "capacity_envelopes": declared[contract.COUNT],
                 "capacity_weight_g": declared[contract.WEIGHT],
                 "shift_start": facility["shift_start"],
                 "shift_end": facility["shift_end"]}
                for n in range(1, bikes + 1)]
    problem = contract.to_problem(facility, packages, vehicles, matrix,
                                  today=today, model=model)
    run = model["run"]
    solution = solve(problem, run["budget"], run["seed"])
    return FacilityPlan(facility_id=facility["id"], bikes=bikes,
                        offered=len(packages), problem=problem,
                        solution=solution, verified=verify(problem, solution).ok)


def expired(envelope: Mapping[str, Any], today: date) -> bool:
    """§6.1's clock: this envelope's SLA date is already behind us.

    Deliberately narrower than `contract.triage`, which applies the same test
    and then also withholds on geocode confidence, status and `excluded_by_ops`.
    Substituting triage here would change the cohort silently, which is the
    kind of swap that looks like a simplification in review.
    """
    raw = envelope.get("sla_date")
    return bool(raw) and date.fromisoformat(raw) < today


def sweep_expired(
        pool: Sequence[Mapping[str, Any]],
        today: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """§6.1's first cut: what may still be dispatched, and what goes back.

    "An envelope past its SLA date is not dispatched at all" -- so this runs
    before §8's capacity decision, not after it. An expired envelope that
    reached `select` would compete for a bike it is not allowed to board, and
    could push a live envelope out to make room for a journey it cannot take.

    Returns both halves because the caller needs both: the expired ones are not
    discarded, they are §5.5's load for tonight.
    """
    live: list[dict[str, Any]] = []
    gone: list[dict[str, Any]] = []
    for envelope in pool:
        (gone if expired(envelope, today) else live).append(dict(envelope))
    return live, gone


def withdraw(packages: Sequence[Mapping[str, Any]],
             cancelled: Iterable[str]) -> tuple[list[dict[str, Any]],
                                                tuple[str, ...]]:
    """§6 v0.15: take withdrawn envelopes out of the pool the next plan uses.

    e2e-3 §2.2: "the driver is notified at the next plan refresh; the stop is
    removed if not yet visited". **The pool is what makes "not yet visited"
    true** -- an envelope that has been visited has an outcome and is no longer
    in it -- so this removes from the pool and does not consult a route. A
    route is a plan for a pool, and re-planning without the stop is how the
    stop comes off.

    It reports what it actually removed rather than what it was asked to.
    A cancellation naming an envelope this facility does not hold changed
    nothing, and an operator who is not told that will believe an envelope is
    stopped when it is on a bike somewhere else. `§5.2.6` refuses the
    already-delivered case separately, in `lifecycle.after_cancellation`,
    because that is a statement about status and not about a pool.

    Order is preserved: §5.4 selects under capacity in pool order, so a
    withdrawal must not resequence what it leaves behind.
    """
    asked = set(cancelled)
    kept = [dict(p) for p in packages if p["package_id"] not in asked]
    removed = tuple(p["package_id"] for p in packages
                    if p["package_id"] in asked)
    return kept, removed
