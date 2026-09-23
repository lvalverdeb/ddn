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

**Distances are road distances**, from a matrix the caller supplies — §3.3
assigns "by road distance" and a straddle margin measured any other way is a
margin in the wrong units. §5.2.4 sorts at upload-file receipt, so the caller is
whoever holds a gateway at that point.

**Nothing in the day pipeline calls `presort`, and that is a data gap rather
than an oversight.** §3.3 ranks by *road* distance, so sorting a pool needs a
matrix spanning every envelope address and every facility. §3.1 supplies no
real coordinates, and `tests/matrices.py` replays a recording of 94 points --
the seven facilities and the pickup sites -- so the suite cannot build one over
4,550 addresses and `road_matrix` refuses rather than guessing.

Where the rule *does* run is `ddn/simulation/on_road.py:155`, which builds a
real matrix against the gateway and calls `model.facility.nearest_facility`.
Everything else -- `simulation.day`, the slices, the API -- reads the
`facility_id` already stamped on the §9.1 record, which is what §5.2.4 says the
hub does at file receipt.

So `presort` is the rule plus §3.2's straddle flag, exercised by its own tests
and by nothing else, and `keep_straddlers_at_hub` is e2e-2's decision on that
flag. Wiring either into the pipeline needs envelope-level road travel that
this repository does not have. `tests/test_processing.py` pins the reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from vrp.model import TravelMatrix

from ddn import assumptions
from ddn.model.facility import ranked

#: §9.1's value for a coordinate that is still a zip-code centroid.
ZIP_CENTROID = "zip_centroid"

#: e2e-2 §5's roll reason for a straddler. The envelope is not rolled in the
#: usual sense -- it is at the hub and will be dispatched from there -- so the
#: word is the document's and the placement is `held`, not `rolled`. See
#: `keep_straddlers_at_hub`.
HELD_STRADDLE = "held-straddle"


@dataclass(frozen=True, slots=True)
class Sorted:
    """One envelope's facility, and how close the call was."""

    package_id: str
    facility_id: str
    runner_up: str | None
    #: Road metres between the nearest facility and the next nearest.
    margin_m: float
    #: §3.2's "[flag these]" -- not a decision, a flag. Open Question 14.
    straddles: bool = False


def presort(
    envelopes: Sequence[Mapping[str, Any]],
    facilities: Sequence[Mapping[str, Any]],
    matrix: TravelMatrix,
    index: Mapping[str, int], *,
    margin_m: float = assumptions.EQUIDISTANT_MARGIN_M,
) -> tuple[Sorted, ...]:
    """§3.3's nearest-facility rule over a pool, with §3.2's straddle flag.

    Args:
        envelopes: §9.1 records carrying `package_id` and `coord_source`.
        facilities: §3.1 rows carrying `id`.
        matrix: road travel spanning every envelope and every facility.
        index: id -> matrix row, for each of them.
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
        order = ranked(envelope, facilities, matrix, index)
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


def keep_straddlers_at_hub(
        sorted_envelopes: Sequence[Sorted], *,
        hub_id: str = "HUB") -> tuple[tuple[Sorted, ...], tuple[str, ...]]:
    """e2e-2 §2 item 1: a straddler waits at the hub for a real address.

    "A zip-centroid envelope whose centroid is within `EQUIDISTANT_MARGIN_M`
    of two facilities is flagged; the working rule is to **keep it at the hub**
    pending address geocoding rather than commit it to a depot it may have to
    leave again."

    `presort` flags; this decides, and the two are separate on purpose.
    §3.2 says only "[flag these]" and Open Question 14 is still open, so the
    flag is what the parent document supports and the keep-at-hub rule is
    e2e-2's, marked there for promotion. A caller that wants §3.3's answer
    unmodified simply does not call this.

    Returns:
        The same records with each straddler reassigned to the hub, in the
        input's order, and the ids that moved. The ids are returned rather
        than inferred from a comparison, because an envelope whose nearest
        facility was *already* the hub straddles without moving and is held
        just the same.
    """
    held = tuple(s.package_id for s in sorted_envelopes if s.straddles)
    kept = tuple(replace(s, facility_id=hub_id) if s.straddles else s
                 for s in sorted_envelopes)
    return kept, held
