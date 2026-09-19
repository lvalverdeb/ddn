"""§5.3.2's inter-depot transfers: the lifecycle, the circuits, the bullets."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from ddn import linehaul
from ddn.linehaul import MAX_LOAD_G
from ddn.model import Status, TransferReason, TransferRequest, may
from ddn.solver_adapter import Day, check_day_constraints, postcheck

HOUR = 3600
TODAY = date(2026, 9, 16)
# §3.1 and §5.6 give clock times with no zone; the operation runs in one place,
# so datetimes here are naive and local, as they are throughout the modules.
RELEASE = datetime.combine(date(2026, 9, 17), time(7))
RAISED = datetime.combine(TODAY, time(18))


def depot(name: str, *, transit_min: int, release_h: int = 7) -> dict:
    return {"id": name, "route_release_time": release_h * HOUR,
            "transit_from_hub_min": transit_min}


def transfer(transfer_id: str = "T1", *, frm: str = "D1", to: str = "D3",
             deadline: datetime = RELEASE, grams: int = 200,
             reason: TransferReason = TransferReason.MISASSIGNMENT):
    return TransferRequest(transfer_id, f"PKG-{transfer_id}", frm, to, reason,
                           RAISED, deadline, weight_g=grams)


def envelopes(facility: str, count: int, *, grams: int = 200) -> list[dict]:
    return [{"package_id": f"{facility}-{i}", "facility_id": facility,
             "expected_ready_at": 16 * HOUR, "weight_g": grams}
            for i in range(count)]


# ------------------------------------------------------------- §5.2.6

def test_the_lifecycle_draws_the_transfer_path():
    """§5.2.6: "Transfer requested → In transfer → Ready (at new depot)"."""
    assert may(Status.POSTPONED, Status.TRANSFER_REQUESTED)
    assert may(Status.TRANSFER_REQUESTED, Status.IN_TRANSFER)
    assert may(Status.IN_TRANSFER, Status.READY)


def test_an_envelope_in_transfer_is_not_routable():
    """§7.1: "not routable until it arrives at the destination depot"."""
    assert not may(Status.IN_TRANSFER, Status.DISPATCHED)
    assert Status.IN_TRANSFER not in __import__(
        "ddn.model.lifecycle", fromlist=["ROUTABLE"]).ROUTABLE


def test_a_transfer_that_cannot_make_its_deadline_goes_back_instead():
    """§5.3.2: "otherwise it is returned to the customer via the hub"."""
    assert may(Status.TRANSFER_REQUESTED, Status.RETURN_RUN)


def test_a_transfer_to_the_same_depot_is_refused():
    with pytest.raises(ValueError, match="to itself"):
        transfer(frm="D1", to="D1")


def test_the_deadline_is_what_decides():
    """§9.1: min(receiving depot's next morning release, SLA date)."""
    one = transfer(deadline=RELEASE)
    assert one.makes(RELEASE - timedelta(minutes=1))
    assert not one.makes(RELEASE + timedelta(minutes=1))


# ------------------------------------------------------------- §5.3.2

def transit(_a: str, _b: str) -> int:
    """Inter-depot seconds. §3.1 gives none, so the caller supplies them."""
    return 40 * 60


def test_a_transfer_rides_a_circuit_through_its_origin():
    """§5.3.2: transfer loads are "picked up at earlier depots"."""
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 3), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()],
                          transit=transit)
    trip, = night.trips
    assert trip.transfer_ids == ("T1",)
    assert [(leg.from_facility, leg.to_facility) for leg in trip.legs] == [
        ("HUB", "D1"), ("D1", "D3")]
    assert night.declined == ()


def test_the_circuit_departs_early_enough_for_its_last_stop():
    """§5.3 departs as late as possible — for the *whole* circuit.

    A van timed only against D1 leaves at D1's own latest departure and then
    cannot reach D3 before its release. §5.3.2's constraint is arrival before
    the receiving depot's morning release, which is a property of the circuit.
    """
    depots = [depot("D1", transit_min=30), depot("D3", transit_min=45)]
    alone = linehaul.plan(depots, envelopes("D1", 3), [{"vehicle_id": "V1"}],
                          unload_seconds=1800)
    with_transfer = linehaul.plan(depots, envelopes("D1", 3),
                                  [{"vehicle_id": "V1"}], unload_seconds=1800,
                                  transfers=[transfer()], transit=transit)

    assert with_transfer.trips[0].departure < alone.trips[0].departure
    last = with_transfer.trips[0].legs[-1]
    assert last.arrival <= linehaul.DAY + 7 * HOUR


def test_a_transfer_nobody_passes_is_declined_with_a_reason():
    """§9.2: "transfers not carried, with reason"."""
    night = linehaul.plan([depot("D1", transit_min=30)], envelopes("D1", 2),
                          [{"vehicle_id": "VAN-01"}], unload_seconds=1800,
                          transfers=[transfer(frm="D5", to="D6")],
                          transit=transit)
    assert night.trips[0].transfer_ids == ()
    assert [d.reason for d in night.declined] == [linehaul.NO_VAN_LEG]


def test_without_inter_depot_transit_a_transfer_is_refused_not_guessed():
    """§3.1 supplies hub transit only; §12 Q8 asks for nothing else."""
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 2), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()])
    assert night.trips[0].transfer_ids == ()
    assert "transit" in night.declined[0].reason


def test_a_transfer_that_would_overload_the_van_is_declined():
    """§7.1: 500 kg across hub-origin, transfer and return envelopes."""
    heavy = envelopes("D1", 1, grams=MAX_LOAD_G)
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          heavy, [{"vehicle_id": "VAN-01"}], unload_seconds=1800,
                          transfers=[transfer(grams=1000)], transit=transit)
    assert [d.reason for d in night.declined] == [linehaul.OVER_CAPACITY]


def test_every_transfer_is_carried_or_declined():
    """The accounting §9.2 asks for: none may quietly vanish."""
    wanted = [transfer("T1"), transfer("T2", frm="D5", to="D6"),
              transfer("T3", grams=MAX_LOAD_G)]
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 2), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=wanted, transit=transit)
    carried = {tid for trip in night.trips for tid in trip.transfer_ids}
    declined = {d.transfer_id for d in night.declined}
    assert carried | declined == {"T1", "T2", "T3"}
    assert not (carried & declined)


def test_the_load_falls_as_the_circuit_drops_it():
    night = linehaul.plan([depot("D1", transit_min=30), depot("D3", transit_min=45)],
                          envelopes("D1", 3), [{"vehicle_id": "VAN-01"}],
                          unload_seconds=1800, transfers=[transfer()],
                          transit=transit)
    first, second = night.trips[0].legs
    assert first.weight_g == 3 * 200 + 200, "hub load plus the transfer"
    assert second.weight_g == 200, "only the transfer, after D1 is unloaded"
    assert not any(leg.overloaded for leg in night.trips[0].legs)


# ------------------------------------------------------------- §7.1

def test_section_7_1_now_enforces_every_bullet():
    assert len(postcheck.BULLETS) == 13
    assert postcheck.NOT_YET_ENFORCED == ()
    assert postcheck.COMBINED_LOAD in postcheck.BULLETS
    assert postcheck.TRANSFER_WITHIN_SLA in postcheck.BULLETS


def test_an_overloaded_leg_is_a_violation():
    """§7.1's combined-load bullet, which only a leg can break."""
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D1", package_ids=(), departure=0,
        arrival=1, returns=2,
        legs=(linehaul.Leg("HUB", "D1", 0, 1, weight_g=MAX_LOAD_G + 1),)),))
    found = check_day_constraints(Day(today=TODAY, linehaul=night))
    assert postcheck.COMBINED_LOAD in {v.bullet for v in found}


def test_a_transfer_arriving_after_its_deadline_is_a_violation():
    """§7.1: it should have gone to the return run instead."""
    late = transfer(deadline=datetime.combine(date(2026, 9, 17), time(3)))
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D3", package_ids=(), departure=0,
        arrival=1, returns=2, transfer_ids=("T1",),
        legs=(linehaul.Leg("D1", "D3", 0, linehaul.DAY + 6 * HOUR,
                           transfer_ids=("T1",), weight_g=200),)),))
    found = check_day_constraints(
        Day(today=TODAY, linehaul=night, transfers=[late]))
    assert postcheck.TRANSFER_WITHIN_SLA in {v.bullet for v in found}


def test_a_transfer_arriving_in_time_is_no_violation():
    night = linehaul.LinehaulPlan(trips=(linehaul.Trip(
        van_id="VAN-01", destination="D3", package_ids=(), departure=0,
        arrival=1, returns=2, transfer_ids=("T1",),
        legs=(linehaul.Leg("D1", "D3", 0, linehaul.DAY + 6 * HOUR,
                           transfer_ids=("T1",), weight_g=200),)),))
    found = check_day_constraints(
        Day(today=TODAY, linehaul=night, transfers=[transfer()]))
    assert postcheck.TRANSFER_WITHIN_SLA not in {v.bullet for v in found}
