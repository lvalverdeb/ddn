"""Every notebook in `notebooks/` is executed.

A notebook is prose about code, and prose about code fails by agreeing with
whatever it was written against. Nothing here asserts what the outputs *say* --
that would be a second copy of the modules' own tests -- but a notebook that no
longer runs is caught the moment it stops running, which is the failure that
actually happens: a renamed argument, a moved module, a changed return shape.

Discovered from the directory rather than listed, so a new notebook is covered
by existing it.
"""

from __future__ import annotations

from pathlib import Path

import nbformat
import pytest
from nbclient import NotebookClient

NOTEBOOKS = sorted((Path(__file__).resolve().parent.parent / "notebooks")
                   .glob("*.ipynb"))


def test_there_are_notebooks_to_run():
    assert NOTEBOOKS, "notebooks/ is empty; this test would pass vacuously"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_the_notebook_runs(path: Path):
    """Executed from the repository root, as a reader would open it."""
    book = nbformat.read(path, as_version=4)
    NotebookClient(book, timeout=300, kernel_name="python3",
                   resources={"metadata": {"path": str(path.parent.parent)}}).execute()


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.stem)
def test_the_notebook_ships_without_outputs(path: Path):
    """Stored outputs are a second copy of results, and they go stale.

    They also make a diff unreadable and can carry data nobody meant to commit.
    The suite runs the notebook, so the outputs are worth nothing in git.
    """
    book = nbformat.read(path, as_version=4)
    for number, cell in enumerate(book.cells, start=1):
        if cell.cell_type == "code":
            assert not cell.get("outputs"), (
                f"cell {number} of {path.name} has stored output. "
                "Run `make nb-clean` before committing.")
            assert cell.get("execution_count") is None, (
                f"cell {number} of {path.name} was executed and saved. "
                "Run `make nb-clean` before committing.")
