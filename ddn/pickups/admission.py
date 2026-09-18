"""§5.1 — mailbag pickups, the one genuinely dynamic stage.

Requests arrive through the day and refer to *specific sealed bags* (§5.1.1),
so the unit of assignment is a bag: not an envelope, and not a site. §4.1 sets
the capacity that follows — "van pickup capacity is in mailbags (bags are
collected whole) with the 500 kg weight limit as a secondary bound" — which
makes a bag of four envelopes and a bag of four hundred the same load.

§5.1.4 states the decision in a sentence: "which van, decided by remaining bag
capacity, remaining weight capacity and least additional route cost". §5.1.5
adds the constraint that makes this stage expensive rather than merely dynamic
— a van may take a bag only if it can still make **its own line-haul release**.

**That is the coupling to §5.3, and it is the whole point of the stage.**
§5.1.3: earmarking pickups no longer costs delivery capacity, "its cost is
line-haul availability: a van on pickups cannot depart for a depot until it is
back and unloaded". A van that overcommits to collections is a depot that
misses its morning route release, and under §5's one-day lag a missed release
is not a late delivery but a day's delay for every envelope aboard.

**What is here and what is not.** This module decides *which van*, which is
§5.1.4 and the admission half of §5.1.5. The re-optimisation cadence — running
every thirty minutes with visited stops frozen — is the other half, and the
platform already carries it (`vrp.committed.commit_locks` freezes an executed
prefix, `vrp.triggers.reoptimise` re-plans the rest). Least-additional-cost
insertion is `vrp.quote.quote_insertion`, confirmed working in
`docs/solver-capabilities.md`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ddn.contract import CLASS_OF

# §4.1's two dimensions for a pickup van. Envelope count travels separately as
# inflow information for §5.2's processing plan; it is not a routing quantity.
BAGS, WEIGHT = "mailbags", "grams"

# §4.1: sealed bags are not carried on motorbikes.
COLLECTS = frozenset({"VAN"})


class Assignment(dict):
    """Bags per van, plus the ones no van could take.

    A `dict` so callers read it as `assigned["V1"]`, with `unplaced` beside it
    rather than folded in: a bag nobody collected is a re-request tomorrow
    (§5.1.7), not an empty route.
    """

    def __init__(self, *args, unplaced: tuple[str, ...] = (), **kwargs):
        super().__init__(*args, **kwargs)
        self.unplaced = unplaced


def load(request: dict[str, Any]) -> dict[str, Any]:
    """A §9.1 mailbag record as the load it puts on a van.

    One bag is one bag whatever it holds — §4.1 collects them whole — so the
    envelope count does not appear here. It is what §5.2 needs to schedule the
    clean room, and treating it as a routing quantity would let a van take
    twenty bags of two envelopes and refuse two bags of four hundred.
    """
    return {"mailbag_id": request["mailbag_id"],
            "customer_id": request["customer_id"],
            "lat": request["lat"], "lon": request["lon"],
            BAGS: 1,
            WEIGHT: int(request["expected_weight_g"]),
            "envelope_count": int(request.get("envelope_count", 0))}


def can_take(vehicle: dict[str, Any], *, carrying_bags: int, carrying_g: int,
             request: dict[str, Any], back_at: int | None = None,
             cut_off: int | None = None) -> bool:
    """Whether this van may accept this bag. §5.1.4, §5.1.5.

    Args:
        vehicle: a §9.1 vehicle record.
        carrying_bags: bags already aboard.
        carrying_g: grams already aboard.
        request: the bag, as `load` returns it.
        back_at: when taking it would return the van to the hub. Required to
            test the line-haul release and the cut-off; omitted only when the
            caller is asking about capacity alone.
        cut_off: §5.1.6's processing cut-off.

    Returns:
        True when every one of §5.1.5's conditions holds.
    """
    if CLASS_OF.get(vehicle["type"]) not in COLLECTS:
        return False
    if carrying_bags + request[BAGS] > int(vehicle["capacity_mailbags"]):
        return False
    if carrying_g + request[WEIGHT] > int(vehicle["capacity_weight_g"]):
        return False

    if back_at is not None:
        # §5.1.5: "subject to ... that van's scheduled release time to
        # line-haul". A van that returns late is a depot that misses its
        # morning release.
        release = vehicle.get("linehaul_release_at")
        if release is not None and back_at > int(release):
            return False
        # §5.1.6: a bag arriving after the processing cut-off is tomorrow's.
        if cut_off is not None and back_at > cut_off:
            return False
    return True


def assign(requests: Sequence[dict[str, Any]], vans: Sequence[dict[str, Any]],
           *, back_at: int | None = None,
           cut_off: int | None = None) -> Assignment:
    """Place bags on vans, splitting a site across vans where it must.

    §5.1.4: "a site with more bags than any single van can take is split across
    vans or served by a second visit." Splitting is the cheaper of the two —
    a second visit pays the travel twice — so bags are placed one at a time
    rather than by site.

    This is admission, not sequencing. Which van, not in what order: the order
    is `vrp.quote.quote_insertion`'s to decide against a live route, and a van
    with room is a candidate regardless of where the stop lands in its day.

    Returns:
        An `Assignment` of van id to bag ids, with `unplaced` carrying those no
        van could accept — a re-request tomorrow (§5.1.7) rather than a bag
        forced onto a van that cannot make its release.
    """
    placed: dict[str, list[str]] = {v["vehicle_id"]: [] for v in vans}
    bags = {v["vehicle_id"]: 0 for v in vans}
    grams = {v["vehicle_id"]: 0 for v in vans}
    unplaced: list[str] = []

    for request in requests:
        for van in vans:
            vid = van["vehicle_id"]
            if can_take(van, carrying_bags=bags[vid], carrying_g=grams[vid],
                        request=request, back_at=back_at, cut_off=cut_off):
                placed[vid].append(request["mailbag_id"])
                bags[vid] += request[BAGS]
                grams[vid] += request[WEIGHT]
                break
        else:
            unplaced.append(request["mailbag_id"])

    return Assignment(placed, unplaced=tuple(unplaced))
