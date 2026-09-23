"""`ddn/e2e/handoff.py` — the models the three slices pass between them.

The one property worth testing here is the one the module exists for: a slice
must be runnable from a *file*, so every hand-off has to survive being written
out and read back with nothing lost. A model that serialises but does not
round-trip would let E2E-2 be tested against a hand-off E2E-1 could not
actually have produced.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import fields
from datetime import datetime, time

import pytest

from ddn import returns
from ddn.contract import Excluded
from ddn.e2e import handoff
from ddn.linehaul.circuit import MISSES_DEADLINE, Declined
from ddn.model.records import Envelope, Outcome, Status, TransferReason, TransferRequest
from ddn.simulation.metrics import Tally
from ddn.solver_adapter.postcheck import Violation
from tests import peak_day_inputs
from tests.fixtures import peak_day

DAY = peak_day.load()


def _at(day, hour, minute=0):
    """§10's own dates, not invented ones — a hand-off's times are its day's."""
    return datetime.combine(day, time(hour, minute))


def _record():
    """One §9.1 envelope record in the shape the §5 modules pass around.

    Taken from the real builder rather than hand-written, and stamped with the
    sender's site the way `simulation.day._position` stamps it, because that is
    what §5.5 reads back off the record.
    """
    inflow = peak_day_inputs.build().kwargs["inflow"][0]
    return dict(inflow, expected_ready_at=8 * 3600,
                customer_lat=inflow["lat"], customer_lon=inflow["lon"])


def _ready_pool():
    return handoff.ReadyPool(
        collection_day=DAY.collection_day,
        ready={handoff.HUB: (_record(),)},
        uncollected=410,
        held=(Excluded("P-held", "in dispute"),),
        rolled_assembly=(),
        released=(handoff.VanRelease(vehicle_id="V1",
                                     back_at_hub=_at(DAY.collection_day, 16, 30),
                                     unloaded=True),))


def _day_outcomes():
    return handoff.DayOutcomes(
        delivery_day=DAY.delivery_day,
        facility_id="D1",
        outcomes={"P-1": Outcome.DELIVERED, "P-2": Outcome.POSTPONED},
        next_state={"P-1": Status.DELIVERED, "P-2": Status.READY},
        transfers=(TransferRequest(
            transfer_id="T-1", package_id="P-3",
            from_facility_id="D1", to_facility_id="D2",
            reason=TransferReason.ADDRESS_CORRECTION,
            created_at=_at(DAY.delivery_day, 11),
            deadline=_at(DAY.delivery_day, 7), priority=100.0),),
        returns=handoff.ReturnLoad(facility_id="D1",
                                   package_ids=("P-4", "P-5"), weight_g=400),
        tally=Tally(ready_pool=10, dispatched=8, delivered=7),
        violations=(Violation(bullet="one vehicle per envelope per day",
                              detail="P-6 on two routes", vehicle_id="M1"),))


HANDOFFS = [
    pytest.param(_ready_pool, id="ReadyPool"),
    pytest.param(lambda: handoff.PositionedPool(
        delivery_day=DAY.delivery_day,
        positioned={handoff.HUB: (_record()["package_id"],)},
        envelopes={handoff.HUB: (_record(),)}),
        id="PositionedPool"),
    pytest.param(lambda: handoff.TransferOutcomes(
        carried=("T-1",), deferred=(Declined("T-2", MISSES_DEADLINE),),
        to_returns=("T-3",)), id="TransferOutcomes"),
    pytest.param(lambda: handoff.ReturnLoad(
        facility_id="D3", package_ids=("P-9",), weight_g=200), id="ReturnLoad"),
    pytest.param(_day_outcomes, id="DayOutcomes"),
]


@pytest.mark.parametrize("build", HANDOFFS)
def test_a_handoff_survives_the_file_it_is_written_to(build):
    """Written out and read back is the same hand-off, field for field.

    Not `model_dump()` but `model_dump_json()`: the enums, dates and datetimes
    are exactly where a dict round-trip would pass and a file would not.
    """
    original = build()
    assert type(original).model_validate_json(original.model_dump_json()) == original


@pytest.mark.parametrize("build", HANDOFFS)
def test_a_handoff_refuses_a_field_neither_slice_knows(build):
    """§9 is the only schema definition, so a hand-off cannot grow one quietly.

    A slice writing a key the reader ignores is a decomposition that has
    drifted; silence there would surface later as a missing figure, at the
    far end of a chain, with nothing pointing back to here.
    """
    payload = build().model_dump()
    payload["carried_forward"] = ["P-99"]
    with pytest.raises(ValueError, match="carried_forward"):
        type(build()).model_validate(payload)


def test_the_models_do_not_restate_section_9s_fields():
    """The hand-offs carry §9.1's records whole; they do not redeclare them.

    Carrying the record rather than a chosen subset of its fields is what keeps
    §9.1 the only place those names are written down — and what stops a slice
    from silently losing a field its consumer needs, which is the defect the
    test below pins.
    """
    record = _record()
    pool = handoff.ReadyPool(collection_day=DAY.collection_day,
                             ready={handoff.HUB: (record,)})
    assert pool.ready[handoff.HUB][0] == record, "a key was dropped in transit"

    fields = {name for model in (handoff.ReadyPool, handoff.PositionedPool,
                                 handoff.TransferOutcomes, handoff.ReturnLoad,
                                 handoff.DayOutcomes, handoff.VanRelease)
              for name in model.model_fields}
    assert not fields & {"package_id", "lat", "lon", "priority", "sla_date",
                         "geocode_confidence", "coord_source", "status"}


def test_a_carried_record_keeps_the_sender_site_an_envelope_cannot_hold():
    """Why `ready` carries `dict` and not `ddn.model.records.Envelope`.

    §9.1's envelope table has no sender coordinates — §9.1 puts the customer's
    site on Mailbags and on Return-run stops. But §5.5 returns an envelope *to
    the sender*, so `returns.sites` reads `customer_lat` straight off the
    record (`ddn/returns/run.py:128`) and `simulation.day._return_run` raises
    rather than guess when it is missing.

    So an `Envelope`-shaped hand-off does not make a slice *disagree* with the
    simulator — it makes the slice **crash**, one stage after the field was
    dropped, with nothing pointing back to the hand-off. This pins both halves:
    `Envelope` genuinely cannot hold it, and the hand-off genuinely carries it.
    """
    assert "customer_lat" not in {f.name for f in fields(Envelope)}, (
        "Envelope now carries the sender site; §9.1 changed, and `ready` could "
        "go back to holding Envelopes — check §9.1 before simplifying")

    pool = _ready_pool()
    carried = pool.ready[handoff.HUB][0]
    assert carried["customer_lat"] == carried["lat"]

    # §5.5 gates on the outcome, so mark it the way `day.py:223` marks one
    # going back. Without the sender site this call raises inside `sites`.
    going_back = dict(carried, sla_expired=True)
    stop, = returns.sites([going_back], hub_id=carried["facility_id"])
    assert stop.lat == carried["customer_lat"]
    assert stop.package_ids == (carried["package_id"],)


def test_nothing_in_the_slices_imports_the_api():
    """CLAUDE.md: `ddn/api/` "depends on every module above it and nothing
    depends on it". A hand-off importing `ddn.api.schemas` would invert that
    and make the service layer a prerequisite for running a slice offline.

    Read as syntax, not as text: the first version of this test grepped the
    source and failed on the paragraph of the module docstring that explains
    the rule. A prose mention is not an edge.
    """
    for path in sorted(pathlib.Path("ddn/e2e").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n == "ddn.api" or n.startswith("ddn.api.")
                           for n in names), f"{path}: {names}"


def test_the_e2e_2_to_3_return_hand_off_is_not_invented():
    """`docs/claude-code-task-prompts.md` names `ReturnLoads` as an E2E-2 →
    E2E-3 hand-off. No table in either slice document carries it that way —
    e2e-3 §4 raises the load and e2e-2 §4 consumes it, so the arrow is 3 → 2.

    This pins the absence, because the cheapest way to "fix" a failing chain
    later is to add the model the task text asks for and let the slices agree
    with each other instead of with the documents.
    """
    assert not hasattr(handoff, "ReturnLoads")
    e2e2 = pathlib.Path("docs/e2e/e2e-2-hub-to-depots.md").read_text()
    outputs = e2e2.split("## 5. Outputs")[1].split("## 6.")[0]
    assert "Return load" not in outputs, (
        "e2e-2 §5 now has a return-load row: promote it to a hand-off model "
        "rather than leaving this test pinning its absence")
