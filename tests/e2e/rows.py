"""The acceptance rows of `docs/e2e/e2e-1..3`, read out of the documents.

Every stub in this package is named by its row id and cites its slice
document. The row *text* is not copied here: it is parsed from the markdown
table, so a reworded row changes what the test claims rather than leaving the
test claiming something the document no longer says.

That is the same discipline `tests/test_peak_day.py` applies to §10 and
`tests/test_peak_day_transfers.py` to e2e-2's B3 — and the reason for it is on
record. §10 was restated in v0.13 and the figures that were checked against a
copy of themselves did not move.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent.parent / "docs" / "e2e"

SLICES = {
    "A": ("e2e-1-pickups-to-hub.md", "pickups to hub"),
    "B": ("e2e-2-hub-to-depots.md", "hub to depots"),
    "C": ("e2e-3-depot-delivery-and-outcomes.md", "depot delivery and outcomes"),
}

#: `| C7 | Same as C6 but at HUB | Rejected/defective enter tonight's run |`
ROW = re.compile(r"^\|\s*([ABC]\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")


def _rows(letter: str) -> dict[str, tuple[str, str]]:
    name, _ = SLICES[letter]
    path = DOCS / name
    if not path.exists():                                # pragma: no cover
        raise FileNotFoundError(
            f"{path} is gone; the acceptance rows of slice {letter} are "
            "defined there and this package cites them by id")
    found = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if match := ROW.match(line):
            found[match[1]] = (match[2], match[3])
    return found


ROWS: dict[str, tuple[str, str]] = {
    row_id: pair
    for letter in SLICES
    for row_id, pair in _rows(letter).items()
}

#: How many rows each slice document is expected to carry. A document that
#: loses a row would otherwise take its stub with it, silently: the stub is
#: generated from the table, so a row that vanishes leaves no failing test
#: behind, only a smaller suite.
EXPECTED = {"A": 9, "B": 9, "C": 12}


def document(row_id: str) -> str:
    """The slice document a row belongs to, for a test to cite."""
    return SLICES[row_id[0]][0]


def scenario(row_id: str) -> str:
    """The row's scenario column — what the test is to set up."""
    return ROWS[row_id][0]


def expected(row_id: str) -> str:
    """The row's expected column — what the test is to assert."""
    return ROWS[row_id][1]
