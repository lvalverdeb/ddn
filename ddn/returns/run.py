"""§5.5 — the evening return run.

Unlike §5.3 this *is* a routing problem: §5.5 calls it "a static CVRP from the
hub to customer sites, solved each evening". It is not last mile reversed, and
two differences do the work.

**The stop is a customer site, not an envelope.** §9.1's return-run record is
`customer_id, lat, lon, package_ids` — a list. Several refused envelopes going
back to one sender is a single visit, so aggregation happens before any routing.
§5.4 has one envelope per stop; here that would plan three visits to one door.

**The pool is yesterday's outcomes, not today's demand.** §6 returns Rejected
and Returned envelopes to the customer and §6.1 adds those whose SLA date has
passed. Postponed envelopes are explicitly *not* here: §6 holds them at the
facility for another attempt, and returning one would end an attempt the
operation intends to make.

**Two things the document leaves open, and this module does not close.**
Open Question 13 asks whether the mailbag security rule applies to returns —
if it does, the run is van-only — so the fleet is the caller's to supply rather
than this module's to assume. And §7.4 gives no service time for a return stop:
its ten minutes is per envelope *delivered*, for a recipient who "opens,
reviews, asks questions, signs", and it explicitly treats multiple envelopes to
one recipient as "rare and not modelled separately" — which is the normal case
here. `SERVICE_SECONDS` is that figure borrowed per stop, flagged rather than
invented per envelope.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from vrp import servicemodel
from vrp.model import Problem, TravelMatrix, Vehicle

from ddn import assumptions
from ddn.contract import CLASS_OF, DEFAULT_WEIGHT_G, as_depot, costs

MODELS = Path(__file__).resolve().parent.parent.parent / "models"
MODEL_NAME = "ddn-return"

# §6's two return outcomes. Postponed is held, Delivered is closed.
RETURNED = frozenset({"Rejected", "Returned"})

#: §3.1 names the hub. A parameter rather than a literal because `run_day`
#: already takes one and two spellings of the same facility is a bug waiting
#: for the day somebody deploys with a different id.
HUB = "HUB"

# §7.4 has no figure for a return stop. Borrowed from the delivery figure and
# applied per stop rather than per envelope — see the module docstring. The
# platform can express fixed + per-unit (`StopSpec.service_per_unit`) the day
# §7.4 supplies both.
#
# Read from the registry rather than written out here. It was `600` for two
# days before anyone noticed it was an invented service time at all, and
# `RETURN_STOP_MIN` was added to `docs/assumptions.md` to stop it being
# invisible — but nothing read it, so the literal stayed in force and the
# placeholder had no effect on anything.
SERVICE_SECONDS = assumptions.RETURN_STOP_MIN * 60


@dataclass(frozen=True)
class ReturnStop:
    """One customer site, and everything going back to it tonight."""

    customer_id: str
    lat: float
    lon: float
    package_ids: tuple[str, ...]
    weight_g: int

    @property
    def envelope_count(self) -> int:
        return len(self.package_ids)


def load_model(name: str = MODEL_NAME) -> dict[str, Any]:
    """This repository's return-run model, from its own `models/`."""
    return json.loads((MODELS / f"{name}.json").read_text())


def eligible(envelopes: Sequence[dict[str, Any]], *,
             hub_id: str = HUB) -> list[dict[str, Any]]:
    """Envelopes that go back to the customer tonight.

    Args:
        envelopes: records carrying `previous_outcome` and `facility_id`, and
            `sla_expired` where §6.1's clock has run out.

    Returns:
        Those at the hub whose outcome sends them back, in their given order.
        Envelopes rejected at a secondary depot are **not** included: §5.5 has
        them "travel back to the hub on the van's return leg and join the
        following evening's return run", so tonight they are not at the hub and
        a visit planned for them would start from a place they are not.
    """
    return [e for e in envelopes
            if e.get("facility_id") == hub_id and goes_back(e)]


def goes_back(envelope: Mapping[str, Any]) -> bool:
    """Whether §6 sends this envelope back to its customer at all.

    The outcome half of `eligible`, on its own because §5.3.2 needs the same
    question asked at a *depot*: the envelopes riding the van home are these
    ones, somewhere else. Two copies of the predicate would be two places to
    forget §6.1's expiry clock.
    """
    return (envelope.get("previous_outcome") in RETURNED
            or bool(envelope.get("sla_expired")))


def sites(envelopes: Sequence[dict[str, Any]], *,
          hub_id: str = HUB) -> list[ReturnStop]:
    """Aggregate eligible envelopes into one stop per customer site."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for envelope in eligible(envelopes, hub_id=hub_id):
        grouped.setdefault(envelope["customer_id"], []).append(envelope)

    return [
        ReturnStop(
            customer_id=customer,
            lat=batch[0]["customer_lat"],
            lon=batch[0]["customer_lon"],
            package_ids=tuple(e["package_id"] for e in batch),
            weight_g=sum(int(e.get("weight_g", DEFAULT_WEIGHT_G)) for e in batch),
        )
        for customer, batch in grouped.items()
    ]


def fleet(vehicle_ids: Sequence[str], *, shift_start: int, shift_end: int,
          vehicle_type: str = assumptions.RETURN_VEHICLE_TYPE
          ) -> list[dict[str, Any]]:
    """§9.1 vehicle records for tonight's return run.

    Open Question 13 asks "does the van-only security rule also apply to
    envelopes returned to customers?" and nobody has answered. The default is
    the van, from `docs/assumptions.md`, on the cautious reading: a van cannot
    breach a rule that turns out to apply, and a motorbike can. `to_problem`
    still takes whatever fleet the caller hands it, so answering the question
    later is a change to one placeholder rather than to this module.
    """
    return [{"vehicle_id": vehicle_id, "type": vehicle_type, "role": "return",
             "shift_start": shift_start, "shift_end": shift_end}
            for vehicle_id in vehicle_ids]


def _vehicle(record: dict[str, Any], model: dict[str, Any]) -> Vehicle:
    """A return-run vehicle, capacity from the model by class.

    The same rule as `contract._capacities`: §4.1 gives capacity per type, so
    the model owns it. The *choice* of type is the caller's, because Open
    Question 13 has not been answered.
    """
    from vrp.model import TimeWindow

    declared = {spec["class"]: spec["capacities"] for spec in model["fleet"]}
    wanted = CLASS_OF.get(record["type"])
    if wanted not in declared:
        raise ValueError(
            f"vehicle {record['vehicle_id']} is a {record['type']}, which "
            f"{model['name']} does not describe; it declares "
            f"{', '.join(sorted(declared))}")
    shift = TimeWindow(start=int(record["shift_start"]),
                       end=int(record["shift_end"]))
    return Vehicle(
        id=record["vehicle_id"],
        capacities=dict(declared[wanted]),
        shift=shift,
        max_duration=shift.end - shift.start,
        skills={record["role"]},
        start_location_id="HUB",
        end_location_id="HUB",
        # §8: this builder replaces the vehicle `servicemodel.build` priced, so
        # it carries the price across or the objective is lost with it.
        **costs(model, wanted),
    )


def to_problem(hub: dict[str, Any], stops: Sequence[ReturnStop],
               vehicles: Sequence[dict[str, Any]],
               matrix: TravelMatrix, *,
               model: dict[str, Any] | None = None) -> Problem:
    """§5.5's CVRP: out from the hub, round the sites, back.

    Args:
        hub: the §3.1 hub row, with `lat`, `lon` and the late shift.
        stops: aggregated customer sites from `sites`.
        vehicles: whatever fleet the operation releases tonight — see Open
            Question 13.
        matrix: travel over `[hub, *stops]`, in that order.
        model: the return-run model; this repository's own by default.

    Raises:
        ValueError: if the matrix does not span the hub and every stop, because
            a mismatch attributes travel to the wrong site rather than failing.
    """
    expected = len(stops) + 1
    if matrix.size != expected:
        raise ValueError(
            f"matrix spans {matrix.size} locations for {len(stops)} return "
            f"stops and the hub; it must span exactly {expected}, in that "
            "order, or arcs land on the wrong sites")

    model = model if model is not None else load_model()
    records = [{"id": stop.customer_id, "lat": stop.lat, "lon": stop.lon,
                "envelope_count": stop.envelope_count,
                "weight_g": stop.weight_g}
               for stop in stops]
    built = servicemodel.build(model, [as_depot(hub)], records, matrix)

    return replace(
        built,
        orders=tuple(replace(order, required_skills={"return"})
                     for order in built.orders),
        vehicles=tuple(_vehicle(v, model) for v in vehicles),
    )
