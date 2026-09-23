# Open Question 5, answered

**Question (§12.5 of the problem definition; answered against v0.10,
still current at v0.16):** *"Solver capabilities to
confirm: dynamic stop insertion; flexible vehicle-to-depot assignment; native
due-date handling; locked assignments; ready-time constraints on stops."*

**Verdict up front: four of five yes, one no.** The no is
vehicle-to-depot assignment, and it settles §4.2 — the **two-stage** approach
the document already recommends is the only one available, so that
recommendation can stop being provisional.

First measured against `vrp-platform` 0.3.2, and **re-run unchanged against
`v0.4.0`** — the version this repository now pins — on 17 September 2026. All
five answers reproduce exactly; the bump touches no library code
(`git diff --stat v0.3.3..v0.4.0 -- vrp/ gateway/` is empty), so this is a
confirmation rather than a new measurement. Both runs go through this
repository's own `ddn.contract` mapping, and every line below is real output
from `docs/solver-capabilities.py`, not a reading of the source.

| # | Capability | Answer |
|---|---|---|
| 1 | Dynamic stop insertion | **Yes** |
| 2 | Flexible vehicle-to-depot assignment | **No** |
| 3 | Native due-date handling | **Yes** |
| 4 | Locked assignments | **Yes** |
| 5 | Ready-time constraints on stops | **Yes**, and independently verified |

---

## 1. Dynamic stop insertion — yes

`vrp.quote.quote_insertion` prices one new stop against a plan already running
and returns the route it would produce.

```
inserting P7 into a live plan costs 900 on M2
resulting route: P7 -> P1 -> P0 -> P6 -> P2 -> P3 -> P5 -> P4
```

It answers "no room" distinctly from "expensive", which is the distinction a
dispatcher needs: refusing an insertion raises `NoRoomForOrder` naming
capacity, a window, skills or access rather than returning a large number.

**What this means for §5.1.** The fallback the document offers — *"if the
solver in use does not support dynamic insertion, pickups can be batched into
fixed waves"* — is not needed. Note this is now only about **pickups**: v0.10's
one-day lag (§5, pickup and line-haul on D, all delivery on D+1) makes last-mile
routing a static overnight batch against a pool known by morning.

## 2. Flexible vehicle-to-depot assignment — no

`Vehicle.start_location_id` is a required `str`. A vehicle's depot is fixed
before the solve; it is not a decision variable. `PIN_DEPOT`, the one lock that
mentions a depot, requires `order_id` and `depot_id` — it pins an *order* to a
facility, not a vehicle to one.

**What this means for §4.2.** The "Integrated" option — *"the solver treats all
packages and all vehicles in one multi-depot problem where a vehicle's home
facility is itself a decision variable"* — cannot be expressed. §4.2 asks to
*"confirm the solver's capabilities before deciding"*; this is that
confirmation, and it decides for two-stage.

That is not a loss. The document's own reasoning for two-stage stands on its
own — §4.2 notes relocation takes time and the rider is unavailable during it,
which is why it suggests weekly or rolling reallocation rather than daily. A
solver that reassigned homes freely each night would be modelling a freedom the
operation does not have.

**The daily allocation step itself is not built.** `vrp.scenarios` is fleet
*sizing* over a scenario set, which §1 puts explicitly out of scope ("total
fleet is fixed; only its distribution varies"); `vrp.allocate` reports
utilisation and marginal value rather than deciding. §7.2's soft constraint on
relocation between consecutive days has no home yet either.

## 3. Native due-date handling — yes, and it does not double-count

Two mechanisms, deliberately separate, which is what lets §6.1's concern be
satisfied rather than traded off.

```
SLA-today -> tier {0}, source SLA
must-serve orders all served: True (6 of them)
hard window honoured: verifier ok = True
```

- **A date that is today** becomes `priority_tier = 0`, which the platform
  treats as must-serve whatever the prize. That is §6.1's *"a constraint, not a
  weight, so it does not double-count"*, expressed as a constraint.
- **A time window** can be `HARD`, and the independent verifier checks arrivals
  against it (`INV-3`).
- **Commercial priority** travels separately as `priority_source`, so the SLA
  clock and the score are never added together.

**One correction to §8.1 that matters more than it did.** The document
recommends encoding classes as widely spaced tiers *within the score* — the
worked example gives Urgent 1,000–1,999, Standard 100–199, Low 1–99, which is
1,098 distinct values. Mapped to `priority_tier` that **now raises**: the
platform compiles lexicographic tiers into prize bonuses that grow
multiplicatively in the number of distinct tiers and exceed int64 between 50 and
55 of them. Before `v0.3.2` it wrapped silently and inverted the priority order.

This repository maps the **band** to the tier (three of them) and the **score**
to `Order.prize`, which is §8.1's own second bullet and is safe by nine orders
of magnitude. §8.1's first bullet — that lexicographic objectives are often
unsupported — is not true of this platform, and acting on it is what breaks.

## 4. Locked assignments — yes

```
P4 pinned to M2 -> solver placed it on M2
verifier (INV-8) accepts: True
```

`Lock(kind="PIN_ORDER_TO_VEHICLE", …)` is honoured by the solver and checked
independently by the verifier, so §8.2's override survives re-optimisation and
is not merely requested. `ddn.contract` maps §9.1's `locked_vehicle_id` to it
directly.

**Half of §8.2 is not a solver feature, and is no longer missing.** "Force a
package in" works as above. "Pull one out" — force a package to be unassigned —
has no lock kind: `FORBID_ORDER_ON_VEHICLE` names one vehicle, so excluding a
package at a 48-bike hub means 48 locks, and the platform's minimal-conflict
diagnosis over 48 synthetic locks is noise. `FORBID_DEPLOY` is vehicle-scoped
and does not express it at all.

So it is not encoded as a lock. v0.14 of the problem definition gives §9.1 an
`excluded_by_ops` field and §9.2 an "excluded by operations" reason, and
`contract.triage` withholds the envelope before the solve — the documented
fallback rather than an invented capability. The override is visible in the
output where an operator can find it, and the solver is never asked a question
it has no way to answer. Nothing needs raising upstream.

## 5. Ready-time constraints on stops — yes, and independently verified

New in v0.10, and the one where the answer improved while this was being
written.

```
released 11:00 -> solver departs at [39600] (39600 = 11:00)
a plan departing two hours early:
  INV-17 -> P1 is released at 39600 but its route departs at 32400
```

`Order.release_time` is honoured by the solver — and as of `v0.3.2` the
independent verifier checks it too (`INV-17`). Until that landed, only the
solver enforced it: a plan from a second engine, a hand-built plan, or one
supplied by an integrator would have been accepted while departing before its
load existed. Sixteen invariants and none covered it.

**This is the mechanism for §7.1's depot rule.** *"Depot-bound envelopes are
only dispatched from a depot after they have physically arrived there"* is a
release time, and under v0.10's one-day lag it is the coupling between
line-haul (§5.3) and last mile (§5.4). It is now enforced on both sides.

`INV-17` binds on the route's **departure**, not on the stop — a release time
is a fact about the depot, so it binds when the vehicle leaves carrying the
goods. A van that left empty-handed and dawdled until the goods existed would
pass a stop-based check.

---

## Two things found while answering, which the document should know

**The shipped model declines work rather than deploying a bike.**
`models/ddn-lastmile.json` prices a vehicle at 50,000 against prizes of at most
1,999, so on a small pool the solver leaves everything unassigned and the
bikes idle. The demonstrations above had to raise the prizes to get a route at
all. This is §8's objective question — *how much is a delivery worth against
the cost of deploying?* — and it is unanswered. It needs the real numbers from
§8.3's van-hours and cost figures, not a guess here.

**Matrix build is not a constraint.** One facility's travel matrix at hub scale
(1,601 stops, 2,563,201 cells, 289 tiles) builds in **29.4 s** against a real
Costa Rica graph; §10's whole night across seven facilities is about **50 s**,
which is 3.3% of the platform's own fifteen-minute budget for a plan that size.
Measured cold; `MTX-10` expects ≥90% pair reuse night to night.
