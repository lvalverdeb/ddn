"""§9.1's entities and §5.2.6's lifecycle, as types.

The data contract and the state machine, and nothing that routes: this package
knows what an envelope *is* and which statuses follow which, not where it goes.
Geography (§3.3's nearest-facility rule), allocation (§4.2) and every stage of
§5 live elsewhere.
"""

from ddn.model.facility import NoRoadPath, nearest_facility, ranked
from ddn.model.lifecycle import (
    ROUTABLE,
    TERMINAL,
    TRANSITIONS,
    IllegalTransition,
    Status,
    advance,
    may,
)
from ddn.model.records import (
    DEFAULT_SERVICE_MIN,
    DEFAULT_WEIGHT_G,
    CoordSource,
    Envelope,
    Facility,
    FacilityType,
    GeocodeConfidence,
    Mailbag,
    Outcome,
    PickupRequest,
    ReturnStop,
    TransferReason,
    TransferRequest,
    Vehicle,
    VehicleRole,
    VehicleType,
)

__all__ = [
    "DEFAULT_SERVICE_MIN",
    "DEFAULT_WEIGHT_G",
    "ROUTABLE",
    "TERMINAL",
    "TRANSITIONS",
    "CoordSource",
    "Envelope",
    "Facility",
    "FacilityType",
    "GeocodeConfidence",
    "IllegalTransition",
    "Mailbag",
    "NoRoadPath",
    "Outcome",
    "PickupRequest",
    "ReturnStop",
    "Status",
    "TransferReason",
    "TransferRequest",
    "Vehicle",
    "VehicleRole",
    "VehicleType",
    "advance",
    "may",
    "nearest_facility",
    "ranked",
]
