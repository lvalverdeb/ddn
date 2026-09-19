"""`ddn/assumptions.py` must say what `docs/assumptions.md` says.

Two copies of a placeholder drift, and a stale one is believed: that is how a
platform version survived a bump in three files and stayed wrong in the fourth.
This test parses the document and compares it to the module, so the document is
the source and the module is the thing checked.
"""

from __future__ import annotations

import ast
import re
from datetime import time
from pathlib import Path

import pytest

from ddn import assumptions

DOC = (Path(__file__).resolve().parent.parent
       / "docs" / "assumptions.md").read_text(encoding="utf-8")

KEY = re.compile(r"`([A-Z_]+)`")
TIME = re.compile(r"^(\d{1,2}):(\d{2})$")
FACILITY_ROW = re.compile(
    r"^\|\s*(HUB|D\d)\s*\|\s*([^|]+?)\s*\|\s*(-?[\d.]+)\s*\|\s*(-?[\d.]+)\s*\|"
    r"\s*(—|\d+)\s*\|"
)


def _documented() -> dict[str, object]:
    """Every `KEY` row of the document's tables, as Python values."""
    found: dict[str, object] = {}
    for line in DOC.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        keys = KEY.findall(cells[0])
        values = [value.strip() for value in cells[1].split("/")]
        if not keys or len(keys) != len(values):
            continue
        for key, raw in zip(keys, values, strict=True):
            if match := TIME.match(raw):
                found[key] = time(int(match[1]), int(match[2]))
            elif raw.isdigit():
                found[key] = int(raw)
    return found


DOCUMENTED = _documented()


def test_the_document_has_rows_to_check():
    assert len(DOCUMENTED) >= 16, f"parsed only {sorted(DOCUMENTED)}"


@pytest.mark.parametrize("key", sorted(DOCUMENTED))
def test_the_module_matches_the_document(key):
    assert hasattr(assumptions, key), f"{key} is documented but not in the module"
    assert getattr(assumptions, key) == DOCUMENTED[key]


def test_the_module_places_no_value_the_document_does_not_carry():
    scalars = {
        name for name in vars(assumptions)
        if name.isupper() and isinstance(getattr(assumptions, name), int | time)
    }
    assert scalars <= set(DOCUMENTED), (
        f"undocumented placeholders: {sorted(scalars - set(DOCUMENTED))}"
    )


def test_facility_placeholders_match_the_documents_table():
    rows = [FACILITY_ROW.match(line) for line in DOC.splitlines()]
    table = {
        m[1]: (m[2], float(m[3]), float(m[4]), 0 if m[5] == "—" else int(m[5]))
        for m in rows if m
    }
    assert len(table) == 7, "§3.1 has a hub and six secondary depots"
    assert {f.facility_id for f in assumptions.FACILITIES} == set(table)
    for facility in assumptions.FACILITIES:
        locality, lat, lon, transit = table[facility.facility_id]
        assert (facility.locality, facility.lat, facility.lon) == (locality, lat, lon)
        assert facility.transit_from_hub_min == transit


def test_the_absent_gaps_have_no_placeholder():
    """§11's targets are named absent, not filled in.

    An invented SLA target reads as a commitment the customer never made, which
    is a different kind of wrong from an invented service time: the second is a
    guess, the first is a promise.

    §8's cost ratio used to be checked here too, and this test is the reason it
    is worth saying what happened. It looked for names matching `COST|RATIO` in
    `ddn.assumptions` and found none, and passed for months while
    `contract.PRIZE_SCALE` and three `models/*.json` objective blocks carried
    the ratio in full. A test that greps one module for a naming convention
    cannot see a value in another module, or in a JSON file, and the property it
    claimed to establish was never the property it checked. The weights are
    registered now, so what is left here is the genuinely absent gap; the
    duplication of a registered value is checked by
    `test_no_module_repeats_a_registered_placeholder`.
    """
    forbidden = re.compile(r"SLA_TARGET|_TARGET_RATE|_COMPLIANCE")
    assert not [name for name in vars(assumptions) if forbidden.search(name)]
    assert "Deliberately absent" in DOC
    assert "PICKUP_RESPONSE_TARGET_H" in vars(assumptions), (
        "the one §11-shaped value that is a service metric rather than a "
        "commitment should stay registered, not be caught by the pattern above")


def test_the_module_docstring_counts_the_absent_list_correctly():
    """The docstring names how many gaps go unfilled, so it can be wrong.

    It said two for as long as the list had three — §9.2's unreachable-address
    reason code was added to the document and the sentence counting it was not.
    A prose count agrees with a stale world silently; only reading the list it
    claims to count catches it.
    """
    absent = DOC.split("## Deliberately absent", 1)[1]
    bullets = re.findall(r"^- \*\*", absent, re.MULTILINE)
    words = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    assert words[len(bullets)] in assumptions.__doc__, (
        f"the document lists {len(bullets)} deliberately-absent gaps; "
        "ddn/assumptions.py's docstring counts a different number")


def test_the_pickup_model_carries_the_documented_mailbag_capacity():
    """§7.1's `[TBD]` reaches a model file, which is a second place to drift.

    `models/ddn-pickup.json` declares the van's mailbag capacity so that
    `servicemodel.build` can size the fleet, and `solver_adapter.problem`
    cross-checks it against each §9.1 vehicle row. That makes three copies of
    one unsupplied number, so the model is held to the document too.
    """
    import json

    model = json.loads(
        (Path(__file__).resolve().parent.parent
         / "models" / "ddn-pickup.json").read_text(encoding="utf-8"))
    capacities = {spec["class"]: spec["capacities"] for spec in model["fleet"]}
    assert capacities["VAN"]["mailbags"] == assumptions.MAILBAGS_PER_VAN


@pytest.mark.parametrize("name", ["ddn-lastmile", "ddn-pickup", "ddn-return"])
def test_every_model_prices_its_fleet_from_the_documented_weights(name):
    """§8's weights reach the solver through the model files, so they drift.

    `contract.costs` reads the three cost terms off each `fleet[]` entry,
    because that is where the platform puts a per-vehicle cost and where this
    repository already reads capacities. That makes the model file a second
    copy of a registered placeholder, which is exactly the shape
    `MAILBAGS_PER_VAN` has above -- so it gets the same treatment, and the
    document stays the source.

    Without this the failure is quiet: a cost changed in one model file and not
    in `docs/assumptions.md` would simply be what the solver used, and the page
    claiming to list every stand-in would be wrong about one that decides which
    envelopes get delivered.
    """
    import json

    model = json.loads(
        (Path(__file__).resolve().parent.parent
         / "models" / f"{name}.json").read_text(encoding="utf-8"))

    for spec in model["fleet"]:
        assert spec["cost_per_metre"] == assumptions.COST_PER_METRE
        assert spec["fixed_cost"] == assumptions.VEHICLE_FIXED_COST
        assert spec["cost_per_second"] == assumptions.COST_PER_SECOND


#: The placeholders whose duplication is worth hunting: fleet sizes and
#: durations. Deliberately not every registered value — see the test's
#: docstring for why a blanket check is worse than useless.
GUARDED = frozenset({
    "MOTORBIKES_TOTAL", "VANS_TOTAL", "ENVELOPES_PER_BIKE", "MAILBAGS_PER_VAN",
    "PICKUP_STOP_MIN", "RETURN_STOP_MIN", "FACILITY_UNLOAD_MIN",
    "VAN_IDLE_RETURN_MIN", "REOPT_CADENCE_MIN", "EQUIDISTANT_MARGIN_M",
    "RECONCILE_PER_HOUR", "ASSEMBLY_PER_HOUR", "SORT_PER_HOUR",
})

#: Constants that hold a number a placeholder also holds, legitimately: each is
#: a figure the spec states outright, not a stand-in for one it withholds.
#: §4.1's 200 g default weight, §7.4's ten-minute delivery, §11's 20–30 band.
NOT_A_DUPLICATE = frozenset({
    "DEFAULT_WEIGHT_G", "DEFAULT_SERVICE_MIN", "EXPECTED_PER_BIKE",
})


def _guarded_values() -> dict[int, list[str]]:
    """Every guarded placeholder, in the units a module might spell it in.

    A value maps to *every* name that holds it, not the first: 120 is both
    `MOTORBIKES_TOTAL` and `ASSEMBLY_PER_HOUR`, and naming one of them in a
    failure would send a reader to the wrong constant.
    """
    found: dict[int, list[str]] = {}
    for name in sorted(GUARDED):
        value = getattr(assumptions, name)
        found.setdefault(value, []).append(name)
        # The codebase's own idiom for a minutes placeholder is `X * 60`, so a
        # duplicate is as likely to be in seconds as in minutes.
        found.setdefault(value * 60, []).append(f"{name} * 60")
    return found


def _planted_literals(tree: ast.AST) -> list[tuple[int, int, str]]:
    """`(value, line, what)` for every literal a placeholder could have been.

    Module-level assignments and default arguments only. That is where a
    placeholder gets copied — someone needs a number, types it, and moves on —
    and it keeps the check away from loop bounds, tuple indices, rounding
    digits and format widths, which is where a blanket scan drowns.
    """
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            targets = node.targets[0]
            values = (node.value.elts
                      if isinstance(node.value, ast.Tuple) else [node.value])
            if isinstance(targets, ast.Tuple):
                names = [e.id for e in targets.elts if isinstance(e, ast.Name)]
            for name, value in zip(
                    names or [""] * len(values), values, strict=False):
                if (isinstance(value, ast.Constant)
                        and isinstance(value.value, int)
                        and not isinstance(value.value, bool)
                        and name not in NOT_A_DUPLICATE):
                    found.append((value.value, value.lineno, name or "assignment"))

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        args = node.args
        for default in (*args.defaults, *(d for d in args.kw_defaults if d)):
            if (isinstance(default, ast.Constant)
                    and isinstance(default.value, int)
                    and not isinstance(default.value, bool)):
                found.append((default.value, default.lineno,
                              f"{node.name}() default"))
    return found


def test_no_module_repeats_a_registered_placeholder():
    """A placeholder written out in a module is a placeholder nobody can find.

    `docs/assumptions.md` claims to carry every stand-in value. It can only be
    true if no module quietly holds its own copy, and nothing checked that: the
    test that used to sit here looked for names matching `COST|RATIO` inside
    `ddn.assumptions` itself, so it could not have seen a duplicate anywhere
    else, and it passed for months while §8's ratio sat in `contract.py` and
    three JSON files.

    **What this deliberately does not do.** A blanket "no literal in `ddn/` may
    equal any registered value" is unusable — 0 is a facility's transit from the
    hub and matches a hundred literals, 2 and 6 are the van earmarks and match
    every tuple index and coordinate precision, and 200 is both
    `POSTPONED_BAD_ADDRESS_PER_MILLE` and §4.1's default envelope weight.
    Measured before writing this: two real findings against eighteen false ones.
    So it checks a named set of fleet sizes and durations, at module-level
    assignments and default arguments, and exempts the constants that hold a
    figure the spec actually supplies.

    And it checks `value * 60`, because minutes-to-seconds is how these get
    copied. Without that it misses the one the repository already knew about:
    `returns.SERVICE_SECONDS = 600` is `RETURN_STOP_MIN`, and `600 != 10`.
    """
    guarded = _guarded_values()
    root = Path(__file__).resolve().parent.parent / "ddn"
    offences = []

    for path in sorted(root.rglob("*.py")):
        if path.name == "assumptions.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for value, line, what in _planted_literals(tree):
            if value in guarded:
                names = " or ".join(f"assumptions.{n}" for n in guarded[value])
                offences.append(
                    f"{path.relative_to(root.parent)}:{line} {what} = {value} "
                    f"is {names}")

    assert not offences, (
        "a registered placeholder is written out again in a module; read it "
        "from ddn.assumptions so that docs/assumptions.md stays the one place "
        "a stand-in can be found and changed:\n  " + "\n  ".join(offences))


def test_the_model_files_state_no_placeholder_the_document_does_not():
    """A model file is a second place for a `[TBD]` to live, so it drifts.

    `models/*.json` cannot simply drop its copies: `shift`, `windows` and
    `per_depot` are in the platform's `REQUIRED_KEYS` and `servicemodel.build`
    dereferences them unguarded, so deleting one is a `KeyError`, not a
    simplification. What can be done is hold them to the document — the same
    treatment `MAILBAGS_PER_VAN` and §8's cost terms already get.

    Three values genuinely duplicate a registered placeholder. The rest of the
    shift and window numbers match nothing in `ddn/assumptions.py` at all —
    `ddn-lastmile`'s 08:00–16:00 and `ddn-return`'s 17:00–21:00 are
    unregistered stand-ins in their own right, which is a gap this test
    records rather than closes: registering them is a decision about four more
    invented values, not a mechanical fix.
    """
    import json

    def model(name: str) -> dict:
        return json.loads((Path(__file__).resolve().parent.parent
                           / "models" / f"{name}.json").read_text(encoding="utf-8"))

    pickup, returns = model("ddn-pickup"), model("ddn-return")

    assert pickup["service"]["fixed_seconds"] == assumptions.PICKUP_STOP_MIN * 60
    assert returns["service"]["fixed_seconds"] == assumptions.RETURN_STOP_MIN * 60

    window, = pickup["windows"]
    assert window["start"] == _as_seconds(assumptions.SHIFT_START)
    assert window["end"] == _as_seconds(assumptions.PROCESSING_CUTOFF)


def test_no_model_file_still_carries_the_objective_nothing_reads():
    """`run.objective` was a third copy of §8's cost terms and reached nothing.

    `vrp.servicemodel.run_config` is its only reader and is never called in
    this repository; the costs that reach PyVRP come off `fleet[]` through
    `contract.costs`. Two test fixtures edited it believing they were pricing
    the solve, and were not. `run` itself stays — `lastmile.plan` reads its
    budget and seed.
    """
    import json

    for name in ("ddn-lastmile", "ddn-pickup", "ddn-return"):
        run = json.loads((Path(__file__).resolve().parent.parent
                          / "models" / f"{name}.json").read_text())["run"]
        assert "objective" not in run, f"{name} carries an objective nothing reads"
        assert {"budget", "seed"} <= set(run), "lastmile.plan reads both"


def _as_seconds(clock: time) -> int:
    return clock.hour * 3600 + clock.minute * 60
