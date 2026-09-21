"""What passes between the three slices of `docs/e2e/`.

Each model is a row of a slice document's own inputs/outputs table, and says so.
They exist for one reason: a slice must be runnable from a *file*, not only from
the slice before it, so that E2E-2 can be tested without first running E2E-1.
That makes the boundary a serialisation boundary, which is why these are
Pydantic models where the rest of `ddn/` is frozen dataclasses.

**They define no fields of their own.** §9 is "the only schema definition"
(CLAUDE.md), so a hand-off *composes* `ddn.model.records` entities rather than
restating their fields — `ReadyPool` carries `Envelope`s, not a parallel
declaration of package_id/lat/lon/priority that would drift from §9.1 silently.
What is declared here is only the shape of the hand-off itself: which envelopes,
grouped how, with what alongside them.

Nothing here imports `ddn.api`. §13 "depends on every module above it and
nothing depends on it" (CLAUDE.md), so the dependency runs the other way:
`ddn/api/schemas.py` may reuse these, and this module may not reuse it.

**One hand-off named by `docs/claude-code-task-prompts.md` is not here.** The
task list asks for `ReturnLoads` as an E2E-2 → E2E-3 hand-off. No table in
either document carries it in that direction: return loads are raised at a
depot by E2E-3 (e2e-3 §4, "Return load at the facility") and *consumed* by
E2E-2 (e2e-2 §4, "Return loads at depots ... | E2E-3"), so the arrow runs
3 → 2, which `ReturnLoad` below is. Inventing a second one to match the task
text would put a hand-off in the code that no slice document defines.

The real gap the name gestures at is on E2E-2's output side and is a
documentation gap, not a missing model: a circuit carries depot returns to the
hub, where they become §5.5's return-run input, and **e2e-2 §5 has no row for
them** — its only mention is `return_ids` inside a leg. Promote to e2e-2 §5
before anything depends on it.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ddn.contract import Excluded
from ddn.linehaul.circuit import Declined
from ddn.model.records import Envelope, Outcome, Status, TransferRequest
from ddn.simulation.metrics import Tally
from ddn.solver_adapter.postcheck import Violation

#: `PositionedPool.positioned` and `ReadyPool.ready` key hub-direct envelopes
#: under the hub's own id, per e2e-2 §2.2: "Hub-direct envelopes are not 'not
#: transported'; they are positioned at the hub and enter E2E-3's hub pool."
HUB = "HUB"


class _Handoff(BaseModel):
    """Frozen, and strict about unknown keys: a hand-off read from a file is
    the whole contract between two slices, so a key neither side recognises is
    a decomposition that has drifted, not a field to ignore."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class VanRelease(_Handoff):
    """e2e-1 §4: "Van release events: van_id, time back at hub, unloaded".

    §7.1's line-haul bullet is about both facts, not one: a van does not depart
    on line-haul until it is back *and* unloaded, so a release that recorded
    only the arrival would satisfy half a constraint.
    """

    vehicle_id: str
    back_at_hub: datetime
    unloaded: bool


class ReadyPool(_Handoff):
    """E2E-1 → E2E-2. e2e-1 §4 rows 3-6; e2e-2 §4 rows 1 and 4.

    The four things E2E-2 needs from the pickup-and-processing day: what is
    Ready and where it is pre-sorted, what is held back, what assembly work
    rolled, and which vans are free to carry any of it.
    """

    collection_day: date
    #: Ready envelopes by pre-sorted facility id, hub-direct under `HUB`.
    #: Envelopes still in processing carry their `expected_ready_at`, which is
    #: what e2e-2 §3's waiting decision reads.
    ready: dict[str, tuple[Envelope, ...]] = Field(default_factory=dict)
    #: e2e-1 §4: "Held envelopes with reason (disputed / low-confidence geocode
    #: / straddling zip)".
    held: tuple[Excluded, ...] = ()
    #: e2e-1 §4: the assembly queue's "rolled set identified" — §5.2.3's
    #: overflow, ordered by (priority desc, SLA asc, arrival) before the cut.
    rolled_assembly: tuple[Excluded, ...] = ()
    released: tuple[VanRelease, ...] = ()


class PositionedPool(_Handoff):
    """E2E-2 → E2E-3. e2e-2 §5 rows 2, 3 and 6; e2e-3 §3 row 1.

    e2e-2 §1 ends "every Ready envelope is either **positioned** at its
    dispatching facility before that facility's morning route release, or
    explicitly **rolled** to the next day's line-haul with a reason" — so these
    two fields are that boundary, and an envelope in neither is the failure the
    slice exists to make visible.
    """

    delivery_day: date
    #: package_ids by dispatching facility, hub-direct under `HUB` (e2e-2 §2.2).
    positioned: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: e2e-2 §5: "Rolled envelopes with reason: no van / weight / deadline
    #: unreachable / held-straddle".
    rolled: tuple[Excluded, ...] = ()
    #: §13.1's obligation, empty on success — from `check_day_constraints`,
    #: which is the half of §7.1 a single solve cannot see.
    violations: tuple[Violation, ...] = ()


class TransferOutcomes(_Handoff):
    """E2E-2 → E2E-3. e2e-2 §5 row 4: "Transfers carried, deferred (reason),
    or sent to returns (deadline unreachable)".

    E2E-3 raises transfers and E2E-2 carries them, so this is the answer coming
    back: the depot learns tonight which of yesterday's requests arrived.
    """

    carried: tuple[str, ...] = ()
    #: transfer_id with one of `circuit.NO_VAN_LEG` / `MISSES_DEADLINE` /
    #: `OVER_CAPACITY` — §9.2's "transfers not carried, with reason".
    deferred: tuple[Declined, ...] = ()
    #: Past saving by a later circuit: the envelope goes back instead.
    to_returns: tuple[str, ...] = ()


class ReturnLoad(_Handoff):
    """E2E-3 → E2E-2, or → §5.5's return run. e2e-3 §4 row 4; e2e-2 §4 row 3.

    e2e-3 §2.5's two destinations for one shape: at a depot the load waits for
    the next van leg and rides to the hub; at the hub it enters tonight's
    return run directly. The slice that produces it does not choose — the
    facility it was produced at does.
    """

    facility_id: str
    package_ids: tuple[str, ...] = ()
    weight_g: int = 0


class DayOutcomes(_Handoff):
    """E2E-3 → E2E-2 next day, and → reporting. e2e-3 §4 rows 1-3, 5, 6.

    One facility's delivery day, after every dispatched envelope has an
    outcome. §3.1's one-day lag is why this is a hand-off at all: the
    consequences land on tomorrow's line-haul, not on tonight's.
    """

    delivery_day: date
    facility_id: str
    #: package_id → the §5.2.6 outcome recorded against it.
    outcomes: dict[str, Outcome] = Field(default_factory=dict)
    #: package_id → the state §5.2.6 moves it to. Delivered is terminal;
    #: Postponed returns to Ready for a retry; the rest enter the return flow.
    next_state: dict[str, Status] = Field(default_factory=dict)
    #: e2e-3 §4: "reason (time / count / locked-out / SLA expired)".
    unassigned: tuple[Excluded, ...] = ()
    #: e2e-3 §4: raised here, carried by E2E-2 — reason=address_correction.
    transfers: tuple[TransferRequest, ...] = ()
    returns: ReturnLoad | None = None
    tally: Tally | None = None
    #: §13.1's obligation on this slice's routing result, empty on success.
    violations: tuple[Violation, ...] = ()
