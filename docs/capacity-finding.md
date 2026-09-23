# §8.3's capacity checks: one answered, one not

**Status:** measured 17 September 2026 against problem definition v0.10.
**Current spec: v0.16**, and §10 has moved since — v0.13 restated the end of
day (2,750 delivered of 2,950 dispatched, 150 unassigned, tomorrow's pool
4,820). The van check below rests on §10's shape, so re-read its figures as
measured against v0.10's and not against today's.
**Audience:** the author of the problem definition.

---

## Verdict up front

§8.3 asks for three capacity checks. We can now answer one of them.

| check | status |
|---|---|
| **Vans** | **Answered. It binds, and harder than the document assumes.** |
| Delivery (motorbikes) | **Cannot be answered yet** — needs §8's cost ratio and §3.1's geography |
| Processing (hub throughput) | Out of scope for the solver; not attempted |

§8.3 already calls van-hours "the most likely operational bottleneck after
clean-room assembly". Three independent measurements agree, and each was taken
from a different stage. None of them needed the cost ratio that blocks the
delivery check, because van capacity is decided by clocks and transit times
rather than by what a delivery is worth.

---

## 1. The van check — answered, and it binds

### 1.1 Line-haul ties up every van overnight

§5.3, run at §10's shape: 2,950 depot-bound envelopes, seven vans available
through the afternoon, six depots at 45–240 minutes' transit, all releasing
routes at 06:00.

Every envelope is carried, by six of the seven vans, each arriving 05:40 for a
06:00 release. That part works. The cost appears afterwards:

| van | depot | departs | back at hub |
|---|---|---|---|
| VAN-6 | D1 | 04:55 | **06:45** |
| VAN-5 | D2 | 04:30 | **07:10** |
| VAN-4 | D3 | 03:50 | **07:50** |
| VAN-3 | D4 | 03:10 | **08:30** |
| VAN-2 | D5 | 02:20 | **09:20** |
| VAN-1 | D6 | 01:40 | **10:00** |

**All six miss the start of the next pickup shift.** That is a direct
consequence of a rule §5.3 states and recommends: a van "may wait for more
envelopes to become ready if it can still reach the depot before its morning
release". Waiting maximises what is carried and returns the van late. §5.3
permits the first without weighing it against the second.

### 1.2 Earmarking pickups costs exactly what §5.1.3 says it costs

§5.1.3: earmarking "no longer costs motorbike delivery capacity; its cost is
**line-haul availability**". Measured at §10's shape — 180 bag requests, six
vans holding twenty bags each:

| vans | released to line-haul | bags collected of 180 |
|---|---|---|
| 6 | none | 120 |
| 6 | three, from 14:00 | **60** |
| 3 | all three | **0** |

A van released at 14:00 cannot be collecting at 16:30, so every bag it would
have taken becomes a re-request tomorrow (§5.1.7).

### 1.3 The two stages squeeze each other

Read together, §1.1 and §1.2 are the same constraint seen from opposite ends:

- Line-haul departing **late** maximises envelopes and returns vans **late**,
  so fewer vans are available for the morning's pickups.
- Pickups running **late** keep vans out, so line-haul cannot release them in
  time to reach distant depots before the morning release.

The taper §5.1.3 asks for is the lever between them, and it has no numbers yet
(Open Question 7). This is where they should come from.

### 1.4 The return run needs vans too, and a longer shift will not do

§5.5 at §10's shape — 80 envelopes back to 30 customer sites in the GAM:

| vans | late shift | outcome |
|---|---|---|
| 2 | 4 h | infeasible |
| 2 | **6 h** | **still infeasible** |
| 4 | 4 h | feasible, all 30 sites |

**This answers half of Open Question 13.** It asks "extended shift or separate
crew?" — for the return run an extended shift is *not* a substitute for
vehicles. §10's two vans do not do it at any shift length tried.

### 1.5 What the van check needs to become a plan

Four figures, all already open questions:

- **Open Question 9** — total van fleet.
- **Open Question 7** — vans earmarked for pickups per weekday, and the rule
  for releasing them through the afternoon. §1.3 is where this bites.
- **Open Question 1** — mailbag capacity per van. We used twenty; the
  collected figures in §1.2 scale directly with it.
- **Open Question 13** — whether the return run is van-only, and whether it is
  an extended shift or a separate crew. §1.4 answers the shift half.

---

## 2. The delivery check — not yet answerable

The run produces **12.8 envelopes per deployed bike** against §7.4's expected
20–30. **That is not a capacity measurement and should not be used as one.**

**Nothing binds.** Routes end with a median **110 minutes of an eight-hour
shift unused**; neither the 35-envelope capacity nor the route-duration limit
is close. §7.4 predicts that shift duration binds — in this run it does not.
Independently checked: at §7.4's own 25 stops the arithmetic leaves 81 minutes
spare, so the clock was never the limit.

The solver stops because of prices, not time. A standard envelope carries a
priority score of 150 and routing charges 1 cost unit per metre, which makes a
standard envelope worth 15,000 against 2,000 per kilometre travelled —
**break-even at 7.5 km one way**. At the hub, whose pool is 98% GAM, the median
*declined* envelope is 16.6 km out, so a round trip to it costs 33,222 against
its 15,000. Declining is arithmetically correct given the prices it was handed.
§8 ranks delivered work first and distance third; those numbers invert it.

**Where those prices came from, established 19 September 2026.** The 1 cost unit
per metre above is right, but it was never DDN's setting: `ddn` has never put a
cost on a vehicle, and `vrp/solve/pyvrp_adapter.py:600` omits the key when the
cost is zero, so PyVRP applied its own `unit_distance_cost` default of 1. The
arithmetic in this section is unaffected. What did *not* apply is
`vehicle_fixed_cost` — `models/ddn-lastmile.json` declares 50,000 and the solver
never saw it, so deploying a bike was free in this run. **Wiring it will move the
12.8**, and the figure should be re-measured rather than carried across.

**Two inputs are missing, neither a solver problem.**

1. **§8's ratio** — what a delivered envelope is worth against a kilometre
   ridden. §1 of the document is load-bearing here: the fleet is fixed, so a
   bike already allocated and already riding has a *marginal* cost, not a
   commercial rate per kilometre.
2. **§3.1's coordinates and the real customer geography.** Assignment follows
   §3.3's nearest-facility rule over a corpus that spreads 50,000 deliveries
   across the whole country: D2 draws envelopes a **median 49 km** away, where
   no motorbike round exists at all. Depot placement changes this answer more
   than any solver setting does.

---

## 3. What the runs do establish

Independent of both unknowns:

- **The operation is expressible.** All five stages of §5's daily flow are
  built, and every routed plan passes a verifier that shares no code with the
  solver.
- **The stages are not uniform**, which matters for whoever integrates them.
  §5.4 and §5.5 are static CVRPs and use the solver. §5.3 is an assignment
  problem and uses none — treating it as routing would invent a route where
  there is a single leg. §5.1 is a dynamic VRP *as a problem type*, but the
  half built so far is admission — which van may take a bag — and that half
  needs no solver either; insertion and the re-optimisation cadence do.

  Stated as code rather than as a plan: of the four operational modules that
  exist today, **two import no part of the routing library at all**.
- **Travel matrices are not a constraint.** A hub-scale matrix — 1,601 stops,
  2.5M cells, 289 tiles — builds in **29.4 s**; a whole night across seven
  facilities is about **50 s**.
- **The one-day lag simplifies delivery.** Because §5 puts all delivery on D+1,
  last mile is a static overnight batch against a pool known by morning. §5.1's
  fallback — batching into waves if dynamic insertion is unsupported — is not
  needed; insertion works.

---

## 4. Three gaps in the data contract

**§9.2 has no reason code for an address with no road path.** The published
reasons are *time, count, cut-off missed, low geocode confidence, SLA expired*.
Real road data contains addresses that geocode perfectly and have no route from
their assigned facility — a one-way system, a severed fragment, an island. The
remedies differ: a low-confidence address needs re-geocoding, an unroutable one
needs a different facility or a note that this network cannot serve it.
Suggested addition: **`no road path`**.

**§7.4 has no service time for a return stop.** Its ten minutes is per envelope
*delivered* — "recipient opens, reviews, asks questions, signs" — and it treats
multiple envelopes to one recipient as "rare and not modelled separately". A
return stop aggregates them by construction: §9.2's return record carries a
*list* of package_ids. One handover of a bundle is not N signings, so the
delivery figure does not transfer. We borrowed it per stop and flagged it.
A fixed-plus-per-envelope figure would be expressible as-is.

**§7.1's shift bullet has no shift to check line-haul against.** §7.1 requires
that "a vehicle's route starts and ends at its home facility within its shift
(service time + travel time ≤ shift)", and §9.1 gives every vehicle a
`shift_start / end` to check it against. No line-haul trip is inside one.
Planning a night for all six depots on recorded road travel:

| depot | departs | arrives |
|---|---|---|
| D6 | D+1 02:30 | D+1 06:30 |
| D5 | D+1 03:15 | D+1 06:30 |
| D4 | D+1 05:00 | D+1 06:30 |
| D2 | D+1 05:45 | D+1 06:30 |
| D3 | D+1 05:50 | D+1 06:30 |
| D1 | D+1 06:00 | D+1 06:30 |

**The shift these are outside of is a placeholder, and the finding does not
rest on its value.** The fixture's vans run `SHIFT_START` to
`PROCESSING_CUTOFF`, 07:00 to 18:00 — the first *derived* from §7.4's eight
hours, the second marked **invented** in `assumptions.md`. Neither is §9.1's
and neither is §10's; the document gives no van shift window. What the table
shows is structural rather than numeric: every departure falls between 02:30
and 06:00, which is outside any daytime window, whatever its end.

These are not §1.1's departures and should not be read against them. §1.1 runs
§10's shape — 06:00 releases, one depot per van, and the clock carried on until
the van is back at the hub. This table is the peak-day fixture: 07:00 releases,
arrival at the last depot, no return leg. The two agree on the only thing this
section needs, which is that the whole of line-haul happens at night.

That is not a planning error. §5 puts line-haul on D and delivery on D+1, and a
van must arrive before the receiving depot's morning release, so line-haul is
overnight by construction. The bullet is simply never applied: it is delegated
to `vrp.verify`'s INV-6, INV-3, INV-4 and INV-7, which run only in
`check_route_constraints` on a *solved* route. §5.3 is an assignment problem
and is planned rather than solved, so no route object reaches the verifier, and
the four cross-stage bullets in `check_day_constraints` include no shift check.
`ddn/linehaul/` never reads `shift_end` at all.

Nothing has been invented to close it, because **Open Question 8** already asks
the question it depends on — "whether overnight line-haul drivers are the same
pool as daytime van drivers". A separate night crew makes 02:30 to 06:30 an
ordinary shift and there is nothing to check. The same pool makes it the tail
of a day that began at 07:00, and the longest circuit found on real road data —
a §5.3.2 transfer from D5 to D6, 9.6 hours of driving, departing 20:53 and
arriving 06:30 — ends 23.5 hours after that van signed on. The data contract
cannot currently express either answer: a vehicle carries one shift window and
there is no field for a second crew.

The aggregate is not hidden, which is why this is a contract gap rather than a
silent one. §8.3's van-hours check, run over the peak day on recorded road
travel, reads **121 hours required against 110 available** — the fleet's own
§9.1 shifts, and the check that binds hardest of the three
(`tests/test_simulation.py::test_the_van_check_is_over_capacity_on_real_roads`
pins both figures). The day-level number sees the overload. The per-trip
planner has no rule with which to refuse a trip.

Suggested addition: a **crew or driver-pool field on the vehicle record**, or a
second shift window for overnight work. Either makes the bullet checkable for
line-haul; neither can be chosen here.

This one surfaced only when real inter-depot road times replaced a flat
40-minute placeholder in the tests. Under the placeholder, hub → D5 → D6 read
as 3.9 hours and looked ordinary.

---

## 5. What we would run, given the inputs

With §8's ratio and §3.1's coordinates, the same runs answer the delivery check
directly: envelopes per bike per facility, the shortfall against the day's pool,
and which constraint binds — the number that says whether to adjust the fleet,
the shift, or the 10-minute service time.

With Open Questions 1, 7, 9 and 13, §1's van check becomes a schedule rather
than a warning: how many vans to earmark, when to taper them, and whether the
return run needs its own.

Until then the honest answers are that the van bottleneck is real and
measurable today, and that the delivery question is not yet well posed.
