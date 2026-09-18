"""§3.3's facility assignment rule, on road distance.

§3.3 is unambiguous: an envelope goes to its "nearest facility **by road
distance**". This module used to rank by straight line, justified by a
docstring quoting §3.3 as accepting straight-line "as a first approximation to
be validated against operations". That sentence was real — in the spec as it
stood before v0.10. The v0.10 rewrite removed it and hardened the rule, and the
code went on citing a permission the document had withdrawn, which is why the
deviation survived being read.

**The matrix comes from the caller**, as it does for every other stage. §5.2.4
sorts at upload-file receipt, so the caller is whoever has a gateway then; this
module will not reach for one, any more than `lastmile` or `returns` do. A
straight line is not offered as a fallback: one silently wrong answer per
envelope is worse than a refusal, because it moves an envelope to a depot that
is nearer on paper and further by road, and nothing downstream can tell.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vrp.model import TravelMatrix, UnreachableArc


class NoRoadPath(ValueError):
    """No facility is reachable by road from this envelope's coordinates.

    §9.2 has no reason code for it — `capacity-finding.md` §4 records that gap
    — so it is raised rather than resolved into an arbitrary facility.
    """


def ranked(package: Mapping[str, Any], facilities: Sequence[Mapping[str, Any]],
           matrix: TravelMatrix, index: Mapping[str, int]) -> tuple[
               tuple[str, int], ...]:
    """Every facility by road metres from the package, nearest first.

    Args:
        package: a §9.1 record; `package_id` identifies its row in `index`.
        facilities: §3.1 rows, each with an `id` in `index`.
        matrix: road travel spanning at least the package and the facilities.
        index: id -> matrix row, for the package and every facility.

    Raises:
        KeyError: if the package or a facility has no row. A missing row is a
            caller that built the matrix over a different set, and guessing
            which row was meant is how travel lands on the wrong stop.
        NoRoadPath: if no facility is reachable from the package.
    """
    origin = index[package["package_id"]]
    reachable = []
    for facility in facilities:
        try:
            metres = matrix.distance(origin, index[facility["id"]])
        except UnreachableArc:
            # The platform raises rather than returning its -1 sentinel, so an
            # unreachable facility cannot be compared as though it were near.
            # `MTX-5` and `lastmile.reachable_subset` exist for the same reason.
            continue
        reachable.append((facility["id"], metres))

    if not reachable:
        raise NoRoadPath(
            f"{package['package_id']} has no road path to any of "
            f"{', '.join(f['id'] for f in facilities)}; §3.3 assigns by road "
            "distance and this address reaches none of them")
    return tuple(sorted(reachable, key=lambda pair: (pair[1], pair[0])))


def nearest_facility(package: Mapping[str, Any],
                     facilities: Sequence[Mapping[str, Any]],
                     matrix: TravelMatrix,
                     index: Mapping[str, int]) -> str:
    """§3.3: "nearest facility by road distance"."""
    return ranked(package, facilities, matrix, index)[0][0]
