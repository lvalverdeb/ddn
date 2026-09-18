"""§9.1 as request and response models.

§13.1: "Request and response models are generated from §9 and shared with the
internal model; the OpenAPI document is the published form of §9." So every
field here is a §9.1 field, spelled as §9.1 spells it, and the enums are the
ones `ddn.model` already defines rather than a second set that could drift.

`tests/test_api_contract.py` asserts the field sets match the dataclasses in
`ddn.model.records` exactly. That check reads the dataclass, not this file, so
a field added on one side without the other fails rather than diverging
quietly -- the published contract and the internal model are the same contract
or the OpenAPI document is a work of fiction.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ddn.model import (
    CoordSource,
    FacilityType,
    GeocodeConfidence,
    Outcome,
    Status,
    VehicleRole,
    VehicleType,
)
from ddn.model.records import DEFAULT_SERVICE_MIN, DEFAULT_WEIGHT_G

__all__ = [
    "CoordSource",
    "Envelope",
    "EnvelopeBatch",
    "EnvelopeEvent",
    "FacilityType",
    "GeocodeConfidence",
    "JobAccepted",
    "JobState",
    "LinehaulEvent",
    "Lock",
    "Mailbag",
    "Outcome",
    "PickupEvent",
    "Problem",
    "ProcessingEvent",
    "ReadyCount",
    "RouteRun",
    "RunRequest",
    "SimulationRequest",
    "Status",
    "VehicleRole",
    "VehicleType",
    "Violation",
]


class Strict(BaseModel):
    """§9 is the only schema: a field it does not declare is refused."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Envelope(Strict):
    """§9.1's envelope table, every field and no other."""

    package_id: str
    customer_id: str
    recipient_id: str
    package_type: str
    mailbag_id: str
    status: Status
    expected_ready_at: datetime | None = None
    lat: float
    lon: float
    coord_source: CoordSource
    geocode_confidence: GeocodeConfidence
    facility_id: str
    priority: float = Field(description="§8.1: a numeric score; tiers are offsets in it")
    weight_g: int = Field(default=DEFAULT_WEIGHT_G, description="§4.1 default 200")
    sla_date: date
    time_window_start: datetime | None = None
    time_window_end: datetime | None = None
    service_time_min: float = Field(default=DEFAULT_SERVICE_MIN,
                                    description="§7.4: ten minutes per envelope")
    attempt_number: int = 0
    previous_outcome: Outcome | None = None
    locked_vehicle_id: str | None = None


class EnvelopeBatch(Strict):
    """§5.1.1's upload file: the envelopes a mailbag will contain."""

    envelopes: list[Envelope]


class Mailbag(Strict):
    """§9.1's mailbag / pickup request table."""

    mailbag_id: str
    customer_id: str
    lat: float
    lon: float
    requested_at: datetime
    envelope_count: int
    expected_weight_g: int
    assembly_required_count: int = 0
    pickup_window_start: datetime | None = None
    pickup_window_end: datetime | None = None
    seal_id: str


class EnvelopeEvent(Strict):
    """§13.1: "Status is never set directly." A caller appends what happened."""

    event: Status = Field(description="the §5.2.6 state this event moves to")
    actor: str = Field(description="§13.1: outcome events record who")
    at: datetime | None = None
    reason: str | None = Field(default=None,
                               description="§6's postponement sub-reason")
    lat: float | None = Field(default=None, description="§6: address correction")
    lon: float | None = None


class PickupEvent(Strict):
    """§5.1.7's doorstep outcomes, as events."""

    event: str = Field(description="collected | seal_broken | failed | cancelled")
    actor: str
    at: datetime | None = None
    seal_intact: bool | None = None
    reason: str | None = None


class ProcessingEvent(Strict):
    """§5.2's hub steps. §13.4: the API receives them, it does not perform them."""

    package_id: str
    event: Status = Field(description="Reconciled | Assembled | Sorted | Ready")
    actor: str
    at: datetime | None = None
    discrepancy: str | None = None


class LinehaulEvent(Strict):
    """§5.3: departed and arrived, against a plan already made."""

    event: str = Field(description="departed | arrived")
    actor: str
    at: datetime | None = None


class Lock(Strict):
    """§8.2: operations force an envelope onto a vehicle, and the plan re-runs."""

    package_id: str
    locked_vehicle_id: str
    actor: str


class RunRequest(Strict):
    """What a §5.3/§5.4/§5.5 run is asked to plan."""

    day: date
    facility_id: str | None = None


class SimulationRequest(Strict):
    """§5.6 and §10: one day, or several, with the sampling fixed."""

    day: date
    days: int = 1
    seed: int = 0


class JobAccepted(Strict):
    """§13.1: "returns 202 Accepted with a job id"."""

    job_id: str
    status: str
    poll: str = Field(description="where to ask what became of it")


class JobState(Strict):
    """A job's status, and its result once there is one."""

    job_id: str
    status: str
    result: dict | None = None
    violations: list[Violation] = Field(
        default_factory=list,
        description="§13.1: every routing result carries its §7.1 list")


class Violation(Strict):
    """One broken §7.1 bullet, as `solver_adapter.postcheck` reports it."""

    bullet: str
    detail: str
    vehicle_id: str | None = None
    order_id: str | None = None


class RouteRun(Strict):
    """§9.2's routing output for one facility's day."""

    run_id: str
    facility_id: str
    day: date
    routes: list[dict] = Field(default_factory=list)
    unassigned: list[dict] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)


class ReadyCount(Strict):
    """§5.2.5: how many will be ready at a facility by a given moment."""

    facility_id: str
    by: datetime
    ready: int


class Problem(Strict):
    """RFC 9457-shaped error body, so a 409 says what the current state is."""

    title: str
    detail: str
    status: int
    current_state: str | None = None
    package_id: str | None = None


JobState.model_rebuild()
RouteRun.model_rebuild()
