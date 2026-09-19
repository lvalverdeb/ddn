"""§5.1 — mailbag pickups, the one genuinely dynamic stage.

Requests arrive through the day and refer to *specific sealed bags* (§5.1.1),
so the unit of assignment is a bag rather than an envelope or a site. §4.1 is
explicit about the capacity that follows: "van pickup capacity is in mailbags
(bags are collected whole) with the 500 kg weight limit as a secondary bound".

§5.1.4 states the decision in one sentence — "which van, decided by remaining
bag capacity, remaining weight capacity and least additional route cost" — and
§5.1.5 adds the constraint that makes this stage expensive rather than merely
dynamic: a van may only take a bag if it can still make **its own line-haul
release**. §5.1.3 says why that matters: earmarking pickups no longer costs
delivery capacity, it costs line-haul availability.

That is the coupling to §5.3. A van that overcommits to pickups is a depot that
misses its morning release, and under §5's one-day lag a missed release is a
day's delay for every envelope aboard.
"""

from __future__ import annotations

from ddn import pickups

HOUR = 3600
HUB = {"id": "HUB", "lat": 9.9472, "lon": -84.0531,
       "shift_start": 6 * HOUR, "shift_end": 18 * HOUR}


def bag(mailbag_id: str, customer: str = "C1", *, envelopes: int = 40,
        weight_g: int = 8_000, **over) -> dict:
    """A §9.1 mailbag record."""
    return {"mailbag_id": mailbag_id, "customer_id": customer,
            "lat": 9.95, "lon": -84.06, "requested_at": 8 * HOUR,
            "envelope_count": envelopes, "expected_weight_g": weight_g,
            "assembly_required_count": 0, "seal_id": f"SEAL-{mailbag_id}",
            **over}


def van(vid: str, *, bags: int = 20, weight_g: int = 500_000,
        release_at: int | None = None, **over) -> dict:
    return {"vehicle_id": vid, "type": "van", "facility_id": "HUB",
            "role": "pickup", "capacity_mailbags": bags,
            "capacity_weight_g": weight_g, "linehaul_release_at": release_at,
            "shift_start": 6 * HOUR, "shift_end": 18 * HOUR, **over}


# --------------------------------------------------------------------------
# The unit of capacity (§4.1, §5.1.1)
# --------------------------------------------------------------------------

def test_a_bag_is_one_unit_of_van_capacity_whatever_it_holds():
    """§4.1: "bags are collected whole". A bag of 400 envelopes and a bag of 4
    each occupy one of a van's bag slots; envelope count is inflow information
    for §5.2, not a routing quantity."""
    small = pickups.load(bag("B1", envelopes=4))
    large = pickups.load(bag("B2", envelopes=400))

    assert small["mailbags"] == large["mailbags"] == 1


def test_weight_is_the_secondary_bound():
    """§4.1 keeps the 500 kg limit as a second dimension, so a van full by
    weight is full even with bag slots to spare."""
    loaded = pickups.load(bag("B1", weight_g=12_345))

    assert loaded["grams"] == 12_345


# --------------------------------------------------------------------------
# Which van (§5.1.4)
# --------------------------------------------------------------------------

def test_a_van_at_bag_capacity_cannot_take_another():
    full = van("V1", bags=2)

    assert not pickups.can_take(full, carrying_bags=2, carrying_g=0,
                                request=pickups.load(bag("B3")))


def test_a_van_at_weight_capacity_cannot_take_another():
    """Bag slots free, weight gone — §4.1's secondary bound still binds."""
    heavy = van("V1", bags=20, weight_g=10_000)

    assert not pickups.can_take(heavy, carrying_bags=1, carrying_g=9_000,
                                request=pickups.load(bag("B2", weight_g=2_000)))


def test_only_vans_collect_mailbags():
    """§4.1: "mailbags are collected by vans only, for security: sealed bags
    are not carried on motorbikes"."""
    bike = van("M1", bags=20)
    bike["type"] = "motorbike"

    assert not pickups.can_take(bike, carrying_bags=0, carrying_g=0,
                                request=pickups.load(bag("B1")))


# --------------------------------------------------------------------------
# The line-haul coupling (§5.1.3, §5.1.5)
# --------------------------------------------------------------------------

def test_a_van_due_on_line_haul_will_not_take_a_bag_it_cannot_return_from():
    """§5.1.5: insertion is "subject to ... that van's scheduled release time
    to line-haul", and §5.1.3 says the cost of earmarking *is* line-haul
    availability.

    A van due back at 14:00 cannot accept a collection that returns it at
    14:30 — under §5's one-day lag that is a depot missing its morning release
    and a day's delay for everything aboard.
    """
    due = van("V1", release_at=14 * HOUR)

    assert not pickups.can_take(due, carrying_bags=0, carrying_g=0,
                                request=pickups.load(bag("B1")),
                                back_at=14 * HOUR + 1800)
    assert pickups.can_take(due, carrying_bags=0, carrying_g=0,
                            request=pickups.load(bag("B1")),
                            back_at=13 * HOUR)


def test_a_van_with_no_line_haul_duty_is_bounded_only_by_the_cut_off():
    """Not every earmarked van is committed; §5.1.3 has the earmark taper
    through the afternoon, so some are released and some are not."""
    free_van = van("V1", release_at=None)

    assert pickups.can_take(free_van, carrying_bags=0, carrying_g=0,
                            request=pickups.load(bag("B1")),
                            back_at=17 * HOUR)


def test_nothing_is_collected_after_the_processing_cut_off():
    """§5.1.6: "bags collected and delivered to the hub before the processing
    cut-off can be made ready the same day"."""
    free_van = van("V1")

    assert not pickups.can_take(free_van, carrying_bags=0, carrying_g=0,
                                request=pickups.load(bag("B1")),
                                back_at=19 * HOUR, cut_off=18 * HOUR)


# --------------------------------------------------------------------------
# Splitting a site (§5.1.4)
# --------------------------------------------------------------------------

def test_a_site_with_more_bags_than_a_van_holds_is_split():
    """§5.1.4: "a site with more bags than any single van can take is split
    across vans or served by a second visit"."""
    requests = [pickups.load(bag(f"B{i}", customer="BIG")) for i in range(5)]
    fleet = [van("V1", bags=3), van("V2", bags=3)]

    assigned = pickups.assign(requests, fleet, back_at=10 * HOUR)

    assert sum(len(v) for v in assigned.values()) == 5
    assert len(assigned["V1"]) <= 3 and len(assigned["V2"]) <= 3
    assert {b for v in assigned.values() for b in v} == {r["mailbag_id"]
                                                         for r in requests}


def test_a_bag_no_van_can_take_is_refused_rather_than_forced():
    """A refusal is a re-request tomorrow (§5.1.7); a forced assignment is a
    van that cannot make its line-haul release."""
    requests = [pickups.load(bag(f"B{i}")) for i in range(4)]

    assigned = pickups.assign(requests, [van("V1", bags=2)], back_at=10 * HOUR)

    assert len(assigned["V1"]) == 2
    assert len(assigned.unplaced) == 2


# ------------------------------- §7.1's three mailbag bullets, where they bind


def test_a_motorbike_is_never_offered_a_bag():
    """§7.1: "Mailbags are collected by vans only; motorbikes are never
    assigned pickup stops."

    `postcheck` has a `stage="pickup"` branch that reports this, and no
    production path can reach it: `pickups.run` is a greedy assigner that
    builds no `Problem` and no `Solution`, and `solver_adapter.pickup` — which
    does — has no caller outside tests. Fabricating a `Solution` so the branch
    could run would put `vrp.verify`'s seventeen invariants in judgement over
    an object this repository made up.

    So the bullet is pinned where it actually binds. §4.1's security rule is
    kept by refusing the vehicle at admission, which is earlier and harder than
    reporting it afterwards.
    """
    moto = dict(van("MOTO-1"), type="motorbike")

    assert not pickups.can_take(moto, carrying_bags=0, carrying_g=0,
                                request=pickups.load(bag("BAG-1")))


def test_a_van_is_not_given_more_bags_than_it_holds():
    """§7.1: "A van never carries more than [TBD] mailbags."

    The limit is the vehicle's own `capacity_mailbags`, which
    `docs/assumptions.md` supplies as a placeholder for Open Question 1.
    """
    small = van("VAN-1", bags=2)
    request = pickups.load(bag("BAG-3"))

    assert pickups.can_take(small, carrying_bags=1, carrying_g=0, request=request)
    assert not pickups.can_take(small, carrying_bags=2, carrying_g=0,
                                request=request)


def test_a_bag_is_one_bag_however_many_envelopes_it_holds():
    """§7.1: "Sealed mailbags are collected whole; bags are never split at the
    customer site."

    A bag cannot be part-collected because it is never more than one unit of
    load to begin with. Counting envelopes here would let a van take twenty
    bags of two and refuse two bags of four hundred, and would make "half a
    bag" a representable quantity.
    """
    small = pickups.load(bag("BAG-1", envelopes=2))
    huge = pickups.load(bag("BAG-2", envelopes=400))

    assert small[pickups.BAGS] == huge[pickups.BAGS] == 1
    assert huge["envelope_count"] == 400, "§5.2 still needs the count"
