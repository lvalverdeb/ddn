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


def test_the_blocking_unknowns_have_no_placeholder():
    """§8's cost ratio and §11's targets are named absent, not filled in.

    A stand-in cost ratio is what produced the figure `capacity-finding.md`
    exists to disown, and an invented SLA target would read as a commitment the
    customer never made.
    """
    forbidden = re.compile(r"COST|RATIO|SLA_TARGET|_TARGET_RATE")
    assert not [name for name in vars(assumptions) if forbidden.search(name)]
    assert "Deliberately absent" in DOC
