"""The entities of §9.1, carrying every field that section declares and no other.

This is the data contract as types. Where §9.1 gives a default ("Default 200",
"Default 10") the default is here; where it gives an enum, the enum carries
exactly the values listed and nothing convenient. A field that is not in §9.1
does not belong on these classes -- CLAUDE.md makes §9 the only schema
definition, so a new field is a change to the document first.

Three readings, recorded rather than settled silently:

1. **`coord_source` spelling.** §9.1 writes `actual / geocoded_address /
   zip_centroid` and §3.2 writes the same three with hyphens. §9 is the schema,
   so the underscored spelling wins and §3.2 is prose about it.
2. **Mailbag and pickup request are one table in §9.1 and two entities here.**
   The section heading is "Mailbags / pickup requests" and the fields divide
   cleanly: a bag has an id, a manifest count, a weight and a seal; a request
   has a site, a time and a window. `customer_id` appears on both because it is
   the join. Their union is exactly §9.1's table.
3. **`Facility.type`.** §3.1's Type column is prose ("Hub + delivery depot",
   "Secondary depot"); it is normalised to two values, since §3.3's only
   question of a facility is whether it is the hub.

`latest_van_departure` takes the delivery day rather than reading a clock,
because §3.1's one-day lag puts the release on D+1 while the van departs on D --
a derived value that does not know which day it is asked about is the shape that
has already cost this repository four fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from ddn.model.lifecycle import Status

#: §4.1: "actual where recorded, default 200 g".
DEFAULT_WEIGHT_G = 200
#: §7.4 and §9.1: ten minutes per envelope, the largest lever on throughput.
DEFAULT_SERVICE_MIN = 10


class CoordSource(StrEnum):
    """§9.1's three values. §3.2 explains what each is fit for."""

    ACTUAL = "actual"
    GEOCODED_ADDRESS = "geocoded_address"
    ZIP_CENTROID = "zip_centroid"


class GeocodeConfidence(StrEnum):
    """§9.1. Low confidence is held and flagged rather than routed (§3.2)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Outcome(StrEnum):
    """§6's four delivery outcomes, carried by §9.1's `previous_outcome`."""

    DELIVERED = "Delivered"
    REJECTED = "Rejected"
    RETURNED = "Returned"
    POSTPONED = "Postponed"


class VehicleType(StrEnum):
    """§4.1. Motorbikes never take pickups; vans never take delivery stops."""

    MOTORBIKE = "motorbike"
    VAN = "van"


class VehicleRole(StrEnum):
    """§9.1: "pickup and linehaul are van-only"."""

    DELIVERY = "delivery"
    PICKUP = "pickup"
    LINEHAUL = "linehaul"
    RETURN = "return"


class TransferReason(StrEnum):
    """§9.1's three, which are §5.3.2's three triggers.

    They differ in more than bookkeeping. The first two are corrections and
    §5.3.2 rules them the same way -- transfer if the envelope can still beat
    its SLA date, otherwise return it. The third is a cost comparison the
    allocation step makes, and may be declined without anything being wrong.
    """

    ADDRESS_CORRECTION = "address_correction"
    MISASSIGNMENT = "misassignment"
    REBALANCING = "rebalancing"


class FacilityType(StrEnum):
    """§3.1's Type column, normalised to the distinction §3.3 draws."""

    HUB = "hub"
    DEPOT = "depot"


def _numeric(value: object, field: str) -> float:
    """§8.1: one numeric score per envelope. `bool` is not a priority.

    `TypeError` rather than the `ValueError` the rest of the repository raises
    for contract violations: a score of the wrong *type* is a different fault
    from a field that is missing or contradicts §9.1, and the linter is right
    to insist on the distinction.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(
            f"§8.1 requires {field} to be a numeric score; got "
            f"{type(value).__name__} {value!r}"
        )
    return float(value)


@dataclass(frozen=True, slots=True)
class Envelope:
    """§9.1's envelope table. Only `Ready` ones are routable (§5.2.6)."""

    package_id: str
    customer_id: str
    recipient_id: str
    package_type: str
    mailbag_id: str
    status: Status
    expected_ready_at: datetime | None
    lat: float
    lon: float
    coord_source: CoordSource
    geocode_confidence: GeocodeConfidence
    facility_id: str
    priority: float
    sla_date: date
    weight_g: int | None = None
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    service_time_min: float = DEFAULT_SERVICE_MIN
    attempt_number: int = 0
    previous_outcome: Outcome | None = None
    locked_vehicle_id: str | None = None
    #: §8.2's other direction: withheld from the plan by operations.
    excluded_by_ops: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "priority", _numeric(self.priority, "priority"))
        if self.weight_g is None:
            object.__setattr__(self, "weight_g", DEFAULT_WEIGHT_G)

    @property
    def is_ready(self) -> bool:
        """§5.2.6: Ready at the hub or at a depot is the solver's only input."""
        return self.status is Status.READY

    def must_deliver_today(self, today: date) -> bool:
        """§6.1: SLA date = today is a hard must-deliver-today constraint.

        Takes the day rather than reading a clock: the pool a facility routes in
        the morning was made ready the day before (§3.1), so "today" is the
        caller's frame, not this process's.
        """
        return self.sla_date == today


@dataclass(frozen=True, slots=True)
class Mailbag:
    """The sealed bag of §2 -- the unit of pickup, never split at the site (§7.1)."""

    mailbag_id: str
    customer_id: str
    envelope_count: int
    expected_weight_g: int
    assembly_required_count: int
    seal_id: str


@dataclass(frozen=True, slots=True)
class PickupRequest:
    """A customer's request that one or more sealed bags be collected (§5.1.1)."""

    customer_id: str
    lat: float
    lon: float
    requested_at: datetime
    mailbags: tuple[Mailbag, ...]
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None

    @property
    def envelope_count(self) -> int:
        """§5.1.4: manifests give exact counts, so inflow is known per request."""
        return sum(bag.envelope_count for bag in self.mailbags)

    @property
    def expected_weight_g(self) -> int:
        return sum(bag.expected_weight_g for bag in self.mailbags)


@dataclass(frozen=True, slots=True)
class TransferRequest:
    """§9.1's transfer-request table: §5.3.2's load, with its deadline.

    `deadline` is §9.1's own definition -- "min(receiving depot's next morning
    release, SLA date)" -- and is the caller's to compute, because only the
    caller knows the receiving depot's release. Computing it here from a
    facility row would make this record disagree with the one a caller already
    built.
    """

    transfer_id: str
    package_id: str
    from_facility_id: str
    to_facility_id: str
    reason: TransferReason
    created_at: datetime
    deadline: datetime
    weight_g: int = DEFAULT_WEIGHT_G

    def __post_init__(self) -> None:
        if self.from_facility_id == self.to_facility_id:
            raise ValueError(
                f"{self.transfer_id} moves {self.package_id} from "
                f"{self.from_facility_id} to itself; §5.3.2 transfers an "
                "envelope to a *different* depot")

    def makes(self, arrival: datetime) -> bool:
        """§7.1: a transfer arriving after its deadline is not one to raise."""
        return arrival <= self.deadline


@dataclass(frozen=True, slots=True)
class ReturnStop:
    """§9.1's return-run stop: one customer site, the envelopes going back."""

    customer_id: str
    lat: float
    lon: float
    package_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Vehicle:
    """§9.1's vehicle table. `facility_id` is the day's allocation (§4.2)."""

    vehicle_id: str
    type: VehicleType
    facility_id: str
    role: VehicleRole
    capacity_envelopes: int
    capacity_mailbags: int
    capacity_weight_g: int
    shift_start: datetime
    shift_end: datetime
    linehaul_release_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Facility:
    """§3.1's row, plus the two §9.1 adds and the value derived from them."""

    facility_id: str
    name: str
    type: FacilityType
    lat: float
    lon: float
    route_release_time: time
    transit_from_hub_min: int
    unload_min: int

    @property
    def is_hub(self) -> bool:
        return self.type is FacilityType.HUB

    def latest_van_departure(self, delivery_day: date) -> datetime:
        """§3.1: release - transit - unload, for routes released on `delivery_day`.

        The result is usually on the day before `delivery_day`: §3.1's rhythm
        line-hauls on D for a release on D+1, and for distant depots the run is
        overnight. A hub has no line-haul to itself, so its own value is the
        release time less unloading and means nothing operationally.
        """
        release = datetime.combine(delivery_day, self.route_release_time)
        return release - timedelta(minutes=self.transit_from_hub_min + self.unload_min)
