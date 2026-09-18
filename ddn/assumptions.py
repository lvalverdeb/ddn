"""Placeholder values for the spec's `[TBD]`s, in one place.

`docs/assumptions.md` is the document; this module is what code reads, and
`tests/test_assumptions.py` fails if the two drift apart. Nothing here is a
measurement. Every value is either stated in a passage the problem definition
labels illustrative (§10), derived from arithmetic the definition does supply
(§7.4), or invented outright -- the document says which, per row, and the
invented ones carry their sensitivity.

**No value here may be quoted as an operational figure.** The facility
coordinates in particular are synthetic towns on the Costa Rican graph that
`run_day.py` happens to use; they are not the operation's geography, and
`capacity-finding.md` is the standing warning about what follows from measuring
on placeholders.

Two gaps are deliberately not filled: §8's objective weights, which is the
blocking unknown, and §11's metric targets, which are commitments rather than
inputs. See the document's closing section.
"""

from __future__ import annotations

from datetime import time
from typing import NamedTuple

# §4.2 fleet table, Open Question 9 -- from §10's worked example.
MOTORBIKES_TOTAL = 120
VANS_TOTAL = 10

# §5.1.3 earmark and its afternoon taper -- from §10.
PICKUP_VANS_EARMARKED = 6
PICKUP_VANS_AFTER_TAPER = 2

# §4.2's own planning figure: "~25 envelopes per motorbike per day", which
# §7.4 derives from the ten-minute service time against an 8-hour shift.
ENVELOPES_PER_BIKE = 25

# §4.1, §7.1 and §10 all leave this [TBD]. Invented: binds only if bags per
# trip falls below demand; at the 200 g default the 500 kg limit never binds.
MAILBAGS_PER_VAN = 40

# §7.2. Derived: §7.4's 480 minutes / 10 minutes = 48 stops before any travel,
# and §4.2's ~25 per bike per day is the same number from the other side.
STOPS_PER_ROUTE_SOFT_MAX = 25

# §7.4 and §5.1.2. Invented; scales pickup route length directly.
PICKUP_STOP_MIN = 10
# §7.4. Invented; feeds every depot's latest van departure.
FACILITY_UNLOAD_MIN = 30
# Not tagged [TBD] anywhere -- already hard-coded at returns.py:52. Recorded
# here so that it stops being invisible.
RETURN_STOP_MIN = 10

# §3.1. Invented. Delivery runs on D+1, so this is tomorrow's clock and
# line-haul departs the day before it.
ROUTE_RELEASE = time(7, 0)
# §5.6. Derived from §7.4's eight-hour shift and the release time.
SHIFT_START = time(7, 0)
SHIFT_END = time(15, 0)
# §5.1.6, §5.6. Invented.
PROCESSING_CUTOFF = time(18, 0)
RETURN_RUN_DISPATCH = time(16, 30)
VAN_IDLE_RETURN_MIN = 45
# §5.1.6. Invented. A service metric, not a solver constraint.
PICKUP_RESPONSE_TARGET_H = 4


# §5.2.5 and Open Question 4: no throughput figure is supplied anywhere. These
# three decide `expected_ready_at`, and through it what line-haul can carry, so
# a readiness result computed from them describes them rather than the
# operation. Deliberately not fitted to §10's 700-of-900 assembly split.
RECONCILE_PER_HOUR = 1200
ASSEMBLY_PER_HOUR = 120
SORT_PER_HOUR = 2400

# §5.1.5's own illustration: "re-optimisation runs at a fixed cadence (e.g.
# every 30 minutes)".
REOPT_CADENCE_MIN = 30

# §3.2 asks for zips "straddling two facilities' areas" to be flagged and Open
# Question 14 asks what to do about them. Invented: too small and nothing is
# flagged, too large and everything is.
EQUIDISTANT_MARGIN_M = 2000


class FacilityPlaceholder(NamedTuple):
    """One row of the document's facility table. Synthetic, not the operation."""

    facility_id: str
    locality: str
    lat: float
    lon: float
    transit_from_hub_min: int


# §3.1's table is entirely [TBD]. These are Costa Rican towns chosen to sit on
# the graph run_day.py uses. D6's 240 minutes is the one figure with a source:
# §10 calls it "the 4-hour D6 transit".
FACILITIES: tuple[FacilityPlaceholder, ...] = (
    FacilityPlaceholder("HUB", "San José", 9.9333, -84.0833, 0),
    FacilityPlaceholder("D1", "Heredia", 9.9981, -84.1197, 30),
    FacilityPlaceholder("D2", "Cartago", 9.8638, -83.9199, 45),
    FacilityPlaceholder("D3", "Alajuela", 10.0162, -84.2141, 40),
    FacilityPlaceholder("D4", "Puntarenas", 9.9763, -84.8384, 90),
    FacilityPlaceholder("D5", "Limón", 9.9907, -83.0359, 195),
    FacilityPlaceholder("D6", "Liberia", 10.6346, -85.4377, 240),
)
