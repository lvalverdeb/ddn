"""§9.1's records, as a `Problem` the platform can solve.

The operation's data contract and the platform's domain model are different
shapes, and the mapping between them is where an operation is most easily
misread. Everything downstream -- the models, the outputs, the reason codes --
reads the decisions made here, so they are stated rather than implied.

**Priority is a class and a score, not one number.** §8.1 asks for a numeric
score with classes encoded as widely-spaced tiers, reasoning that "strict
lexicographic objectives over classes often are not" supported. This platform
does support them, but compiles them into prize bonuses that grow
multiplicatively in the number of *distinct* tiers: measured against
`vrp.solve.pyvrp_adapter.tier_bonuses`, they pass int64 somewhere between 50
and 55, and §8.1's own worked encoding has 1,098 distinct values. So the band
becomes `priority_tier` and the score within it becomes `prize` -- which is
what §8.1's second bullet asks for anyway, and is safe by nine orders of
magnitude.

**The SLA clock is not the commercial priority.** §6.1 warns the prioritisation
algorithm already folds SLA proximity into its score, so weighting it again
double-counts, and proposes "SLA date is today" as a hard constraint instead --
"a constraint, not a weight". `FR-25` is that distinction in the platform:
`priority_source` separates COMMERCIAL from SLA, and tier 0 is must-serve
whatever the prize. Neither idea has to be weakened to fit the other.

**An absent `status` is not a refusal.** §7.1 makes "only ready envelopes are
assigned to line-haul or delivery" a hard constraint, and §9.1 carries `status`
to enforce it. A record that omits the field is from a caller that has not
adopted it yet, and refusing the whole pool would be a worse answer than the one
this module gave before the field existed -- so the field is how a caller opts
into the gate, and its absence leaves the pool as it was. A record that carries
it is held to it.

**Triage happens before the matrix, not after.** A package refused for a bad
address or an expired SLA is not a routing decision and must not occupy a
matrix row -- `build_large_matrix` costs a round trip per tile, and the indices
have to line up with the orders. Hence two steps: `triage`, then a matrix over
what survived, then `to_problem`.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

from vrp import servicemodel
from vrp.model import Lock, Problem, TimeWindow, TravelMatrix, Vehicle

# §9.2's vocabulary for the unassigned list. Time and count are decided
# downstream by the solver; "in dispute" has no state of its own in §5.2.6 and
# so cannot be told apart from the rest of the pipeline here. These three are
# the ones known before any routing happens.
LOW_GEOCODE_CONFIDENCE = "low geocode confidence"
SLA_EXPIRED = "SLA expired"
NOT_READY = "not ready by cut-off"
REASONS = frozenset({LOW_GEOCODE_CONFIDENCE, SLA_EXPIRED, NOT_READY})

# §5.2.6: "Only envelopes in Ready (at the hub or at a depot) are solver inputs
# for delivery routing." Every other state in that lifecycle is either upstream
# of readiness or past dispatch.
READY = "Ready"

MODELS = Path(__file__).resolve().parent.parent / "models"
MODEL_NAME = "ddn-lastmile"

# What this mapping may change on an order `servicemodel.build` already made.
# Declared and checked rather than remembered: everything outside this set is
# the model file's to decide, and a second opinion on it would be the "two
# sources for one fact" that moving to `build` exists to remove.
OVERLAID = frozenset({"priority_tier", "prize", "priority_source",
                      "required_skills"})

# §7.3 keeps an optional per-envelope window "for exceptional cases", and a
# model's `windows` is a fixed list with no way to read one from a record. So
# the window is overlaid when a package carries one -- narrowly, on the stop's
# `time_windows` alone, because widening `OVERLAID` to the whole `delivery`
# field would let this mapping quietly re-decide the service time the model
# owns.
OVERLAID_STOP = frozenset({"time_windows"})

DEFAULT_WEIGHT_G = 200          # §4.1
DEFAULT_SERVICE_MIN = 10        # §7.4
COUNT, WEIGHT = "envelopes", "grams"

# §4.1 names the types; a delivery model names the classes. One mapping between
# them, here, because §9.1 publishes `type` and the model is written in the
# platform's vocabulary.
CLASS_OF = {"motorbike": "MOTO", "van": "VAN"}


@dataclass(frozen=True)
class Band:
    """One priority class, and the scores that fall in it.

    §8.1 leaves the categories open (`Open Question 2`) and gives an
    illustrative encoding; these are that illustration until operations name
    theirs. `tier` is 1-based because tier 0 belongs to the SLA clock.
    """

    name: str
    low: int
    tier: int


# Highest first, so the first band a score clears is its own.
BANDS: tuple[Band, ...] = (
    Band("urgent", 1000, 1),
    Band("standard", 100, 2),
    Band("low", 0, 3),
)


@dataclass(frozen=True)
class Excluded:
    """A package that will not be routed today, and the §9.2 reason."""

    package_id: str
    reason: str


def _sla(package: dict[str, Any]) -> date | None:
    raw = package.get("sla_date")
    return date.fromisoformat(raw) if raw else None


def triage(packages: Sequence[dict[str, Any]], *,
           today: date) -> tuple[list[dict[str, Any]], list[Excluded]]:
    """Split today's pool into what may be routed and what may not.

    Args:
        packages: §9.1 package records.
        today: the planning date, passed rather than read from the clock so a
            replay of a past day answers what that day answered.

    Returns:
        The routable records in their original order, and the exclusions with
        their §9.2 reasons. Order matters: the caller builds a matrix over the
        first list, so its indices must be stable.
    """
    routable, excluded = [], []
    for package in packages:
        sla = _sla(package)
        status = package.get("status")
        if package.get("geocode_confidence") == "low":
            excluded.append(Excluded(package["package_id"],
                                     LOW_GEOCODE_CONFIDENCE))
        elif sla is not None and sla < today:
            excluded.append(Excluded(package["package_id"], SLA_EXPIRED))
        # Last of the three, because the first two are terminal and this one is
        # a wait: a re-geocode or a return run has to be arranged, while an
        # envelope short of Ready needs only the hub to finish with it. An
        # envelope that is both stuck and past its SLA is therefore reported as
        # expired, which is the half somebody has to act on.
        elif status is not None and status != READY:
            excluded.append(Excluded(package["package_id"], NOT_READY))
        else:
            routable.append(package)
    return routable, excluded


def _tier_and_prize(package: dict[str, Any], *, today: date,
                    bands: Sequence[Band]) -> tuple[int, int, str]:
    """`(tier, prize, source)` for one package. See the module docstring."""
    score = int(package.get("priority", 0))
    if _sla(package) == today:
        # §6.1's exception. Tier 0 is must-serve whatever it is worth, so the
        # urgency is expressed once -- as a constraint -- and the score is left
        # to rank it against the other must-serves rather than to carry it.
        return 0, score, "SLA"
    for band in bands:
        if score >= band.low:
            return band.tier, score, "COMMERCIAL"
    return bands[-1].tier, score, "COMMERCIAL"


def _window(package: dict[str, Any], facility: dict[str, Any]) -> TimeWindow:
    start = package.get("time_window_start")
    end = package.get("time_window_end")
    if start is None or end is None:
        # §7.3: any time during the shift. The platform wants the key, and a
        # shift-wide window is what "no window" means operationally.
        return TimeWindow(start=facility["shift_start"], end=facility["shift_end"])
    return TimeWindow(start=int(start), end=int(end))


def load_model(name: str = MODEL_NAME) -> dict[str, Any]:
    """This repository's own delivery model, read from its own `models/`.

    Loaded by path rather than through `VRP_MODEL_PATH` because DDN ships the
    file and knows where it is; the environment variable is how the platform's
    own tooling finds models it did not ship.
    """
    return json.loads((MODELS / f"{name}.json").read_text())


def as_depot(facility: dict[str, Any]) -> dict[str, Any]:
    """A §3.1 facility row in the shape `servicemodel.build` expects."""
    return {"id": facility["id"], "lat": facility["lat"], "lon": facility["lon"]}


def as_record(package: dict[str, Any]) -> dict[str, Any]:
    """A §9.1 package in the shape `servicemodel.build` expects.

    The model addresses weight through `from_field`, which has no default of
    its own -- a record without `weight_g` would raise rather than fall back.
    §4.1 states the fallback, so it is applied here, where the operation's
    defaults belong, rather than asking the platform for a feature.
    """
    return {"id": package["package_id"],
            "lat": package["lat"], "lon": package["lon"],
            "weight_g": int(package.get("weight_g", DEFAULT_WEIGHT_G)),
            "service_time_min": int(package.get("service_time_min",
                                                DEFAULT_SERVICE_MIN))}


def _overlay(order, package: dict[str, Any], *, today: date,
             bands: Sequence[Band]):
    """What a model file cannot say about an order.

    The band, the score and the SLA clock are decisions about *this* envelope
    on *this* day; a model describes the operation. `required_skills` is here
    for the same reason -- §7.1 keeps earmarked pickup vehicles off delivery
    stops, and which vehicles those are is today's allocation.
    """
    tier, prize, source = _tier_and_prize(package, today=today, bands=bands)
    order = replace(order, priority_tier=tier, prize=prize,
                    priority_source=source, required_skills={"delivery"})
    start, end = package.get("time_window_start"), package.get("time_window_end")
    if start is not None and end is not None:
        order = replace(order, delivery=replace(
            order.delivery,
            time_windows=(TimeWindow(start=int(start), end=int(end)),)))
    return order


def _capacities(model: dict[str, Any], record: dict[str, Any]) -> dict[str, int]:
    """What this vehicle carries: from the model, cross-checked against the row.

    §4.1 gives capacity per *type* -- "Motorbike | 35 envelopes", "Van | 500 kg"
    -- so the model owns it and `modelcheck` gates it. §9.1 repeats it per row
    because the published contract has a column for it, which makes the record
    a cross-check rather than a second source.

    Neither is silently preferred. Taking the model's would load a bike to 35
    when the row says its box holds 20; taking the row's would put the model's
    reviewed number beyond reach. One of them is wrong, and which one is not
    this function's to guess.

    Raises:
        ValueError: if the model describes no such class, or if the record
            contradicts it -- naming the vehicle and both numbers.
    """
    wanted = CLASS_OF.get(record["type"])
    declared = {spec["class"]: spec["capacities"] for spec in model["fleet"]}
    if wanted not in declared:
        raise ValueError(
            f"vehicle {record['vehicle_id']} is a {record['type']}, which "
            f"{model['name']} does not describe; it declares "
            f"{', '.join(sorted(declared))}. §4.1 has motorbikes doing last "
            "mile exclusively, so a van here is a mistake rather than a gap")

    capacities = dict(declared[wanted])
    for dimension, field in ((COUNT, "capacity_envelopes"),
                             (WEIGHT, "capacity_weight_g")):
        stated = record.get(field)
        if stated is not None and int(stated) != capacities.get(dimension):
            raise ValueError(
                f"vehicle {record['vehicle_id']} states {field} "
                f"{int(stated)} and {model['name']} says "
                f"{capacities.get(dimension)}; §4.1 gives one capacity per "
                "type, so one of the two is wrong")
    return capacities


def _vehicle(record: dict[str, Any], facility_id: str,
             model: dict[str, Any]) -> Vehicle:
    shift = TimeWindow(start=int(record["shift_start"]),
                       end=int(record["shift_end"]))
    return Vehicle(
        id=record["vehicle_id"],
        capacities=_capacities(model, record),
        shift=shift,
        # §7.4: "The solver must enforce route duration as a hard constraint."
        # Per vehicle, not per model: §9.1 gives every row its own shift, so
        # the model cannot know this one and no longer states it.
        max_duration=shift.end - shift.start,
        # §7.1: earmarked pickup vehicles are not assigned delivery stops.
        skills={record["role"]},
        start_location_id=facility_id,
        end_location_id=facility_id,
    )


def to_problem(facility: dict[str, Any], packages: Sequence[dict[str, Any]],
               vehicles: Sequence[dict[str, Any]], matrix: TravelMatrix, *,
               today: date, model: dict[str, Any] | None = None,
               bands: Sequence[Band] = BANDS) -> Problem:
    """One facility's last mile (§5.4), as a `Problem`.

    The structural facts -- what a stop costs, what an envelope counts as,
    when the shift runs, whether the route closes -- come from the delivery
    model through `servicemodel.build`, so the file in `models/` is what
    decides them and `modelcheck` is what gates them. This function adds only
    what a model cannot express: the priority decision, the delivery skill,
    and operations' overrides.

    The fleet is the exception, and the tradeoff is worth naming. §9.1 gives
    each vehicle its own capacities, shift and role, so the day's vehicles are
    data rather than model; the model's `fleet` section describes the class and
    `build`'s generated vehicles are replaced with the records. Two sources
    would be a defect if both were read, so only one is.

    Args:
        facility: a §3.1 row carrying `id`, `lat`, `lon` and the shift.
        packages: records that survived `triage`, in the matrix's order.
        vehicles: §9.1 vehicle records allocated to this facility.
        matrix: travel over `[facility, *packages]`, in that order.
        today: the planning date.
        model: the delivery model; this repository's own by default.
        bands: the priority classes; see `BANDS`.

    Returns:
        A `Problem` the platform will solve, verify and explain.

    Raises:
        ValueError: if the matrix does not span the facility and every package,
            because a mismatch there silently attributes travel to the wrong
            stop rather than failing.
    """
    expected = len(packages) + 1
    if matrix.size != expected:
        raise ValueError(
            f"matrix spans {matrix.size} locations for {len(packages)} "
            f"packages and one facility; it must span exactly {expected}, in "
            "that order, or arcs land on the wrong stops")

    model = model if model is not None else load_model()
    built = servicemodel.build(model, [as_depot(facility)],
                               [as_record(p) for p in packages], matrix)

    locks = tuple(
        Lock(kind="PIN_ORDER_TO_VEHICLE", order_id=p["package_id"],
             vehicle_id=p["locked_vehicle_id"])
        for p in packages if p.get("locked_vehicle_id")
    )

    return replace(
        built,
        id=f"ddn-{facility['id']}-{today.isoformat()}",
        orders=tuple(_overlay(order, package, today=today, bands=bands)
                     for order, package in zip(built.orders, packages, strict=True)),
        vehicles=tuple(_vehicle(v, facility["id"], model) for v in vehicles),
        locks=locks,
    )
