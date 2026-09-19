"""`ddn/assumptions.py` must say what `docs/assumptions.md` says.

Two copies of a placeholder drift, and a stale one is believed: that is how a
platform version survived a bump in three files and stayed wrong in the fourth.
This test parses the document and compares it to the module, so the document is
the source and the module is the thing checked.
"""

from __future__ import annotations

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
