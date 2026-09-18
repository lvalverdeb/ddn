"""§5.1.5's cadence and §5.1.7's exceptions."""

from __future__ import annotations

from ddn import assumptions
from ddn.model import travel as road
from ddn.pickups import Incident, run
from tests.matrices import fake_matrix

HOUR = 3600
HUB = {"id": "HUB", "lat": 9.9333, "lon": -84.0833}
CUT_OFF = 18 * HOUR


def bag(n: int, *, at: int = 7 * HOUR, grams: int = 6000, **over):
    return {"mailbag_id": f"BAG-{n}", "customer_id": f"C{n}",
            "lat": 9.9333 + n / 500, "lon": -84.0833 + n / 600,
            "requested_at": at, "expected_weight_g": grams,
            "envelope_count": 30, **over}


# Road seconds out of a fake matrix spanning every coordinate these tests use.
# A surplus bag (§5.1.7) copies its parent's coordinates, so it resolves to the
# parent's row — which is what makes one matrix enough for the whole module.
#
# It is a *fake*. `tests/matrices.py` says why that is the right trade here and
# what it costs: nothing in this file measures geography.
POINTS = [HUB, *(bag(n) for n in range(10))]
TRAVEL = road.over(fake_matrix(POINTS, kph=30.0), road.index_of(POINTS))


def van(n: int = 1, **over):
    return {"vehicle_id": f"V{n}", "type": "van", "facility_id": "HUB",
            "role": "pickup", "capacity_mailbags": assumptions.MAILBAGS_PER_VAN,
            "capacity_weight_g": 500_000, "shift_start": 7 * HOUR,
            "shift_end": CUT_OFF, **over}


def test_a_days_bags_are_collected_and_accounted_for():
    day = run([bag(n) for n in range(6)], [van()], HUB, travel=TRAVEL,
              cut_off=CUT_OFF)
    assert len(day.collected) == 6
    assert day.unplaced == ()
    assert day.re_requests == ()
    assert sum(len(route) for route in day.routes.values()) == 6


def test_every_bag_ends_in_exactly_one_outcome():
    """Collected, unplaced or re-requested — a bag in none is one nobody seeks."""
    pool = [bag(n) for n in range(8)]
    day = run(pool, [van()], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-2": Incident.SITE_CLOSED,
                         "BAG-3": Incident.CANCELLED})
    accounted = set(day.collected) | set(day.unplaced) | set(day.re_requests)
    assert accounted | {"BAG-3"} == {b["mailbag_id"] for b in pool}


def test_a_bag_requested_after_the_cut_off_is_not_collected():
    """§5.1.6: bags arriving after the cut-off are tomorrow's."""
    day = run([bag(1, at=CUT_OFF + HOUR)], [van()], HUB, travel=TRAVEL,
              cut_off=CUT_OFF)
    assert day.collected == ()
    assert day.unplaced == ("BAG-1",)


def test_a_van_is_not_given_a_bag_it_cannot_get_back_with():
    """§5.1.5: subject to its scheduled release time to line-haul."""
    early = van(linehaul_release_at=7 * HOUR + 60)
    day = run([bag(n) for n in range(4)], [early], HUB, travel=TRAVEL,
              cut_off=CUT_OFF)
    assert day.collected == ()
    assert len(day.unplaced) == 4


def test_a_motorbike_can_collect_nothing():
    """§7.1, enforced by the same admission rule that enforces capacity."""
    bike = van(vehicle_id="M1", type="motorbike", capacity_mailbags=0)
    day = run([bag(1)], [bike], HUB, travel=TRAVEL, cut_off=CUT_OFF)
    assert day.collected == ()
    assert day.unplaced == ("BAG-1",)


def test_bags_beyond_a_vans_capacity_go_to_another_van():
    small = van(1, capacity_mailbags=2)
    other = van(2, capacity_mailbags=2)
    day = run([bag(n) for n in range(4)], [small, other], HUB, travel=TRAVEL,
              cut_off=CUT_OFF)
    assert len(day.collected) == 4
    assert all(len(route) <= 2 for route in day.routes.values())


def test_requests_arriving_later_are_invisible_to_earlier_cycles():
    """§5.1.1: pickup requests arrive continuously through the day."""
    day = run([bag(1, at=7 * HOUR), bag(2, at=15 * HOUR)], [van()], HUB,
              travel=TRAVEL, cut_off=CUT_OFF)
    first, second = (v for v in day.visits)
    assert first.mailbag_id == "BAG-1"
    assert second.at >= 15 * HOUR, "not visited before it was asked for"


def test_visits_run_forwards_on_each_van():
    day = run([bag(n) for n in range(6)], [van()], HUB, travel=TRAVEL,
              cut_off=CUT_OFF)
    times = [v.at for v in day.visits if v.van_id == "V1"]
    assert times == sorted(times)


# ------------------------------------------------------------------ §5.1.7

def test_a_bag_that_is_not_ready_is_a_visit_and_a_re_request():
    day = run([bag(1)], [van()], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-1": Incident.BAG_NOT_READY})
    visit, = day.visits
    assert visit.collected is False
    assert visit.re_request is True
    assert day.re_requests == ("BAG-1",)
    assert day.routes["V1"] == (), "nothing went aboard"


def test_a_broken_seal_is_collected_and_flagged():
    day = run([bag(1)], [van()], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-1": Incident.SEAL_BROKEN})
    visit, = day.visits
    assert visit.collected is True
    assert visit.flagged is True
    assert day.flagged == ("BAG-1",)


def test_a_closed_site_is_a_failed_pickup_and_a_re_request():
    day = run([bag(1)], [van()], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-1": Incident.SITE_CLOSED})
    assert day.collected == ()
    assert day.re_requests == ("BAG-1",)


def test_more_bags_than_expected_leaves_the_surplus_for_another_cycle():
    """§5.1.7: "collect what fits; remainder assigned to another van or a second visit"."""
    day = run([bag(1)], [van(), van(2)], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-1": Incident.MORE_BAGS},
              surplus_bags={"BAG-1": 2})
    assert "BAG-1" in day.collected
    assert {"BAG-1+1", "BAG-1+2"} <= set(day.collected), "the surplus is collected too"


def test_a_surplus_no_van_can_take_is_reported_rather_than_lost():
    day = run([bag(1)], [van(1, capacity_mailbags=1)], HUB, travel=TRAVEL,
              cut_off=CUT_OFF, incidents={"BAG-1": Incident.MORE_BAGS},
              surplus_bags={"BAG-1": 1})
    assert day.collected == ("BAG-1",)
    assert day.unplaced == ("BAG-1+1",)


def test_a_cancelled_request_is_removed_without_a_visit():
    """§5.1.7: "stop removed at next cycle"."""
    day = run([bag(1), bag(2)], [van()], HUB, travel=TRAVEL, cut_off=CUT_OFF,
              incidents={"BAG-1": Incident.CANCELLED})
    assert [v.mailbag_id for v in day.visits] == ["BAG-2"]
    assert "BAG-1" not in day.collected
    assert "BAG-1" not in day.unplaced


def test_the_cadence_is_the_documented_one_by_default():
    """§5.1.5's own "e.g. every 30 minutes", carried in assumptions."""
    assert assumptions.REOPT_CADENCE_MIN == 30
