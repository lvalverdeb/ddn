"""The three mini end-to-ends of `docs/e2e/`, as runnable slices.

Each slice is a package with a `scenario.py` (what §10's day looks like to it)
and a `run.py` (which calls the §5 modules and computes nothing itself). What
crosses between them is in `handoff.py`, so a slice can be run from a file
rather than from the slice before it.
"""
