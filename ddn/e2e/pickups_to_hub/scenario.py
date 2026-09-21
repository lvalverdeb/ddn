"""What E2E-1 needs to be handed, as one record.

§3 of `docs/e2e/e2e-1-pickups-to-hub.md` is the inputs table; this is that
table as a type. It holds inputs and nothing derived — no counts, no
groupings, no defaults standing in for figures §10 does not supply. A
scenario that computed something would move slice logic one file sideways
out of `run.py`, where `tests/e2e/test_run_purity.py` would stop looking.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class Scenario:
    """§9.1 records for one pickup-and-processing day."""

    #: The §3.1 hub row. §5.1's vans start and finish here.
    hub: dict[str, Any]
    #: Every §3.1 facility, **in order**. The order reaches the hand-off and
    #: is load-bearing there — see `processing.at_facilities`.
    facilities: Sequence[dict[str, Any]]
    #: §9.1 mailbag records with `requested_at` — they arrive through the day.
    requests: Sequence[dict[str, Any]]
    #: §9.1 envelope records from the upload files, pre-sorted to a facility.
    inflow: Sequence[dict[str, Any]]
    #: §9.1 vehicle records earmarked for pickups (§5.1.3). Van-only is
    #: enforced by `pickups.admission`, not here.
    vans: Sequence[dict[str, Any]]
    #: Seconds between two points, from the gateway. §5.1 refuses to run on a
    #: guessed speed and this package invents none.
    travel: Callable[[float, float, float, float], int]
    #: The day being collected. §3.1's one-day lag means delivery is D+1.
    collection_day: date
    #: §5.1.6's processing cut-off as a second of the day. An *input* here,
    #: not a constant read inside the slice: e2e-1 §3's table sources it from
    #: `docs/assumptions.md`, and a slice that reached into the registry
    #: itself could not be run against a different cut-off to see what moves.
    cut_off: int
