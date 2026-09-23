"""§5.2.5 — when each envelope will be ready.

§5.2.5 gives the formula outright: "the expected ready time for each envelope
can be computed at request time as: van's expected return to hub +
reconciliation time + (assembly queue time if required) + sorting time". This
is that sum, with the word **queue** taken seriously.

§5.2.5 states that sum for one case of two. Its other sentence -- "where the
file is late or missing (§5.1.1), geocoding cannot have run ahead of the bag and
joins the sequence after reconciliation" -- is the exception this module also
runs, as a fourth server between reconciliation and the clean room.

**A throughput is not a service time.** Open Question 4 asks for "envelopes per
hour" per step, which describes a server working through a line, not a delay
each envelope suffers privately. Modelling it as a flat per-envelope addition
makes the hub infinitely parallel: 4,800 envelopes arriving at once would all be
ready a few minutes later, and §5.2.3's clean room -- which §8.3 calls the
likely bottleneck after van-hours -- would never be one. Each step is therefore
a single server: an envelope starts when both it and the server are free.

**The rates are invented.** `docs/assumptions.md` says so per row, and the
consequence is worth stating here too: every time this module produces is a
property of those four numbers. A readiness result is not a measurement of the
hub, and a line-haul plan built on one inherits that. It is the same warning
`capacity-finding.md` carries about a figure measured with a stand-in price.
"""

from __future__ import annotations

import math
from collections.abc import Container, Mapping, Sequence
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
    geocoded_at: int | None = None

    @property
    def assembled(self) -> bool:
        return self.assembled_at is not None

    @property
    def late_ready(self) -> bool:
        """§5.1.1's flag: "its envelopes are flagged as late-ready".

        Not stored separately, because having passed the geocoding stage *is*
        the fact the flag names -- §5.2.2 puts that stage on the critical path
        in exactly the case §5.1.1 describes and in no other.
        """
        return self.geocoded_at is not None


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
    late_files: Container[str] = frozenset(),
    reconcile_per_hour: int = assumptions.RECONCILE_PER_HOUR,
    geocode_per_hour: int = assumptions.GEOCODE_PER_HOUR,
    assembly_per_hour: int = assumptions.ASSEMBLY_PER_HOUR,
    sort_per_hour: int = assumptions.SORT_PER_HOUR,
) -> tuple[Readiness, ...]:
    """§5.2.5's expected ready time, per envelope.

    Args:
        envelopes: §9.1 records, each carrying `package_id`, `mailbag_id` and
            `package_type`. §5.2.3 orders the clean room by `priority` and
            `sla_date` where they are given; an envelope carrying neither
            queues behind the ones that do.
        arrival_of: mailbag id -> the second its van is expected back at the
            hub. §5.2.5 computes readiness "at request time", so this is the
            *expected* return, not an observed one.
        late_files: mailbag ids whose upload file was late or never came
            (§5.1.1). Their envelopes take §5.1.1's slower order -- open, key
            in, then geocode -- and come out flagged late-ready. A bag not
            named here takes the path it takes today: §5.2.2 has already
            geocoded it, before it arrived.
        reconcile_per_hour: §5.2.1's rate. Defaults to the placeholder.
        geocode_per_hour: §5.2.2's rate, which costs readiness nothing except
            for the bags in `late_files`.
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

    recon, geocode, assemble, sort = (_service(reconcile_per_hour),
                                      _service(geocode_per_hour),
                                      _service(assembly_per_hour),
                                      _service(sort_per_hour))

    # §5.2.1 takes bags in the order they arrive: a queue serves what is in
    # front of it, and two envelopes off the same van must still break their
    # tie the same way on every run.
    queue = sorted(envelopes,
                   key=lambda e: (arrival_of[e["mailbag_id"]], e["package_id"]))

    recon_free = 0.0
    reconciled_at: dict[str, float] = {}
    for envelope in queue:
        entered = float(arrival_of[envelope["mailbag_id"]])
        recon_free = max(entered, recon_free) + recon
        reconciled_at[envelope["package_id"]] = recon_free

    # §5.1.1's slower order, for the bags whose file did not beat them here:
    # "open, key in, then geocode". §5.2.2 runs this before arrival in the
    # usual case, where it costs readiness nothing and so has no stage at all;
    # this is the case where it is on the critical path. A separate pass and a
    # separate server, for the same reason reconciliation is: the desk that
    # keys in a late bag works through a line like every other step in §5.2.
    geocode_free = 0.0
    geocoded_at: dict[str, float] = {}
    for envelope in sorted((e for e in queue
                            if e["mailbag_id"] in late_files),
                           key=lambda e: (reconciled_at[e["package_id"]],
                                          e["package_id"])):
        cleared = reconciled_at[envelope["package_id"]]
        geocode_free = max(cleared, geocode_free) + geocode
        geocoded_at[envelope["package_id"]] = geocode_free

    # What each envelope carries into the clean room: geocoding for the late
    # bags, reconciliation for every other. §5.2.5 puts geocoding "after
    # reconciliation" and the assembly queue after that.
    entered_assembly = {e["package_id"]:
                        geocoded_at.get(e["package_id"],
                                        reconciled_at[e["package_id"]])
                        for e in queue}

    # §5.2.3: the clean room schedules its work and, "where the queue exceeds
    # capacity, prioritised by envelope priority and SLA date". This is a
    # separate pass because it is a separate queue -- reconciliation cannot be
    # ordered by priority, since opening the bag is what makes the envelope's
    # priority known at all. §5.2.3 also names assembly the likely bottleneck
    # on a high-volume day, which makes this order the one that decides what
    # misses the cut-off: §10 says the envelopes that roll are the
    # lowest-priority ones, and before this they were the latest-arriving ones.
    assembly_free = 0.0
    assembled_at: dict[str, float] = {}
    for envelope in sorted((e for e in queue if requires_assembly(e)),
                           key=lambda e: _assembly_rank(e, entered_assembly)):
        cleared = entered_assembly[envelope["package_id"]]
        assembly_free = max(cleared, assembly_free) + assemble
        assembled_at[envelope["package_id"]] = assembly_free

    # §5.2.4 sorts whatever is in front of it, which is whatever cleared the
    # step before -- assembly for the envelopes that needed it, reconciliation
    # for the rest.
    sort_free = 0.0
    done: list[Readiness] = []
    for envelope in sorted(queue,
                           key=lambda e: _cleared(e, entered_assembly,
                                                  assembled_at)):
        package_id = envelope["package_id"]
        reconciled = reconciled_at[package_id]
        geocoded = geocoded_at.get(package_id)
        assembled = assembled_at.get(package_id)

        sort_free = max(entered_assembly[package_id] if assembled is None
                        else assembled, sort_free) + sort
        done.append(Readiness(
            package_id=package_id,
            ready_at=math.ceil(sort_free),
            reconciled_at=math.ceil(reconciled),
            sorted_at=math.ceil(sort_free),
            assembled_at=None if assembled is None else math.ceil(assembled),
            geocoded_at=None if geocoded is None else math.ceil(geocoded)))

    return tuple(sorted(done, key=lambda r: (r.ready_at, r.package_id)))


def _assembly_rank(envelope: Mapping[str, Any],
                   entered: Mapping[str, float]
                   ) -> tuple[float, str, float, str]:
    """§5.2.3's queue order: priority, then the SLA clock, then arrival.

    Priority descends because §8.1 makes the larger score the more urgent one.
    An envelope that states neither sorts last rather than first: saying
    nothing should not displace an envelope that said something. Arrival and
    package id break the remaining ties, so the queue replays.

    `entered` is when the envelope reached this queue, which for a late-file
    bag is geocoding rather than reconciliation. It is only the tie-break:
    §5.2.3 gives the clean room no discipline beyond priority and SLA date, so
    where a late arrival lands among equals is the spec's silence, not a rule
    invented here -- v0.17's proposal §8 is where that question sits.
    """
    return (-float(envelope.get("priority") or 0),
            str(envelope.get("sla_date") or "9999-12-31"),
            entered[envelope["package_id"]],
            envelope["package_id"])


def _cleared(envelope: Mapping[str, Any],
             entered: Mapping[str, float],
             assembled_at: Mapping[str, float]) -> tuple[float, str]:
    """When this envelope reached the sorter, and its id to break a tie."""
    package_id = envelope["package_id"]
    return (assembled_at.get(package_id, entered[package_id]), package_id)


def ready_times(dispatch: Any,
                inflow: Sequence[dict[str, Any]],
                requests: Sequence[Mapping[str, Any]] = ()) -> dict[str, int]:
    """§5.2.5, over the envelopes whose bags actually reached the hub.

    `dispatch` is a §5.1 `pickups.Dispatch`, taken duck-typed rather than
    imported: §5.2 is downstream of §5.1 in the day, but making this module
    depend on that one would put the two stages in one knot for the sake of
    three attribute reads.

    `requests` are §9.1's mailbag records, and they are here for one field:
    `file_received_at`. This is the only place that holds both it and the bag's
    arrival, which is what §5.1.1's comparison needs.
    """
    collected = set(dispatch.collected)
    arrival_of = {bag: dispatch.returned_at.get(van, 0)
                  for van, route in dispatch.routes.items() for bag in route}
    return {r.package_id: r.ready_at for r in schedule(
        [e for e in inflow if e["mailbag_id"] in collected], arrival_of,
        late_files=late_file_bags(requests, arrival_of))}


def late_file_bags(requests: Sequence[Mapping[str, Any]],
                   arrival_of: Mapping[str, int]) -> frozenset[str]:
    """§5.1.1's "late **or missing**", read off §9.1's `file_received_at`.

    Both halves of the sentence, kept apart because they are different facts:

    - **missing** -- the field is present and null. §9.1 says so outright:
      "Null where it never did". There is no time to compare, and the bag is
      late by having no file at all.
    - **late** -- there is a time, and it is after the bag reached the hub.
      §5.2.2's "before arrival" is what makes geocoding free, so arrival is the
      line it has to beat.

    A record that does not carry the field at all is **not** late. §9.1 makes
    `file_received_at` optional, and an absent optional field says nothing was
    recorded -- which is not the same claim as a recorded null. Reading silence
    as "no file came" would put every bag predating this field on §5.1.1's
    slower order, which is a different day than the one that happened.
    """
    return frozenset(
        bag for r in requests
        if (bag := r["mailbag_id"]) in arrival_of and "file_received_at" in r
        and ((when := r["file_received_at"]) is None
             or int(when) > arrival_of[bag]))


def at_facilities(
        facilities: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """An empty pool per facility, **in the order the facilities were given**.

    Two things depend on this and neither is obvious. A facility whose pool
    stays empty still has to appear, or the day loses it silently downstream.
    And the *order* is load-bearing: §5.4 selects under capacity in pool order,
    so a mapping built by first-appearance instead of by the facility list
    delivers a different set of envelopes with every aggregate count identical.

    It is a function rather than a line in two callers because those two
    callers are `simulation.day` and `ddn/e2e/`, and the whole point of the
    second is to check it agrees with the first.
    """
    return {f["id"]: [] for f in facilities}


def position(positioned: dict[str, list[dict[str, Any]]],
             ready_at: Mapping[str, int],
             requests: Sequence[dict[str, Any]],
             inflow: Sequence[dict[str, Any]]) -> None:
    """Put each ready envelope at its facility, carrying the customer's site.

    §5.5's stop is the sender, not the recipient's address, and the hub knows
    it from the upload file that brought the bag -- so it is attached now,
    because by the time an envelope is rejected the request it arrived in is
    long gone. §9.1's envelope table has no room for it, which is why this is
    stamped onto the record rather than looked up later.
    """
    site_of = {r["mailbag_id"]: (r["lat"], r["lon"]) for r in requests}
    by_id = {e["package_id"]: e for e in inflow}
    for package_id, when in ready_at.items():
        envelope = by_id[package_id]
        site = site_of.get(envelope["mailbag_id"])
        positioned.setdefault(envelope["facility_id"], []).append(
            dict(envelope, expected_ready_at=when,
                 customer_lat=site[0] if site else envelope["lat"],
                 customer_lon=site[1] if site else envelope["lon"]))
