"""§10's worked example as data.

Every count here comes from §10's prose; none is read back out of `ddn.model`
or any other module. That direction matters: a fixture derived from the reader
agrees with the reader whether or not the reader is right, which is how a field
rename once passed twelve tests in this repository. The figures below are
transcribed from the document and the assertions in `tests/test_peak_day.py`
check the build against them.

**§10 closes arithmetically, as of v0.13.** The section states 4,800 envelopes
arriving, 200 rolled by the clean room, 40 held for low geocode confidence, 10
flagged in reconciliation, and 4,550 Ready by cut-off: 4,800 - 200 - 40 - 10 is
4,550, and the per-facility split sums to the same, so the Ready figure is
corroborated twice. It did not always: v0.12 said 12 flagged, which left the
document two short of its own total. This fixture carried 10 against that 12 and
recorded the difference, and v0.13 resolved it the same way -- which is the
argument for transcribing a document rather than deriving from it.

The other reading is §10's "1,300 zip-only envelopes are geocoded before
collection (40 low-confidence, held)": zip-only is where those envelopes
*started*, so after §5.2.2 runs, 1,260 carry `geocoded_address` and the 40 that
failed are still `zip_centroid` and held. §9.1's three coord sources are
end-state values, not provenance.

Coordinates are synthetic and scattered around the placeholder facilities in
`docs/assumptions.md`. They describe no real geography and no figure measured
on them describes the operation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache

from ddn import assumptions
from ddn.model import (
    CoordSource,
    Envelope,
    Facility,
    FacilityType,
    GeocodeConfidence,
    Mailbag,
    Outcome,
    PickupRequest,
    Status,
    Vehicle,
    VehicleRole,
    VehicleType,
)

#: §5.6 and §3.1: bags are collected and line-hauled on D, delivered on D+1.
COLLECTION_DAY = date(2026, 9, 16)
DELIVERY_DAY = COLLECTION_DAY + timedelta(days=1)

#: Fixed so that two builds are identical; nothing about it is meaningful.
SEED = 20260916

# ---------------------------------------------------------------- §10 figures
MOTORBIKES = 120
VANS = 10
PICKUP_VANS = 6
LINEHAUL_VANS = 4
VANS_RELEASED_TO_LINEHAUL = 4

#: "Motorbike allocation: HUB 48, D1 24, D2 18, D3 14, D4 8, D5 6, D6 2."
BIKE_ALLOCATION = {
    "HUB": 48, "D1": 24, "D2": 18, "D3": 14, "D4": 8, "D5": 6, "D6": 2,
}

#: "By cut-off 4,550 envelopes are Ready and sorted: HUB 1,600; D1 850; ..."
READY_BY_FACILITY = {
    "HUB": 1600, "D1": 850, "D2": 650, "D3": 550, "D4": 400, "D5": 320, "D6": 180,
}

SITES = 70
BAGS = 180
INFLOW = 4800
ASSEMBLY_REQUIRED = 900
ASSEMBLY_CLEARED = 700
ASSEMBLY_ROLLED = 200
ZIP_ONLY = 1300
LOW_CONFIDENCE_HELD = 40

#: §10: "on arrival reconciliation flags 10 discrepancies (held)".
DISPUTED = 10

READY = sum(READY_BY_FACILITY.values())

#: §8.1's worked tiers: "Urgent 1,000-1,999, Standard 100-199, Low 1-99".
TIERS = ((1000, 1999), (100, 199), (1, 99))

# §10's end of day. **A different pool from everything above:** these are the
# outcomes of *today's* delivery on the morning pool of 3,100, while the inflow
# above is what was collected today for delivery tomorrow. §10 carries both and
# they do not add up to each other.
DELIVERED, REJECTED, DEFECTIVE, POSTPONED = 2750, 50, 30, 120
#: "Total unassigned 150; dispatched 2,950." D1's 20 and 130 across D2-D6.
UNASSIGNED_D1, UNASSIGNED_OTHER_DEPOTS = 20, 130
UNASSIGNED = UNASSIGNED_D1 + UNASSIGNED_OTHER_DEPOTS
DISPATCHED = MORNING_POOL_TOTAL = 3100
#: "4,550 positioned today + 150 unassigned + 120 postponed = 4,820."
TOMORROW_POOL = 4550 + UNASSIGNED + POSTPONED
#: "Return run: 80 envelopes to 30 customer sites, 2 vans."
RETURN_SITES, RETURN_VANS = 30, 2
RETURN_POOL = REJECTED + DEFECTIVE


@dataclass(frozen=True, slots=True)
class PeakDay:
    """One day of §10, as entities."""

    collection_day: date
    delivery_day: date
    facilities: tuple[Facility, ...]
    vehicles: tuple[Vehicle, ...]
    requests: tuple[PickupRequest, ...]
    envelopes: tuple[Envelope, ...]

    @property
    def mailbags(self) -> tuple[Mailbag, ...]:
        return tuple(bag for request in self.requests for bag in request.mailbags)

    def ready(self) -> tuple[Envelope, ...]:
        return tuple(e for e in self.envelopes if e.is_ready)


def _facilities() -> tuple[Facility, ...]:
    """§3.1's table, filled from the placeholders in `docs/assumptions.md`."""
    return tuple(
        Facility(
            facility_id=row.facility_id,
            name=row.locality,
            type=FacilityType.HUB if row.facility_id == "HUB" else FacilityType.DEPOT,
            lat=row.lat,
            lon=row.lon,
            route_release_time=assumptions.ROUTE_RELEASE,
            transit_from_hub_min=row.transit_from_hub_min,
            unload_min=assumptions.FACILITY_UNLOAD_MIN,
        )
        for row in assumptions.FACILITIES
    )


def _vehicles(delivery_day: date) -> tuple[Vehicle, ...]:
    """120 bikes on §10's allocation; 10 vans, 6 on pickups and 4 on line-haul."""
    shift_start = datetime.combine(delivery_day, assumptions.SHIFT_START)
    shift_end = datetime.combine(delivery_day, assumptions.SHIFT_END)
    fleet: list[Vehicle] = []

    number = 0
    for facility_id, count in BIKE_ALLOCATION.items():
        for _ in range(count):
            number += 1
            fleet.append(
                Vehicle(
                    vehicle_id=f"MOTO-{number:03d}",
                    type=VehicleType.MOTORBIKE,
                    facility_id=facility_id,
                    role=VehicleRole.DELIVERY,
                    capacity_envelopes=35,          # §7.1
                    capacity_mailbags=0,            # §7.1: pickups are van-only
                    capacity_weight_g=35_000,       # §9.1
                    shift_start=shift_start,
                    shift_end=shift_end,
                )
            )

    # Vans work the collection day: they pick up and line-haul on D so that the
    # bikes above can deliver on D+1 (§3.1's one-day lag).
    van_start = datetime.combine(COLLECTION_DAY, assumptions.SHIFT_START)
    van_end = datetime.combine(COLLECTION_DAY, assumptions.PROCESSING_CUTOFF)
    # "tapering to 2 by mid-afternoon as 4 are released to line-haul"
    released_at = datetime.combine(COLLECTION_DAY, assumptions.SHIFT_END)
    for index in range(VANS):
        on_pickups = index < PICKUP_VANS
        releases = on_pickups and index < VANS_RELEASED_TO_LINEHAUL
        fleet.append(
            Vehicle(
                vehicle_id=f"VAN-{index + 1:02d}",
                type=VehicleType.VAN,
                facility_id="HUB",
                role=VehicleRole.PICKUP if on_pickups else VehicleRole.LINEHAUL,
                capacity_envelopes=2500,            # §4.1: 500 kg at the 200 g default
                capacity_mailbags=assumptions.MAILBAGS_PER_VAN,
                capacity_weight_g=500_000,          # §7.1
                shift_start=van_start,
                shift_end=van_end,
                linehaul_release_at=released_at if releases else None,
            )
        )
    return tuple(fleet)


def _bag_sizes() -> list[int]:
    """4,800 envelopes over 180 bags, as evenly as they divide."""
    base, extra = divmod(INFLOW, BAGS)
    return [base + 1] * extra + [base] * (BAGS - extra)


def _requests(rng: random.Random, hub: Facility) -> tuple[PickupRequest, ...]:
    """180 bags from 70 sites: 40 sites send three bags, 30 send two."""
    per_site = [3] * (BAGS - SITES * 2) + [2] * (SITES - (BAGS - SITES * 2))
    sizes = _bag_sizes()
    assembly_per_bag = ASSEMBLY_REQUIRED // BAGS

    requests: list[PickupRequest] = []
    bag_number = 0
    opens = datetime.combine(COLLECTION_DAY, assumptions.SHIFT_START)
    for site in range(SITES):
        bags: list[Mailbag] = []
        for _ in range(per_site[site]):
            count = sizes[bag_number]
            bag_number += 1
            bags.append(
                Mailbag(
                    mailbag_id=f"BAG-{bag_number:04d}",
                    customer_id=f"CUST-{site + 1:03d}",
                    envelope_count=count,
                    expected_weight_g=count * 200,   # §4.1 default weight
                    assembly_required_count=assembly_per_bag,
                    seal_id=f"SEAL-{bag_number:04d}",
                )
            )
        requests.append(
            PickupRequest(
                customer_id=f"CUST-{site + 1:03d}",
                lat=hub.lat + rng.uniform(-0.25, 0.25),
                lon=hub.lon + rng.uniform(-0.25, 0.25),
                requested_at=opens + timedelta(minutes=rng.randrange(0, 480)),
                mailbags=tuple(bags),
            )
        )
    return tuple(requests)


@dataclass(slots=True)
class _Draft:
    """An envelope before §10's outcomes are applied to it."""

    package_id: str
    customer_id: str
    mailbag_id: str
    needs_assembly: bool
    priority: float


def _drafts(requests: tuple[PickupRequest, ...], rng: random.Random) -> list[_Draft]:
    """One draft per envelope, the first five of every bag needing assembly."""
    drafts: list[_Draft] = []
    number = 0
    for request in requests:
        for bag in request.mailbags:
            for position in range(bag.envelope_count):
                number += 1
                low, high = TIERS[number % len(TIERS)]
                drafts.append(
                    _Draft(
                        package_id=f"PKG-{number:05d}",
                        customer_id=request.customer_id,
                        mailbag_id=bag.mailbag_id,
                        needs_assembly=position < bag.assembly_required_count,
                        priority=float(rng.randrange(low, high + 1)),
                    )
                )
    return drafts


def build(seed: int = SEED) -> PeakDay:
    """§10, deterministically. Same seed, same day, byte-identical entities."""
    rng = random.Random(seed)
    facilities = _facilities()
    by_id = {facility.facility_id: facility for facility in facilities}
    requests = _requests(rng, by_id["HUB"])
    drafts = _drafts(requests, rng)

    # "assembly clears 700 of the 900 by cut-off, and the 200 rolled to
    # tomorrow are the lowest-priority ones."
    assembly = sorted(
        (d for d in drafts if d.needs_assembly), key=lambda d: (d.priority, d.package_id)
    )
    rolled = {d.package_id for d in assembly[:ASSEMBLY_ROLLED]}

    rest = [d for d in drafts if d.package_id not in rolled]
    held = {d.package_id for d in rest[:LOW_CONFIDENCE_HELD]}
    disputed = {d.package_id for d in rest[LOW_CONFIDENCE_HELD:][:DISPUTED]}

    ready_order = [
        d for d in drafts
        if d.package_id not in rolled | held | disputed
    ]
    facility_of: dict[str, str] = {}
    cursor = 0
    for facility_id, count in READY_BY_FACILITY.items():
        for draft in ready_order[cursor:cursor + count]:
            facility_of[draft.package_id] = facility_id
        cursor += count

    # The 40 held are exactly the geocoding failures; the other 1,260 zip-only
    # envelopes were geocoded successfully before the bag arrived (§5.2.2).
    geocoded = {d.package_id for d in rest[LOW_CONFIDENCE_HELD:][DISPUTED:]}
    geocoded = set(sorted(geocoded)[: ZIP_ONLY - LOW_CONFIDENCE_HELD])

    ready_at = datetime.combine(COLLECTION_DAY, assumptions.PROCESSING_CUTOFF)
    envelopes: list[Envelope] = []
    for index, draft in enumerate(drafts):
        if draft.package_id in rolled:
            status, facility_id = Status.RECONCILED, "HUB"
        elif draft.package_id in held:
            status, facility_id = Status.REQUESTED, "HUB"
        elif draft.package_id in disputed:
            status, facility_id = Status.RECEIVED_AT_HUB, "HUB"
        else:
            status = Status.READY
            facility_id = facility_of[draft.package_id]

        if draft.package_id in held:
            source = CoordSource.ZIP_CENTROID
            confidence = GeocodeConfidence.LOW
        elif draft.package_id in geocoded:
            source = CoordSource.GEOCODED_ADDRESS
            confidence = GeocodeConfidence.HIGH
        else:
            source = CoordSource.ACTUAL
            confidence = GeocodeConfidence.HIGH

        near = by_id[facility_id]
        envelopes.append(
            Envelope(
                package_id=draft.package_id,
                customer_id=draft.customer_id,
                recipient_id=f"RCPT-{index + 1:05d}",
                package_type="assembly" if draft.needs_assembly else "finished",
                mailbag_id=draft.mailbag_id,
                status=status,
                expected_ready_at=ready_at if status is Status.READY else None,
                lat=near.lat + rng.uniform(-0.12, 0.12),
                lon=near.lon + rng.uniform(-0.12, 0.12),
                coord_source=source,
                geocode_confidence=confidence,
                facility_id=facility_id,
                priority=draft.priority,
                # Every twenty-fifth ready envelope is due today, so that §6.1's
                # hard must-deliver-today constraint has something to bind on.
                sla_date=(
                    DELIVERY_DAY if status is Status.READY and index % 25 == 0
                    else DELIVERY_DAY + timedelta(days=2)
                ),
            )
        )

    return PeakDay(
        collection_day=COLLECTION_DAY,
        delivery_day=DELIVERY_DAY,
        facilities=facilities,
        vehicles=_vehicles(DELIVERY_DAY),
        requests=requests,
        envelopes=tuple(envelopes),
    )


#: §10's delivery morning: "today's routes, on the morning pool of 3,100"
#: (2,700 made ready yesterday plus 400 postponed held locally).
MORNING_POOL = 3100
MORNING_NEW, MORNING_POSTPONED = 2700, 400
#: §10 states two of the seven facility shares outright -- "HUB 48 bikes x 25 =
#: 1,200 vs 1,150 hub-direct" and "D1 24 x 25 = 600 vs 620". The other five are
#: scaled from §10's own positioned split so the total is its 3,100; they are a
#: fixture construction, not a figure the document gives.
MORNING_BY_FACILITY = {"HUB": 1150, "D1": 620, "D2": 412, "D3": 348,
                       "D4": 253, "D5": 203, "D6": 114}


def _is_postponed(index: int) -> bool:
    """Whether the `index`-th envelope of the morning pool is a retry.

    True for exactly `MORNING_POSTPONED` of `MORNING_POOL` indices, spread
    evenly. See the call site.
    """
    return ((index + 1) * MORNING_POSTPONED // MORNING_POOL
            > index * MORNING_POSTPONED // MORNING_POOL)


def morning_pool(seed: int = SEED) -> tuple[Envelope, ...]:
    """§10's morning pool: what today's delivery routes are built on.

    A *different* pool from the day's inflow. §3.1's one-day lag means the
    envelopes delivered this morning were made ready yesterday, while the
    4,550 the fixture positions today are tomorrow's. §10 carries both and they
    are not the same envelopes.

    Every envelope is Ready at its facility, which is what §5.2.6 requires of a
    delivery input, and a fortieth of them are due today so §6.1's hard
    constraint has something to bind on.
    """
    rng = random.Random(seed + 2)
    envelopes: list[Envelope] = []
    for facility_id, count in MORNING_BY_FACILITY.items():
        for _ in range(count):
            index = len(envelopes)
            low, high = TIERS[index % len(TIERS)]
            near = next(f for f in _facilities() if f.facility_id == facility_id)
            envelopes.append(Envelope(
                package_id=f"MRN-{index + 1:05d}",
                customer_id=f"CUST-{(index % SITES) + 1:03d}",
                recipient_id=f"RCPT-M{index + 1:05d}",
                package_type="finished",
                mailbag_id=f"BAG-{(index % BAGS) + 1:04d}",
                status=Status.READY,
                expected_ready_at=datetime.combine(
                    COLLECTION_DAY - timedelta(days=1),
                    assumptions.PROCESSING_CUTOFF),
                lat=near.lat + rng.uniform(-0.12, 0.12),
                lon=near.lon + rng.uniform(-0.12, 0.12),
                coord_source=CoordSource.ACTUAL,
                geocode_confidence=GeocodeConfidence.HIGH,
                facility_id=facility_id,
                priority=float(rng.randrange(low, high + 1)),
                sla_date=(DELIVERY_DAY if index % 40 == 0
                          else DELIVERY_DAY + timedelta(days=3)),
                # §10's 400 postponed, held locally for another attempt.
                # Spaced 3,100/400 apart rather than by an integer stride: the
                # ratio is 7.75, so `index % 8` -- what stood here -- gives 388
                # and no whole number gives 400. Spacing also spreads them
                # across the facilities in proportion to each one's share,
                # which is what "held locally" means when the hub runs delivery
                # routes of its own.
                attempt_number=1 if _is_postponed(index) else 0))
    return tuple(envelopes)


def returns_pool(seed: int = SEED) -> tuple[Envelope, ...]:
    """§10's return run: 80 envelopes to 30 customer sites.

    §6 sends two outcomes back to the customer -- Rejected, where the recipient
    refused, and Returned, where the envelope was defective -- and §5.5 adds
    SLA-expired envelopes to the same run. §10's "50 rejected, 30 defective"
    is exactly the 80 it then returns, so no expired ones are in this day.

    Customer ids are the fixture's own first thirty, so a caller can find each
    site's coordinates in `PeakDay.requests`.
    """
    rng = random.Random(seed + 1)
    # 80 envelopes over 30 sites: twenty sites send three back, ten send two.
    per_site = [3] * 20 + [2] * 10
    statuses = ([Status.REJECTED] * REJECTED) + ([Status.RETURNED] * DEFECTIVE)

    envelopes: list[Envelope] = []
    for site, count in enumerate(per_site):
        for _ in range(count):
            index = len(envelopes)
            low, high = TIERS[index % len(TIERS)]
            envelopes.append(Envelope(
                package_id=f"RET-{index + 1:04d}",
                customer_id=f"CUST-{site + 1:03d}",
                recipient_id=f"RCPT-R{index + 1:04d}",
                package_type="finished",
                mailbag_id=f"BAG-{(index % BAGS) + 1:04d}",
                status=statuses[index],
                expected_ready_at=None,
                lat=9.9333 + rng.uniform(-0.2, 0.2),
                lon=-84.0833 + rng.uniform(-0.2, 0.2),
                coord_source=CoordSource.ACTUAL,
                geocode_confidence=GeocodeConfidence.HIGH,
                facility_id="HUB",
                priority=float(rng.randrange(low, high + 1)),
                sla_date=DELIVERY_DAY + timedelta(days=1),
                # §9.1 carries the outcome as well as the status, and
                # `ddn.returns` keys on the outcome. Both are set, because a
                # real record has both.
                previous_outcome=Outcome(statuses[index].value),
                attempt_number=1))
    return tuple(envelopes)


@lru_cache(maxsize=1)
def load() -> PeakDay:
    """The peak day, built once per process."""
    return build()
