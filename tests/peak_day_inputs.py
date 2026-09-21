"""§10's peak day in the shape `simulation.run_day` takes it.

One builder, because two callers need the identical day: `tests/test_simulation.py`
asserts what the simulator makes of it, and `tests/e2e/` composes the same day
out of slices and checks the two agree. Two copies of this would diverge
somewhere unimportant and the disagreement would be read as a decomposition
bug.

Nothing here computes: it reshapes `tests/fixtures/peak_day.py`'s entities into
the dicts each stage's §9.1 contract expects, and the road tables come from
`tests/matrices.py`'s recording. A figure invented here would be a figure §10
does not supply, wearing §10's name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ddn.model import travel as road
from tests.fixtures import peak_day
from tests.matrices import road_matrix, rows

HOUR = 3600


@dataclass(frozen=True)
class PeakDayInputs:
    """The day, its morning pools, and the keyword arguments `run_day` takes."""

    day: peak_day.PeakDay
    pools: dict[str, tuple[dict[str, Any], ...]]
    kwargs: dict[str, Any]


def build() -> PeakDayInputs:
    """§10 as `run_day` arguments. Deterministic: `peak_day.load()` is cached."""
    day = peak_day.load()
    midnight = datetime.combine(day.collection_day, datetime.min.time())

    def secs(moment):
        return int((moment - midnight).total_seconds())

    facilities = [{"id": f.facility_id, "lat": f.lat, "lon": f.lon,
                   "route_release_time": f.route_release_time.hour * HOUR,
                   "transit_from_hub_min": f.transit_from_hub_min,
                   "shift_start": 7 * HOUR, "shift_end": 15 * HOUR}
                  for f in day.facilities]

    pools: dict[str, list] = {}
    for e in peak_day.morning_pool():
        pools.setdefault(e.facility_id, []).append(
            {"package_id": e.package_id, "customer_id": e.customer_id,
             "lat": e.lat, "lon": e.lon, "status": "Ready",
             "geocode_confidence": "high", "priority": float(e.priority),
             "sla_date": e.sla_date.isoformat(), "facility_id": e.facility_id,
             "attempt_number": e.attempt_number,
             "customer_lat": e.lat, "customer_lon": e.lon})

    requests = [{"mailbag_id": b.mailbag_id, "customer_id": b.customer_id,
                 "lat": r.lat, "lon": r.lon,
                 "requested_at": secs(r.requested_at),
                 "expected_weight_g": b.expected_weight_g,
                 "envelope_count": b.envelope_count}
                for r in day.requests for b in r.mailbags]
    inflow = [{"package_id": e.package_id, "mailbag_id": e.mailbag_id,
               "package_type": e.package_type, "facility_id": e.facility_id,
               "lat": e.lat, "lon": e.lon, "status": "Ready",
               "geocode_confidence": "high", "priority": float(e.priority),
               "sla_date": e.sla_date.isoformat(),
               "customer_id": e.customer_id} for e in day.ready()]
    vans = [{"vehicle_id": v.vehicle_id, "type": "van", "role": v.role.value,
             "capacity_mailbags": v.capacity_mailbags,
             "capacity_weight_g": v.capacity_weight_g,
             "shift_start": secs(v.shift_start),
             "linehaul_release_at": (None if v.linehaul_release_at is None
                                     else secs(v.linehaul_release_at))}
            for v in day.vehicles if v.type.value == "van"]
    bikes = [v.vehicle_id for v in day.vehicles if v.type.value == "motorbike"]

    # Two road tables over the same recording, because the day asks two
    # different questions of it. §5.1's legs run hub-to-site and are looked up
    # by coordinate — a pickup van's position is wherever the last bag was.
    # §5.3.2's circuits run depot-to-depot and are looked up by id. Both come
    # from the gateway (`tests/matrices.py`); neither is computed here.
    #
    # The network is real and the places are not, so nothing below measures
    # this operation's geography — §3.1 still does not supply it.
    points = [facilities[0], *requests]
    return PeakDayInputs(
        day=day,
        pools={f: tuple(p) for f, p in pools.items()},
        kwargs={"facilities": facilities, "bikes": bikes, "vans": vans,
                "requests": requests, "inflow": inflow,
                "travel": road.over(road_matrix(points), road.index_of(points)),
                "transit": road.between(road_matrix(facilities),
                                        rows(facilities)),
                "allocation": peak_day.BIKE_ALLOCATION})
