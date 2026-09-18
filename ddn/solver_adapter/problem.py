"""`to_solver`: the operation's records as a `Problem`, per stage.

§5.4 is not rebuilt here. `ddn.contract` already maps a facility's last mile,
and its docstring carries the reasoning -- priority as tier plus prize, the SLA
clock as a constraint rather than a weight, capacity cross-checked between
model and record with neither silently preferred. A second translator would be
the "two sources for one fact" that module exists to avoid, so `last_mile` is a
delegation and nothing more.

§5.1 is built here, because nothing built it before: `ddn.pickups` decides
*which van may take a bag* and stops there, which is admission rather than
routing.

**The order is a bag, not a site.** §5.1.4 allows a site with more bags than one
van can take to be "split across vans", so bags at one site must be able to
land on different routes; one order per site would make that inexpressible.
Bags sharing a site therefore share their coordinates and produce identical
matrix rows, which is the price of being able to split them.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from vrp import servicemodel
from vrp.model import Problem, TravelMatrix

from ddn import contract, pickups
from ddn.contract import Band, as_depot

MODELS = Path(__file__).resolve().parent.parent.parent / "models"
PICKUP_MODEL = "ddn-pickup"


def load_pickup_model(name: str = PICKUP_MODEL) -> dict[str, Any]:
    """The pickup model, read by path for the reason `contract.load_model` gives."""
    return json.loads((MODELS / f"{name}.json").read_text())


def last_mile(facility: dict[str, Any], packages: Sequence[dict[str, Any]],
              vehicles: Sequence[dict[str, Any]], matrix: TravelMatrix, *,
              today: date, model: dict[str, Any] | None = None,
              bands: Sequence[Band] = contract.BANDS) -> Problem:
    """§5.4, one facility. See `ddn.contract.to_problem`, which does the work."""
    return contract.to_problem(facility, packages, vehicles, matrix,
                               today=today, model=model, bands=bands)


def as_bag_record(request: dict[str, Any]) -> dict[str, Any]:
    """A §9.1 mailbag record in the shape `servicemodel.build` expects.

    `pickups.load` already states what a bag puts on a van -- one bag, its
    expected grams, and the envelope count kept out of the routing quantities
    because §4.1 collects bags whole. This adds only the `id` the builder needs.
    """
    load = pickups.load(request)
    return {"id": load["mailbag_id"], "lat": load["lat"], "lon": load["lon"],
            pickups.BAGS: load[pickups.BAGS], pickups.WEIGHT: load[pickups.WEIGHT]}


def pickup(hub: dict[str, Any], requests: Sequence[dict[str, Any]],
           vans: Sequence[dict[str, Any]], matrix: TravelMatrix, *,
           today: date, model: dict[str, Any] | None = None) -> Problem:
    """§5.1's collection round, as a static problem over the bags admitted.

    §5.1.5 calls the pickup fleet a dynamic VRP and
    `docs/solver-capabilities.md` confirms the platform can insert a stop into
    a running plan, so this is the batch the day *starts* from -- the shape
    `quote_insertion` prices against, not a replacement for it.

    Args:
        hub: the §3.1 hub row, with `id`, `lat`, `lon`.
        requests: §9.1 mailbag records, in the matrix's order.
        vans: §9.1 vehicle records earmarked for pickups (§5.1.3).
        matrix: travel over `[hub, *requests]`, in that order.
        today: the collection date, carried into the problem id.
        model: the pickup model; this repository's own by default.

    Raises:
        ValueError: if the matrix does not span the hub and every bag, or if a
            motorbike appears among the vans -- §7.1 makes pickups van-only,
            and a bike here is a mistake upstream rather than a stop to refuse.
    """
    expected = len(requests) + 1
    if matrix.size != expected:
        raise ValueError(
            f"matrix spans {matrix.size} locations for {len(requests)} bags "
            f"and the hub; it must span exactly {expected}, in that order, or "
            "arcs land on the wrong sites")

    wrong = [v["vehicle_id"] for v in vans
             if contract.CLASS_OF.get(v["type"]) not in pickups.COLLECTS]
    if wrong:
        raise ValueError(
            f"{', '.join(wrong)} cannot collect mailbags; §7.1 makes pickups "
            "van-only for security and motorbikes are never assigned pickup "
            "stops")

    model = model if model is not None else load_pickup_model()
    built = servicemodel.build(model, [as_depot(hub)],
                               [as_bag_record(r) for r in requests], matrix)

    return replace(
        built,
        id=f"ddn-pickup-{hub['id']}-{today.isoformat()}",
        # §7.1 again, from the other side: a vehicle without the skill cannot
        # be given a bag, so the platform's INV-10 enforces what the guard
        # above only refuses to build.
        orders=tuple(replace(order, required_skills={"pickup"})
                     for order in built.orders),
        vehicles=tuple(_van(v, hub["id"], model) for v in vans),
    )


def _van(record: dict[str, Any], hub_id: str, model: dict[str, Any]):
    """A pickup van, capacity cross-checked the way `contract._capacities` does."""
    from vrp.model import TimeWindow, Vehicle

    declared = {spec["class"]: spec["capacities"] for spec in model["fleet"]}
    capacities = dict(declared[contract.CLASS_OF[record["type"]]])
    for dimension, field in ((pickups.BAGS, "capacity_mailbags"),
                             (pickups.WEIGHT, "capacity_weight_g")):
        if dimension not in capacities or field not in record:
            continue
        if int(record[field]) != capacities[dimension]:
            raise ValueError(
                f"van {record['vehicle_id']} states {field} "
                f"{int(record[field])} and {model['name']} says "
                f"{capacities[dimension]}; §4.1 gives one capacity per type, "
                "so one of the two is wrong")

    shift = TimeWindow(start=int(record["shift_start"]),
                       end=int(record["shift_end"]))
    return Vehicle(
        id=record["vehicle_id"],
        capacities=capacities,
        shift=shift,
        # §7.1: the route must fit the shift. §7.4 makes duration the binding
        # constraint, so it is stated per vehicle rather than left to the model.
        max_duration=shift.end - shift.start,
        skills={record["role"]},
        start_location_id=hub_id,
        end_location_id=hub_id,
    )
