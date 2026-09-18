"""§5.2.4 — sorting by target facility, decided at upload-file receipt.

§3.2's finding is that this can happen early and coarsely: "sorting by target
facility can use zip centroids directly; the nearest-facility decision is coarse
enough that zip precision is acceptable". §5.1.1 is why it matters -- the upload
file usually precedes the bag, so envelopes are "geocoded and pre-sorted to a
target facility" before anything is collected, and the clean room and line-haul
both know their day in advance.

**Except where the centroid straddles.** §3.2 carries a bracketed instruction --
"except for zips straddling two facilities' areas [flag these]" -- and Open
Question 14 asks what to do about them: "how to treat zips whose centroid is
near-equidistant from two facilities -- hold for address geocoding before
sorting?" This module does the flagging and not the deciding. Holding an
envelope is an operational choice nobody has made, and a module that quietly
held them would be answering an open question by default.

The flag applies only to envelopes still carrying a zip centroid. An address
that has been geocoded is precise enough that near-equidistance is a real fact
about the geography rather than an artefact of the centroid, and §3.2's concern
is the artefact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ddn import assumptions
from ddn.model.facility import ranked

#: §9.1's value for a coordinate that is still a zip-code centroid.
ZIP_CENTROID = "zip_centroid"


@dataclass(frozen=True, slots=True)
class Sorted:
    """One envelope's facility, and how close the call was."""

    package_id: str
    facility_id: str
    runner_up: str | None
    margin_m: float
    #: §3.2's "[flag these]" -- not a decision, a flag. Open Question 14.
    straddles: bool = False


def presort(
    envelopes: Sequence[Mapping[str, Any]],
    facilities: Sequence[Mapping[str, Any]], *,
    margin_m: float = assumptions.EQUIDISTANT_MARGIN_M,
) -> tuple[Sorted, ...]:
    """§3.3's nearest-facility rule over a pool, with §3.2's straddle flag.

    Args:
        envelopes: §9.1 records carrying `package_id`, `lat`, `lon` and
            `coord_source`.
        facilities: §3.1 rows carrying `id`, `lat`, `lon`.
        margin_m: how close the second-nearest facility must be for a
            zip-centroid envelope to be flagged. Defaults to the placeholder in
            `docs/assumptions.md`; §3.2 gives no figure.

    Returns:
        One `Sorted` per envelope, in the input's order, so a caller can zip it
        back against its own pool.

    Raises:
        ValueError: if there are no facilities to sort into.
    """
    if not facilities:
        raise ValueError("§3.3 assigns each envelope to one of HUB or D1-D6; "
                         "none were given")

    results: list[Sorted] = []
    for envelope in envelopes:
        order = ranked(envelope, facilities)
        nearest, distance = order[0]
        runner_up, second = order[1] if len(order) > 1 else (None, None)
        margin = float("inf") if second is None else second - distance
        results.append(Sorted(
            package_id=envelope["package_id"],
            facility_id=nearest,
            runner_up=runner_up,
            margin_m=margin,
            straddles=(envelope.get("coord_source") == ZIP_CENTROID
                       and margin < margin_m)))
    return tuple(results)
