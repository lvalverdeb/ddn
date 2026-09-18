"""§8.3's three structural checks, and which one binds first.

§8.3 asks for them **before** the solver is expected to meet SLA targets:
delivery capacity against the daily range, van-hours against the day's pickup
and line-haul demand, and hub throughput against the same inflow. Its own
verdict is that vans are "the most likely operational bottleneck after
clean-room assembly", and `docs/capacity-finding.md` has since answered the van
check from three stages: it binds, and harder than the document assumes.

Nothing here measures anything. Each check compares two numbers the caller
supplies, and two of the three depend on placeholders -- the processing rates
are invented outright and the bike rate is §4.2's own approximation -- so a
verdict about which binds is a verdict about those inputs. What the check does
add is the comparison §8.3 asks for, in one place, with the tightest one named.
"""

from __future__ import annotations

from dataclasses import dataclass

from ddn import assumptions


@dataclass(frozen=True, slots=True)
class Check:
    """One of §8.3's comparisons: what there is against what is wanted."""

    name: str
    available: float
    required: float
    unit: str
    #: Whether the figures behind it are placeholders rather than measurements.
    provisional: bool = True

    @property
    def headroom(self) -> float:
        return self.available - self.required

    @property
    def binds(self) -> bool:
        return self.required > self.available

    @property
    def load(self) -> float:
        """Demand as a share of capacity. Above 1.0 is a gap."""
        return float("inf") if not self.available else self.required / self.available


@dataclass(frozen=True)
class Checks:
    """§8.3's three, together, because the tightest is the only one that matters."""

    delivery: Check
    vans: Check
    processing: Check

    @property
    def all(self) -> tuple[Check, ...]:
        return (self.delivery, self.vans, self.processing)

    @property
    def binding(self) -> Check | None:
        """The tightest check that actually binds, or `None` if none does."""
        gaps = [check for check in self.all if check.binds]
        return max(gaps, key=lambda check: check.load) if gaps else None

    @property
    def tightest(self) -> Check:
        return max(self.all, key=lambda check: check.load)


def hub_throughput(hours: float, *, assembly_share: float,
                   reconcile_per_hour: int = assumptions.RECONCILE_PER_HOUR,
                   assembly_per_hour: int = assumptions.ASSEMBLY_PER_HOUR,
                   sort_per_hour: int = assumptions.SORT_PER_HOUR) -> float:
    """Envelopes the hub can make ready in `hours`, at its slowest step.

    §5.2's steps run in series, so the hub's throughput is its worst stage --
    and the clean room only sees `assembly_share` of the inflow, which is why
    a rate of 120 an hour does not cap the day at 120 an hour. §8.3 expects
    this to be the check that fails first after vans.
    """
    limits = [reconcile_per_hour * hours, sort_per_hour * hours]
    if assembly_share > 0:
        limits.append(assembly_per_hour * hours / assembly_share)
    return min(limits)


def check(*, pool: int, motorbikes: int, inflow: int,
          van_hours_available: float, pickup_hours: float,
          linehaul_hours: float, processing_hours: float,
          assembly_share: float,
          per_bike: int = assumptions.ENVELOPES_PER_BIKE) -> Checks:
    """§8.3's three checks over one day's figures.

    Args:
        pool: envelopes ready to deliver this morning.
        motorbikes: bikes deployed today.
        inflow: envelopes arriving at the hub today.
        van_hours_available: van-hours the fleet offers.
        pickup_hours: hours the day's pickup routes take.
        linehaul_hours: hours the night's line-haul round trips take, transit
            to distant depots included -- §8.3 says to count the return leg,
            because §5.3 has the van and driver unavailable until they are back.
        processing_hours: hours the hub works.
        assembly_share: the fraction of inflow needing the clean room.
        per_bike: §4.2's ~25 envelopes per motorbike per day.
    """
    return Checks(
        delivery=Check("delivery", motorbikes * per_bike, pool, "envelopes"),
        vans=Check("van-hours", van_hours_available,
                   pickup_hours + linehaul_hours, "hours"),
        processing=Check(
            "processing",
            hub_throughput(processing_hours, assembly_share=assembly_share),
            inflow, "envelopes"))
