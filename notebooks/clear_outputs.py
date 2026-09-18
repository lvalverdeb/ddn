"""Strip stored outputs from the notebooks. `make nb-clean`.

Running a notebook is the point of it, and saving the result is what Jupyter
does when you hit save. The outputs are just not worth carrying in git: they
are a second copy of results the suite already produces, they go stale against
the code beside them, and they turn a two-line change into an unreadable diff.

So: run them freely, clear before committing. `tests/test_notebooks.py` fails
if outputs are present, and names this script when it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import nbformat

NOTEBOOKS = Path(__file__).resolve().parent


def clear(path: Path) -> bool:
    """Drop every output. Returns whether anything changed."""
    book = nbformat.read(path, as_version=4)
    dirty = False
    for cell in book.cells:
        if cell.cell_type != "code":
            continue
        if cell.get("outputs") or cell.get("execution_count") is not None:
            cell["outputs"] = []
            cell["execution_count"] = None
            dirty = True
    if dirty:
        nbformat.write(book, path)
    return dirty


def main() -> int:
    cleared = [p.name for p in sorted(NOTEBOOKS.glob("*.ipynb")) if clear(p)]
    print(f"cleared: {', '.join(cleared)}" if cleared else "nothing to clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
