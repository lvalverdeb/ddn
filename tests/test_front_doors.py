"""What CLAUDE.md and README.md claim about this repository, checked.

Both are front doors: a reader takes their word before running anything, so a
stale claim misleads exactly the person with the least context to catch it.

The test count is here because it drifted three times in one sitting — once per
change that added a test — and each time it was noticed by a person rather than
by the suite. `prose about code fails by agreeing`: nothing breaks when a
number in a document goes stale, which is why it goes stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONT_DOORS = ("CLAUDE.md", "README.md")

#: `make test  # 728 tests; no gateway ...` in CLAUDE.md and the comparable
#: line in README.md. Matched loosely on purpose: what must not drift is the
#: number, not the sentence around it.
COUNT = re.compile(r"#\s*(\d+) tests\b")


def _claimed() -> dict[str, list[int]]:
    """Every test count each front door states."""
    found = {}
    for name in FRONT_DOORS:
        hits = COUNT.findall((ROOT / name).read_text(encoding="utf-8"))
        assert hits, (
            f"{name} no longer states a test count. If that is deliberate, "
            "delete this test rather than leaving it matching nothing.")
        found[name] = [int(hit) for hit in hits]
    return found


def _narrowed(config: pytest.Config) -> bool:
    """Whether this run collected less than the whole suite.

    A subset run knows its own size and nothing about the suite's, so it has
    no business failing a claim about the total. Re-collecting in a subprocess
    to find out would cost more than the whole suite takes.
    """
    paths = {str(ROOT / "tests"), "tests"}
    return bool(
        config.option.keyword
        or config.option.markexpr
        or getattr(config.option, "lf", False)
        or getattr(config.option, "ff", False)
        or [arg for arg in config.args if arg.rstrip("/") not in paths])


def test_the_front_doors_state_the_real_test_count(request):
    """`make test` runs `pytest tests/ -q`; both files say how many that is."""
    if _narrowed(request.config):
        pytest.skip("a narrowed run does not know the whole suite's size")

    collected = request.session.testscollected
    for name, counts in _claimed().items():
        for claimed in counts:
            assert claimed == collected, (
                f"{name} says {claimed} tests; the suite collects {collected}. "
                f"Update the number in {' and '.join(FRONT_DOORS)}.")


def test_both_front_doors_agree_with_each_other():
    """Two copies of a number drift apart before either is noticed — which is
    how 718 survived in README after CLAUDE.md was corrected."""
    claimed = _claimed()
    assert len({count for counts in claimed.values() for count in counts}) == 1, (
        f"the front doors disagree: {claimed}")
