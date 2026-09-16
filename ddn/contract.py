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

**Triage happens before the matrix, not after.** A package refused for a bad
address or an expired SLA is not a routing decision and must not occupy a
matrix row -- `build_large_matrix` costs a round trip per tile, and the indices
have to line up with the orders. Hence two steps: `triage`, then a matrix over
what survived, then `to_problem`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from vrp.model import (
    Location,
    Lock,
    Order,
    Problem,
    StopSpec,
    TimeWindow,
    TravelMatrix,
    Vehicle,
)

# §9.2's vocabulary for the unassigned list. The other three -- time, count and
# cut-off missed -- are decided downstream, by the solver and by §5.2's
# partition; these two are the ones known before any routing happens.
LOW_GEOCODE_CONFIDENCE = "low geocode confidence"
SLA_EXPIRED = "SLA expired"
REASONS = frozenset({LOW_GEOCODE_CONFIDENCE, SLA_EXPIRED})

DEFAULT_WEIGHT_G = 200          # §4.1
DEFAULT_SERVICE_MIN = 10        # §7.4
COUNT, WEIGHT = "envelopes", "grams"


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
        if package.get("geocode_confidence") == "low":
            excluded.append(Excluded(package["package_id"],
                                     LOW_GEOCODE_CONFIDENCE))
        elif sla is not None and sla < today:
            excluded.append(Excluded(package["package_id"], SLA_EXPIRED))
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


def _order(package: dict[str, Any], facility: dict[str, Any], *,
           today: date, bands: Sequence[Band]) -> Order:
    tier, prize, source = _tier_and_prize(package, today=today, bands=bands)
    minutes = package.get("service_time_min", DEFAULT_SERVICE_MIN)
    return Order(
        id=package["package_id"],
        kind="JOB",
        quantities={COUNT: 1,
                    WEIGHT: int(package.get("weight_g", DEFAULT_WEIGHT_G))},
        priority_tier=tier,
        prize=prize,
        priority_source=source,
        required_skills={"delivery"},
        delivery=StopSpec(location_id=package["package_id"],
                          time_windows=(_window(package, facility),),
                          service_fixed=int(minutes) * 60),
    )


def _vehicle(record: dict[str, Any], facility_id: str) -> Vehicle:
    shift = TimeWindow(start=int(record["shift_start"]),
                       end=int(record["shift_end"]))
    return Vehicle(
        id=record["vehicle_id"],
        capacities={COUNT: int(record["capacity_count"]),
                    WEIGHT: int(record["capacity_weight_g"])},
        shift=shift,
        # §7.4: "The solver must enforce route duration as a hard constraint."
        # A shift window alone bounds when a route may run, not how long it
        # may take; `INV-6` makes this one hard.
        max_duration=shift.end - shift.start,
        # §7.1: earmarked pickup vehicles are not assigned delivery stops.
        skills={record["role"]},
        start_location_id=facility_id,
        end_location_id=facility_id,
    )


def to_problem(facility: dict[str, Any], packages: Sequence[dict[str, Any]],
               vehicles: Sequence[dict[str, Any]], matrix: TravelMatrix, *,
               today: date, bands: Sequence[Band] = BANDS) -> Problem:
    """One facility's last mile (§5.3), as a `Problem`.

    Args:
        facility: a §3.1 row, carrying `id`, `lat`, `lon` and the shift.
        packages: records that survived `triage`, in the order the matrix was
            built over.
        vehicles: §9.1 vehicle records allocated to this facility.
        matrix: travel over `[facility, *packages]`, in that order.
        today: the planning date.
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

    locations = [Location(id=facility["id"], lat=facility["lat"],
                          lon=facility["lon"], matrix_index=0)]
    locations += [Location(id=p["package_id"], lat=p["lat"], lon=p["lon"],
                           matrix_index=i)
                  for i, p in enumerate(packages, start=1)]

    locks = tuple(
        Lock(kind="PIN_ORDER_TO_VEHICLE", order_id=p["package_id"],
             vehicle_id=p["locked_vehicle_id"])
        for p in packages if p.get("locked_vehicle_id")
    )

    return Problem(
        id=f"ddn-{facility['id']}-{today.isoformat()}",
        locations=tuple(locations),
        orders=tuple(_order(p, facility, today=today, bands=bands)
                     for p in packages),
        vehicles=tuple(_vehicle(v, facility["id"]) for v in vehicles),
        matrix=matrix,
        locks=locks,
    )
