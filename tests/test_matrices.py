"""The recorded road table is what it says it is.

`tests/matrices.py` makes three claims in prose — real roads, built through the
platform, and points that had to move to reach one. Prose about data goes stale
exactly the way prose about code does, so each is checked against the file.
"""

from __future__ import annotations

import pytest

from tests.matrices import flat_matrix, provenance, road_matrix

HUB = {"lat": 9.9333, "lon": -84.0833}
D1 = {"lat": 9.9981, "lon": -84.1197}


def test_the_table_was_built_through_the_platform():
    """Not from OSRM's /table: the fixture and production share a code path."""
    where = provenance()
    assert "build_large_matrix" in where["source"]
    assert where["matrix_version"].startswith("osrm:driving:"), (
        "the platform's own version, not a hand-written description")


def test_the_road_is_longer_than_the_straight_line():
    """The whole reason the suite stopped computing distances.

    Straight-line travel is not a neutral approximation; here it is 40% short.
    """
    import math

    road = road_matrix([HUB, D1]).distance(0, 1)
    scale = math.cos(math.radians((HUB["lat"] + D1["lat"]) / 2))
    crow = 6_371_000 * math.hypot(
        math.radians(D1["lat"] - HUB["lat"]),
        math.radians(D1["lon"] - HUB["lon"]) * scale)

    assert road == 11551
    assert road / crow == pytest.approx(1.40, abs=0.02)


def test_the_snap_distances_are_recorded_and_still_what_we_say():
    """Jitter puts synthetic points where no road is; the fixture admits it."""
    where = provenance()
    assert where["snap_threshold_m"] == 100.0
    assert where["snapped_beyond_threshold"] == 39
    assert where["furthest_snap_m"] == pytest.approx(7671, abs=1)


def test_a_coordinate_that_was_never_recorded_fails_loudly():
    """Falling back to a computed distance is what this module exists to stop."""
    with pytest.raises(KeyError, match="re-record"):
        road_matrix([HUB, {"lat": 0.0, "lon": 0.0}])


def test_a_flat_matrix_is_not_pretending_to_be_geography():
    matrix = flat_matrix(3)
    assert matrix.version == "flat"
    assert matrix.distance(0, 1) == matrix.distance(1, 2)
