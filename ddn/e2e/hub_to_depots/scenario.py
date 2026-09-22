"""What E2E-2 needs beyond E2E-1's hand-off.

§4 of `docs/e2e/e2e-2-hub-to-depots.md` is the inputs table. Its first row —
the ready envelopes — arrives as a `ReadyPool` and is not repeated here; what
this holds is the rest: the fleet, the geography, and the loads raised
elsewhere that tonight's circuits have to carry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ddn import assumptions
from ddn.linehaul import Transit


@dataclass(frozen=True, slots=True)
class Scenario:
    """§9.1 records for one night of line-haul."""

    #: Every §3.1 facility, in order. `linehaul.depots` drops the hub.
    facilities: Sequence[dict[str, Any]]
    #: §9.1 vehicle records. `linehaul.available` decides which are free —
    #: a pickup van with no release time is still out collecting.
    vans: Sequence[dict[str, Any]]
    #: Seconds between any two facilities, by id. §3.1 gives transit from the
    #: hub only, so this is a separate input; the circuit planner declines
    #: every transfer rather than guess one (§9.1, v0.15 proposal).
    transit: Transit | None = None
    #: §5.3.2 loads raised at a depot by E2E-3, for tonight to carry.
    transfers: Sequence[Any] = ()
    #: §5.5 envelopes waiting at a depot to ride home.
    returning: Sequence[dict[str, Any]] = ()
    #: van id -> the second it was back at the hub (§5.1.5). §7.1's line-haul
    #: bullet is checked against this, so an empty map is not "all back".
    van_back_at: Mapping[str, int] = field(default_factory=dict)
    hub_id: str = "HUB"
    unload_seconds: int = assumptions.FACILITY_UNLOAD_MIN * 60
    #: The day the circuits run. Deliveries are D+1 (§3.1's one-day lag).
    collection_day: date | None = None
    delivery_day: date | None = None
