"""The internal functions both the jobs and the §13.3 schedulers call.

§13 says handlers hold no stage logic, and §13.3 says the scheduled processes
"call the same internal functions as the endpoints; they do not call the API
over HTTP". This module is those functions: it adapts stored records into the
shapes `ddn.lastmile`, `ddn.linehaul`, `ddn.returns` and `ddn.simulation`
already take, and adapts their results back into §9.2.

Adapting is all it does. Every decision -- which envelopes are left, which van
goes where, what counts as a violation -- belongs to the module being called,
and if a rule appears here that is a rule in the wrong place.

**Travel times are required, never invented.** §5.4 and §5.5 are routing, and a
matrix comes from the gateway. A run asked for without one is refused with a
reason rather than served on a guessed speed -- the same refusal
`pickups.dispatch` and `ddn.simulation` already make.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import Any

from vrp.model import TravelMatrix
from vrp.solve.pyvrp_adapter import solve
from vrp.verify import verify

from ddn import contract, linehaul, returns
from ddn import solver_adapter as sa
from ddn.allocation import EFFECTIVE_PER_BIKE, allocate, place
from ddn.lastmile import select
from ddn.simulation import render, run_day, run_days


class MissingTravel(ValueError):
    """§5.4 cannot be run on a guessed speed, and this service guesses none."""


def _matrix(payload: dict[str, Any], expected: int) -> TravelMatrix:
    raw = payload.get("matrix")
    if not raw:
        raise MissingTravel(
            "a routing run needs travel times; supply `matrix` from the "
            "gateway. §5.4 is routing and this service invents no speed")
    matrix = TravelMatrix(
        version=raw.get("version", "api"),
        durations=tuple(tuple(row) for row in raw["durations"]),
        distances=tuple(tuple(row) for row in raw["distances"]))
    if matrix.size != expected:
        raise MissingTravel(
            f"matrix spans {matrix.size} locations for {expected - 1} stops "
            "and one facility; it must span exactly the facility and every stop")
    return matrix


def deliver(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.4 for one facility: select, solve, and report §7.1 (§13.1)."""
    facility = payload["facility"]
    day = date.fromisoformat(payload["day"])
    bikes = payload["bikes"]
    pool = payload["packages"]

    routable, excluded = contract.triage(pool, today=day)
    offered, declined = select(routable, today=day,
                               capacity=len(bikes) * EFFECTIVE_PER_BIKE)
    matrix = _matrix(payload, len(offered) + 1)

    problem = sa.last_mile(facility, offered, bikes, matrix, today=day,
                           model=payload.get("model"))
    solution = solve(problem)
    report = verify(problem, solution)
    violations = sa.check_route_constraints(
        problem, solution, packages=offered, vehicles=bikes, today=day)

    plan = sa.from_solver(solution, problem, excluded=(*excluded, *declined),
                          verified=not report.violations)
    return {
        "facility_id": facility["id"],
        "day": day.isoformat(),
        "status": solution.status,
        "routes": [asdict(route) for route in plan.routes],
        "unassigned": [asdict(u) for u in plan.unassigned],
        "violations": [asdict(v) for v in violations],
    }


def linehaul_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.3: which van goes where, and what stays behind with a reason.

    §13.1 asks every routing result to carry its §7.1 list, and this one
    returned `[]` as a literal -- a clean bill for a plan nothing had read.
    `check_day_constraints` is what reads it: the combined-load bullet is a
    property of a leg, which is exactly what a line-haul plan is made of, and
    `Leg.overloaded` was being computed and never asserted.

    `van_back_at` is deliberately not supplied. §4.3 makes `linehaul_release_at`
    the time a van is back **and unloaded**, so feeding it to a check that adds
    the unload itself would manufacture a violation on every trip. That bullet
    is honestly unchecked here rather than dishonestly checked, which is what
    the `Day` docstring means by "anything absent is not claimed to have been".
    """
    plan = linehaul.plan(payload["facilities"], payload["envelopes"],
                         payload["vans"],
                         unload_seconds=payload["unload_seconds"],
                         transfers=payload.get("transfers", ()),
                         returning=payload.get("returning", ()))
    violations = sa.check_day_constraints(sa.Day(
        today=date.fromisoformat(payload["day"]),
        linehaul=plan,
        transfers=payload.get("transfers", ()),
        unload_seconds=payload["unload_seconds"]))
    return {"day": payload["day"],
            "trips": [asdict(trip) for trip in plan.trips],
            "rolled": {k: list(v) for k, v in plan.rolled.items()},
            "reasons": dict(plan.reasons),
            # §9.2's fourth clause: "transfers not carried, with reason".
            "declined": [asdict(d) for d in plan.declined],
            "carried": plan.carried, "held": plan.held,
            "violations": [asdict(v) for v in violations]}


def return_run(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.5: the evening run, out from the hub and back."""
    stops = returns.sites(payload["envelopes"])
    hub = payload["hub"]
    matrix = _matrix(payload, len(stops) + 1)
    crew = payload.get("vehicles") or returns.fleet(
        payload.get("vehicle_ids", ["VAN-01"]),
        shift_start=hub["shift_start"], shift_end=hub["shift_end"])

    problem = returns.to_problem(
        hub, stops, crew, matrix,
        model=payload.get("model"))
    solution = solve(problem)
    violations = sa.check_route_constraints(problem, solution, vehicles=crew)
    return {
        "status": solution.status,
        "stops": [asdict(stop) for stop in stops],
        "routes": [{"vehicle_id": r.vehicle_id,
                    "stops": [s.location_id for s in r.steps]}
                   for r in solution.routes],
        "violations": [asdict(v) for v in violations],
    }


def allocation_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """§4.2, and §7.2's relocation cost reported rather than hidden.

    **No violation list, deliberately.** §13.1 asks for one on every *routing*
    result, and §4.2 is not one -- it "is a planning decision made before
    routing", and the VRP solver routes each facility afterwards. Read §7.1's
    thirteen bullets against what this returns and every one of them predicates
    over something that is not here: routes, loads, legs, stops, departures,
    packages. An empty list would say "checked and clean" about a stage with
    nothing checkable, which is the claim this repository has just spent a
    commit removing from three other endpoints.

    Allocation's own failure mode is not §7.1: over-commitment raises in
    `place`, and §7.2's relocation cost is soft and reported as `moves`.
    """
    targets = allocate(payload["pools"], len(payload["bikes"]))
    plan = place(targets, payload["bikes"], previous=payload.get("previous") or {})
    return {"day": payload["day"], "targets": targets, "moves": plan.moves,
            "allocations": [asdict(a) for a in plan.allocations]}


def simulate(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.6 and §10: one day or several, sampling fixed by the seed."""
    from ddn.api.inputs import day_inputs

    state, kwargs = day_inputs(payload)
    seed = int(payload.get("seed", 0))
    days = int(payload.get("days", 1))

    if days > 1:
        reports = run_days(days, state, seed=seed, **kwargs)
        return {"days": [_report(r) for r in reports],
                "violations": _violations(reports)}
    report, tomorrow = run_day(state, seed=seed, **kwargs)
    return {"days": [_report(report)],
            "tomorrow_pool": tomorrow.pool_size,
            "report": render(report),
            "violations": _violations([report])}


def _violations(reports: Sequence[Any]) -> list[dict[str, Any]]:
    """§7.1 across every simulated day, as §13.1 asks for it.

    `run_day` decides what a violation is, because that is where the night's
    plan and the vans' return times still exist as objects rather than counts.
    This only carries the answer out.
    """
    return [asdict(v) for report in reports for v in report.violations]


def _report(report: Any) -> dict[str, Any]:
    """A `DayReport` as JSON, §11's metrics and §8.3's checks included."""
    checks = report.checks
    return {
        "day": report.day.isoformat(),
        "tally": asdict(report.tally),
        "metrics": asdict(report.metrics),
        "outcomes": dict(report.outcomes),
        "postponed_reasons": dict(report.postponed_reasons),
        "positioned": dict(report.positioned),
        "rolled": dict(report.rolled),
        # §5.1.6 and §5.3: the two ways a Ready envelope fails to be
        # positioned. Without them the totals do not close and a reader
        # cannot tell a cut-off from a missing van.
        "uncollected": report.uncollected,
        "rolled_envelopes": report.rolled_envelopes,
        "unassigned": len(report.unassigned),
        "return_stops": report.return_stops,
        "carried_into_tomorrow": report.carried_into_tomorrow,
        "moves": report.allocation.moves,
        "checks": {c.name: {"available": c.available, "required": c.required,
                            "unit": c.unit, "binds": c.binds}
                   for c in checks.all},
        "binding": checks.binding.name if checks.binding else None,
    }
