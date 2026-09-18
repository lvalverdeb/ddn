"""One page of a day, for a person.

§11's rows and §8.3's checks in the order somebody reading a morning report
would want them: what happened, what it cost, and what is about to run out.
Every provisional figure is marked, because the alternative is a page of
numbers that all look equally solid and are not.
"""

from __future__ import annotations

from ddn.simulation.day import DayReport
from ddn.simulation.metrics import EXPECTED_PER_BIKE

WIDTH = 66


def _pct(value: float | None) -> str:
    return "     —" if value is None else f"{value * 100:5.1f}%"


def _num(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{value:,.{places}f}"


def _hours(metrics) -> float | None:
    """§11 asks for request-to-collection; seconds read badly on a page."""
    wait = metrics.pickup_responsiveness_s
    return None if wait is None else wait / 3600


def render(report: DayReport) -> str:
    """A day as one page of text."""
    tally, metrics, checks = report.tally, report.metrics, report.checks
    out: list[str] = [
        "=" * WIDTH,
        f"DDN — day report — {report.day.isoformat()}".center(WIDTH),
        "=" * WIDTH,
        "",
        "DELIVERY (§5.4, on the pool positioned yesterday)",
        f"  ready pool                {tally.ready_pool:>8,}",
        f"  dispatched                {tally.dispatched:>8,}",
        f"  delivered                 {tally.delivered:>8,}",
        f"  postponed                 {tally.postponed:>8,}",
        f"  rejected / defective      {tally.rejected:>8,} / {tally.defective:,}",
        f"  unassigned                {tally.unassigned:>8,}",
    ]
    if report.postponed_reasons:
        out.append("  postponed because (§6):")
        for reason, count in sorted(report.postponed_reasons.items()):
            out.append(f"    {reason:<24}{count:>8,}")

    out += [
        "",
        "COLLECTION (§5.1–§5.3, positioned for tomorrow)",
        f"  received                  {tally.received:>8,}",
        f"  ready by cut-off          {sum(report.positioned.values()):>8,}",
    ]
    for facility, count in sorted(report.positioned.items()):
        if count:
            out.append(f"    {facility:<24}{count:>8,}")
    for facility, reason in sorted(report.rolled.items()):
        out.append(f"  rolled at {facility}: {reason}")

    out += [
        "",
        f"  return run                {report.return_stops:>8,} stops",
        f"  fleet moved (§7.2)        {report.allocation.moves:>8,} vehicles",
        f"  carried into tomorrow     {report.carried_into_tomorrow:>8,}",
        "",
        "§11 METRICS (no targets: §11 leaves every one [TBD])",
        f"  first-attempt delivery    {_pct(metrics.first_attempt_delivery_rate)}",
        f"  postponement rate         {_pct(metrics.postponement_rate)}",
        f"  unassigned rate           {_pct(metrics.unassigned_rate)}",
        f"  SLA compliance            {_pct(metrics.sla_compliance)}",
        f"  SLA expiry rate           {_pct(metrics.sla_expiry_rate)}",
        f"  same-day readiness        {_pct(metrics.same_day_readiness)}",
        f"  discrepancy rate          {_pct(metrics.reconciliation_discrepancy_rate)}",
        f"  pickup responsiveness     {_num(_hours(metrics), 2)} h",
        f"  distance per envelope     {_num(metrics.distance_per_envelope_m)} m",
        (f"  envelopes per bike        {_num(metrics.envelopes_per_bike, 1)}"
         f"   (§11 expects {EXPECTED_PER_BIKE[0]}–{EXPECTED_PER_BIKE[1]})"),
        f"  solver run time           {_num(metrics.solver_seconds, 2)} s",
        "",
        "§8.3 CAPACITY CHECKS",
    ]
    for one in checks.all:
        verdict = "BINDS" if one.binds else "ok"
        out.append(
            f"  {one.name:<14}{one.required:>10,.0f} wanted /"
            f"{one.available:>10,.0f} {one.unit:<10}{verdict}")
    binding = checks.binding
    out += [
        "",
        (f"  first to bind: {binding.name} — short by "
         f"{-binding.headroom:,.0f} {binding.unit}" if binding
         else f"  nothing binds; tightest is {checks.tightest.name} at "
              f"{checks.tightest.load * 100:.0f}% of capacity"),
        "",
        "-" * WIDTH,
        "Every figure above is provisional. Outcome rates are read off §10's",
        "worked example and the processing rates are invented, so a delivered",
        "total restates the document rather than measuring the operation.",
        "See docs/assumptions.md and docs/capacity-finding.md.",
        "=" * WIDTH,
    ]
    return "\n".join(out)
