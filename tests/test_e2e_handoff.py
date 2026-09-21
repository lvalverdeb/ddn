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
from datetime import datetime, time

import pytest

from ddn.contract import Excluded
from ddn.e2e import handoff
from ddn.linehaul.circuit import MISSES_DEADLINE, Declined
from ddn.model.records import Outcome, Status, TransferReason, TransferRequest
from ddn.simulation.metrics import Tally
from ddn.solver_adapter.postcheck import Violation
from tests.fixtures import peak_day

DAY = peak_day.load()


def _at(day, hour, minute=0):
    """§10's own dates, not invented ones — a hand-off's times are its day's."""
    return datetime.combine(day, time(hour, minute))


def _envelope():
    return DAY.ready()[0]


def _ready_pool():
    envelope = _envelope()
    return handoff.ReadyPool(
        collection_day=DAY.collection_day,
        ready={handoff.HUB: (envelope,)},
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
            deadline=_at(DAY.delivery_day, 7)),),
        returns=handoff.ReturnLoad(facility_id="D1",
                                   package_ids=("P-4", "P-5"), weight_g=400),
        tally=Tally(ready_pool=10, dispatched=8, delivered=7),
        violations=(Violation(bullet="one vehicle per envelope per day",
                              detail="P-6 on two routes", vehicle_id="M1"),))


HANDOFFS = [
    pytest.param(_ready_pool, id="ReadyPool"),
    pytest.param(lambda: handoff.PositionedPool(
        delivery_day=DAY.delivery_day,
        positioned={handoff.HUB: ("P-1",), "D1": ("P-2", "P-3")}),
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
    """The hand-offs compose §9.1's records; they do not redeclare them.

    `ReadyPool.ready` holding `Envelope` rather than a parallel list of
    package_id/lat/lon/priority is what keeps §9.1 the only place those field
    names are written down — so a §9.1 change reaches the slices by breaking
    them, not by leaving them quietly describing the old shape.
    """
    envelope = _envelope()
    pool = handoff.ReadyPool(collection_day=DAY.collection_day,
                             ready={handoff.HUB: (envelope,)})
    assert pool.ready[handoff.HUB][0] is envelope

    fields = {name for model in (handoff.ReadyPool, handoff.PositionedPool,
                                 handoff.TransferOutcomes, handoff.ReturnLoad,
                                 handoff.DayOutcomes, handoff.VanRelease)
              for name in model.model_fields}
    assert not fields & {"package_id", "lat", "lon", "priority", "sla_date",
                         "geocode_confidence", "coord_source", "status"}


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
