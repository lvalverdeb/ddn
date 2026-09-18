"""§5.2.5 — when each envelope will be ready.

§5.2.5 gives the formula outright: "the expected ready time for each envelope
can be computed at request time as: van's expected return to hub +
reconciliation time + (assembly queue time if required) + sorting time". This
is that sum, with the word **queue** taken seriously.

**A throughput is not a service time.** Open Question 4 asks for "envelopes per
hour" per step, which describes a server working through a line, not a delay
each envelope suffers privately. Modelling it as a flat per-envelope addition
makes the hub infinitely parallel: 4,800 envelopes arriving at once would all be
ready a few minutes later, and §5.2.3's clean room -- which §8.3 calls the
likely bottleneck after van-hours -- would never be one. Each step is therefore
a single server: an envelope starts when both it and the server are free.

**The rates are invented.** `docs/assumptions.md` says so per row, and the
consequence is worth stating here too: every time this module produces is a
property of those three numbers. A readiness result is not a measurement of the
hub, and a line-haul plan built on one inherits that. It is the same warning
`capacity-finding.md` carries about a figure measured with a stand-in price.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ddn import assumptions

#: §5.2.3: "The `package_type` field identifies which envelopes require it."
#: §9.1 declares `package_type` an enum and lists no values anywhere, so this is
#: the fixture's spelling until operations name the real set.
ASSEMBLY_TYPES = frozenset({"assembly"})


@dataclass(frozen=True, slots=True)
class Readiness:
    """One envelope's path through §5.2, and when it comes out."""

    package_id: str
    ready_at: int
    reconciled_at: int
    sorted_at: int
    assembled_at: int | None = None

    @property
    def assembled(self) -> bool:
        return self.assembled_at is not None


def requires_assembly(envelope: Mapping[str, Any]) -> bool:
    """§5.2.3: only certain package types go through the clean room."""
    return envelope.get("package_type") in ASSEMBLY_TYPES


def _service(per_hour: int) -> float:
    if per_hour <= 0:
        raise ValueError(f"a throughput of {per_hour} per hour processes nothing")
    return 3600 / per_hour


def schedule(
    envelopes: Sequence[Mapping[str, Any]],
    arrival_of: Mapping[str, int], *,
    reconcile_per_hour: int = assumptions.RECONCILE_PER_HOUR,
    assembly_per_hour: int = assumptions.ASSEMBLY_PER_HOUR,
    sort_per_hour: int = assumptions.SORT_PER_HOUR,
) -> tuple[Readiness, ...]:
    """§5.2.5's expected ready time, per envelope.

    Args:
        envelopes: §9.1 records, each carrying `package_id`, `mailbag_id` and
            `package_type`.
        arrival_of: mailbag id -> the second its van is expected back at the
            hub. §5.2.5 computes readiness "at request time", so this is the
            *expected* return, not an observed one.
        reconcile_per_hour: §5.2.1's rate. Defaults to the placeholder.
        assembly_per_hour: §5.2.3's clean-room rate, the one that decides how
            much rolls to tomorrow.
        sort_per_hour: §5.2.4's rate.

    Returns:
        One `Readiness` per envelope, in the order they clear the hub, so the
        caller reads the day as the hub will work it.

    Raises:
        ValueError: if an envelope's bag has no expected arrival. §5.2.5's
            formula starts at the van's return, and an envelope whose bag is
            not coming has no ready time to compute -- guessing one would put a
            phantom on a line-haul.
    """
    missing = sorted({e["mailbag_id"] for e in envelopes
                      if e["mailbag_id"] not in arrival_of})
    if missing:
        raise ValueError(
            f"{len(missing)} mailbag(s) have no expected return to the hub "
            f"({', '.join(missing[:3])}...); §5.2.5 computes readiness from it")

    recon, assemble, sort = (_service(reconcile_per_hour),
                             _service(assembly_per_hour),
                             _service(sort_per_hour))

    # Order of arrival, then package id: a queue serves what is in front of it,
    # and two envelopes off the same van must still break their tie the same
    # way on every run.
    queue = sorted(envelopes,
                   key=lambda e: (arrival_of[e["mailbag_id"]], e["package_id"]))

    recon_free = assembly_free = sort_free = 0.0
    done: list[Readiness] = []
    for envelope in queue:
        entered = float(arrival_of[envelope["mailbag_id"]])

        recon_free = max(entered, recon_free) + recon
        reconciled = recon_free

        assembled: float | None = None
        if requires_assembly(envelope):
            assembly_free = max(reconciled, assembly_free) + assemble
            assembled = assembly_free

        sort_free = max(assembled or reconciled, sort_free) + sort
        done.append(Readiness(
            package_id=envelope["package_id"],
            ready_at=math.ceil(sort_free),
            reconciled_at=math.ceil(reconciled),
            sorted_at=math.ceil(sort_free),
            assembled_at=None if assembled is None else math.ceil(assembled)))

    return tuple(sorted(done, key=lambda r: (r.ready_at, r.package_id)))
