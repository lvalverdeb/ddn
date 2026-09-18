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

from collections.abc import Callable
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


def _priced(model: dict[str, Any] | None, payload: dict[str, Any], *,
            default: Callable[[], dict[str, Any]] = contract.load_model
            ) -> dict[str, Any]:
    """A model, with §8's unanswered objective made explicit.

    `default` is required rather than assumed: §5.4 and §5.5 are different
    operations with different service times and different fleets, and falling
    back to the delivery model for a return run asks the wrong file what a stop
    costs.

    `models/ddn-lastmile.json` prices a vehicle at 50,000 against prizes of at
    most 1,999, so on a small pool the solver declines everything. That is §8's
    open cost ratio, not a solver setting, and a caller may override it for a
    run -- but the override is theirs to state, never this module's to assume.
    """
    model = model or default()
    if "vehicle_fixed_cost" in payload:
        model = dict(model, run=dict(
            model["run"],
            objective=dict(model["run"]["objective"],
                           vehicle_fixed_cost=payload["vehicle_fixed_cost"])))
    return model


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
                           model=_priced(payload.get("model"), payload))
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
    """§5.3: which van goes where, and what stays behind with a reason."""
    plan = linehaul.plan(payload["facilities"], payload["envelopes"],
                         payload["vans"],
                         unload_seconds=payload["unload_seconds"])
    return {"day": payload["day"],
            "trips": [asdict(trip) for trip in plan.trips],
            "rolled": {k: list(v) for k, v in plan.rolled.items()},
            "reasons": dict(plan.reasons),
            "carried": plan.carried, "held": plan.held,
            "violations": []}


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
        model=_priced(payload.get("model"), payload,
                      default=returns.load_model))
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
    """§4.2, and §7.2's relocation cost reported rather than hidden."""
    targets = allocate(payload["pools"], len(payload["bikes"]))
    plan = place(targets, payload["bikes"], previous=payload.get("previous") or {})
    return {"day": payload["day"], "targets": targets, "moves": plan.moves,
            "allocations": [asdict(a) for a in plan.allocations],
            "violations": []}


def simulate(payload: dict[str, Any]) -> dict[str, Any]:
    """§5.6 and §10: one day or several, sampling fixed by the seed."""
    from ddn.api.inputs import day_inputs

    state, kwargs = day_inputs(payload)
    seed = int(payload.get("seed", 0))
    days = int(payload.get("days", 1))

    if days > 1:
        reports = run_days(days, state, seed=seed, **kwargs)
        return {"days": [_report(r) for r in reports], "violations": []}
    report, tomorrow = run_day(state, seed=seed, **kwargs)
    return {"days": [_report(report)],
            "tomorrow_pool": tomorrow.pool_size,
            "report": render(report),
            "violations": []}


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
        "unassigned": len(report.unassigned),
        "return_stops": report.return_stops,
        "carried_into_tomorrow": report.carried_into_tomorrow,
        "moves": report.allocation.moves,
        "checks": {c.name: {"available": c.available, "required": c.required,
                            "unit": c.unit, "binds": c.binds}
                   for c in checks.all},
        "binding": checks.binding.name if checks.binding else None,
    }
