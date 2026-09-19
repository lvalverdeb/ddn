"""Placeholder values for the spec's `[TBD]`s, in one place.

`docs/assumptions.md` is the document; this module is what code reads, and
`tests/test_assumptions.py` fails if the two drift apart. Nothing here is a
measurement. Every value is either stated in a passage the problem definition
labels illustrative (§10), derived from arithmetic the definition does supply
(§7.4), or invented outright -- the document says which, per row, and the
invented ones carry their sensitivity.

**No value here may be quoted as an operational figure.** The facility
coordinates in particular are synthetic towns on the Costa Rican graph that
`simulation/on_road.py` happens to use; they are not the operation's geography, and
`capacity-finding.md` is the standing warning about what follows from measuring
on placeholders.

Two gaps are deliberately not filled: §11's metric targets, which are
commitments rather than inputs, and §9.2's reason code for an unreachable
address, which needs a spec change rather than a value. See the document's
closing section, which is the list this sentence counts.

§8's objective weights used to be a third. They were never absent in practice --
`contract.PRIZE_SCALE` and the `models/*.json` objective blocks both carried a
stand-in, unlabelled, while the document said no placeholder existed. They are
registered here instead, as invented, because a placeholder that is written down
can be argued with and one that is not cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
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

# §6's four outcomes as rates, read off §10's own end of day: of the 2,950
# envelopes it dispatches (3,100 in the morning pool less 150 unassigned --
# D1's 20 and 130 across D2-D6), 2,750 are delivered, 50 rejected, 30 defective
# and 120 postponed. Per mille so that they are integers and sum to 1,000.
#
# These were 935/16/10/39 against v0.12's 2,880 of 3,080. v0.13 closed §10's
# arithmetic and nothing followed it here, because a rate derived from a
# document is not checked by anything that reads the document.
DELIVERED_PER_MILLE = 932
REJECTED_PER_MILLE = 17
RETURNED_PER_MILLE = 10
POSTPONED_PER_MILLE = 41

# §6 names three reasons an attempt is not completed -- "recipient unavailable,
# incorrect address, driver out of time" -- and gives no split. Invented; the
# remainder after these two is "driver out of time". The middle one matters
# beyond bookkeeping: §6 sends an incorrect address back for re-geocoding,
# which may move the envelope to a different facility.
POSTPONED_UNAVAILABLE_PER_MILLE = 500
POSTPONED_BAD_ADDRESS_PER_MILLE = 200

# §5.5 and Open Question 13: "does the van-only security rule also apply to
# envelopes returned to customers?" Nobody has answered, so the return run is
# planned with vans -- the cautious reading, since it is the one that cannot
# breach a security rule that turns out to apply.
RETURN_VEHICLE_TYPE = "van"

# §5.1.3's taper: the earmark "should taper during the afternoon so that vans
# are progressively released to line-haul". §10 releases four of six.
PICKUP_TAPER_FROM = time(12, 0)

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


# --------------------------------------------------------------- §8 objective
#
# A priority score and a metre are not the same unit, and the objective adds
# them. Measured on real Costa Rica road distances over 400 GAM stops, serving
# one more envelope costs about 2,300 at the median and 12,400 at p99.
#
# Scaled against the *median*, not the p99. The first attempt used the p99 so
# that no envelope would ever be declined for a detour, and that broke the
# thing it was protecting: a prize large enough to outweigh any detour is large
# enough to outweigh the shift, and PyVRP answered by returning routes that run
# past the end of the day. Measured on D5 -- 186 envelopes, 5 bikes, real
# Guanacaste roads:
#
#     scale        1     10    100   1,000   12,500
#     status    FEAS   FEAS   FEAS   INFEAS  INFEAS
#     served      43     65     69        0        0
#
# So the useful range is bounded above by feasibility, not by distance. At 100
# a low-priority envelope (score 40 -> 4,000) beats the median marginal cost
# and loses to an exceptional one, which is what prize-collecting is for: a
# remote envelope needing a 6 km detour should be declined ahead of three
# nearby ones, and §8's second objective is minimising unassigned, not
# refusing to leave anything.
#
# Invented. Provisional until §8 supplies real cost figures: a ratio between two
# of the document's own numbers plus a measured feasibility ceiling, not a
# preference.
PRIZE_SCALE = 100

# §8's other half, in the platform's currency, where one cost unit is one metre
# (`vrp/evaluator.py:46-51`). All three invented.
#
# COST_PER_METRE is 1 because that is what PyVRP applies when a vehicle prices
# nothing -- `pyvrp/Model.py:439` defaults `unit_distance_cost` to 1 and
# `pyvrp_adapter.py:600` omits the key for a zero cost. Declaring it changes no
# plan; it makes the rate a decision instead of a default, so that changing it
# has an effect.
COST_PER_METRE = 1
# A bike costs 50 km of riding to put on the road. This one does change plans:
# it is what makes deploying a bike a decision rather than free.
VEHICLE_FIXED_COST = 50_000
# Time is not priced. §7.4 makes the shift a hard bound, so duration is a
# constraint here rather than a cost.
COST_PER_SECOND = 0


@dataclass(frozen=True)
class Band:
    """One priority class, and the scores that fall in it.

    §8.1 leaves the categories open (Open Question 11) and gives an
    illustrative encoding; these are that illustration until operations name
    theirs. `tier` is 1-based because tier 0 belongs to the SLA clock.
    """

    name: str
    low: int
    tier: int


# §8.1's illustration: "Urgent 1,000-1,999, Standard 100-199, Low 1-99".
BAND_URGENT_LOW = 1000
BAND_STANDARD_LOW = 100
BAND_LOW_LOW = 1

#: Highest first, so the first band a score clears is its own.
BANDS: tuple[Band, ...] = (
    Band("urgent", BAND_URGENT_LOW, 1),
    Band("standard", BAND_STANDARD_LOW, 2),
    Band("low", BAND_LOW_LOW, 3),
)


class FacilityPlaceholder(NamedTuple):
    """One row of the document's facility table. Synthetic, not the operation."""

    facility_id: str
    locality: str
    lat: float
    lon: float
    transit_from_hub_min: int


# §3.1's table is entirely [TBD]. These are Costa Rican towns chosen to sit on
# the graph simulation/on_road.py uses. D6's 240 minutes is the one figure with a source:
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
