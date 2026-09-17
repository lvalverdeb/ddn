# §8.3's delivery check: what we can and cannot yet tell you

**Status:** measured 17 September 2026 against problem definition v0.10.
**Audience:** the author of the problem definition.

---

## Verdict up front

**§8.3's delivery check cannot be answered yet, and the reason is §8, not §7.4.**

We ran §5.4 end to end — 4,550 envelopes, six facilities, real Costa Rica road
distances, an independent verification per facility. All six plans verified. The
run produces **12.8 envelopes per deployed bike** against §7.4's expected 20–30.

**That number is not a capacity measurement and should not be used as one.** It
is what the *current objective* produces, and the objective is a placeholder.
Routes end with time to spare and decline work they could have done, because a
delivered envelope is currently priced below the kilometres needed to reach it.

Two things are needed before the check means anything. Neither is a solver
problem:

1. **§8 must fix one ratio**: what a delivered envelope is worth against a
   kilometre ridden.
2. **The customer geography must be the operation's**, not a synthetic corpus's.

---

## 1. Why the number is about the objective

§8 ranks the objectives: *maximise priority-weighted packages delivered* first,
*minimise unassigned* second, *minimise route time and distance* third. The
numbers we are running invert that order.

A standard envelope carries a priority score of 150. Routing charges 1 cost unit
per metre. So a standard envelope is worth **15,000** and a round trip to reach
it costs **2,000 per kilometre** — which makes the break-even **7.5 km one way**.
Anything further is refused, correctly, by the objective as written.

Measured at the hub, whose pool is 98% Greater Metropolitan Area:

| | median distance | p90 |
|---|---|---|
| envelopes served | 14.6 km | 26.1 km |
| envelopes declined | 16.6 km | 29.5 km |

The two distributions are almost the same. Distance is barely discriminating,
because nearly everything sits beyond break-even: a round trip to the median
*declined* envelope costs 33,222 against its 15,000 prize.

**The bikes are not out of time.** Route anatomy at D2, ten bikes:

```
route    stops  service   travel    span    idle
MOTO-1      15     150m     250m    400m     80m
MOTO-3       8      80m     274m    354m    126m
MOTO-6      17     170m     166m    336m    144m
...                                   (median idle ~110 min of a 480 min shift)
```

§7.4 predicts that *"the shift duration, not the 35-envelope limit, is the
binding constraint"*. In this run **nothing binds** — not the shift, not the
35-envelope capacity, not the route-duration limit. The solver stops because the
next envelope costs more than it is worth, and it is right to, given the prices
it was given.

### What we need from §8

One number, or the pair that implies it: **what is a delivered envelope worth,
in the same units as a kilometre ridden?** §1 says the fleet is fixed, which
matters here — a bike already allocated and already riding has a *marginal* cost
of fuel and rider time, not a full commercial rate per kilometre. If the marginal
rate is what belongs in the objective, most of what is currently declined becomes
worth doing, and the capacity answer changes accordingly.

We can derive nothing defensible here. The ratio is a business fact.

---

## 2. Why the geography must be yours

Assignment follows §3.3's nearest-facility rule over the corpus in this
repository, which spreads 50,000 deliveries across the whole country. That is not
the operation the document describes.

D2 (Grecia) draws envelopes a **median 49 km away, up to 106 km**. No motorbike
round exists at that radius — a round trip costs ~89,000 against a 15,000 prize
before any service time. The hub is more plausible at 14.6 km median, but even
that is a wide radius for last-mile motorbike delivery.

§3.1's facility coordinates are still `[TBD]`, so we used the six real depots the
corpus carries. **A different depot placement changes this answer more than any
solver setting does.** Until §3.1 is filled in and the customer distribution is
the real one, no capacity figure from us should be quoted.

---

## 3. What the run does establish

These stand independently of the two unknowns:

- **The operation is expressible.** §5.4 runs end to end and every plan passes an
  independent verifier that shares no code with the solver.
- **Travel matrices are not a constraint.** A hub-scale matrix (1,601 stops,
  2.5M cells, 289 tiles) builds in **29.4 s**; the whole night across seven
  facilities is about **50 s**.
- **The one-day lag simplifies the problem.** Because §5 puts all delivery on
  D+1, last mile is a static overnight batch against a pool known by morning —
  no dynamic routing is needed for delivery.
- **Shift time is not the binding constraint under any setting we tried**, which
  is worth knowing because §7.4 assumes it is. Once §8's ratio is real, that
  assumption becomes testable rather than assumed.

---

## 4. One gap in the data contract

**§9.2's reason vocabulary has no code for an address with no road path.** The
available reasons are *time, count, cut-off missed, low geocode confidence, SLA
expired*.

Real road data contains addresses that are reachable in principle but have no
route from their assigned facility — a one-way system, a severed fragment, an
island. Our triage detects them and has nowhere honest to report them. *Low
geocode confidence* is the nearest fit and is wrong: the address geocoded fine.

This matters operationally because the remedy differs. A low-confidence address
needs re-geocoding; an unroutable one needs a different facility or a note that
it cannot be served by this network at all.

Suggested addition to §9.2: **`no road path`**.

---

## 5. What we would run, given the two inputs

With §3.1's coordinates and §8's ratio, the same run answers §8.3 directly:
envelopes per bike per facility, the shortfall against the day's pool, and which
constraint binds — which is the number that says whether to adjust the fleet, the
shift, or the 10-minute service time.

Until then, the honest answer to *"how many envelopes can 120 bikes deliver?"* is
that the model is ready and the question is not yet well posed.
