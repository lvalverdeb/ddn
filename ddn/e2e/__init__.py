"""The three mini end-to-ends of `docs/e2e/`, as runnable slices.

What crosses between the slices is in `handoff.py`, so a slice can be run from a
file rather than from the slice before it. One shared module, not one per slice
package: three copies of one contract are three places for it to drift.

The slice packages themselves — each a `scenario.py` and a `run.py` that calls
the §5 modules and computes nothing — are not built yet. `handoff.py` is what
exists.
"""
