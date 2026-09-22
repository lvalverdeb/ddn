# Working assumptions — placeholder values

**Every value on this page is a placeholder.** None is supplied by
`vrp-problem-definition.md`, and none has been confirmed by operations. They
exist so that code can run and fixtures can be built; they are not measurements
and must not be quoted as operational figures.

Each row names where the gap is in the spec and where the number came from.
Three provenances are used, and they are not equally trustworthy:

| Provenance | Meaning |
|---|---|
| **document** | Stated in the spec, in a passage the spec itself labels illustrative (§10) |
| **derived** | Computed from figures the spec does state (§7.4's arithmetic) |
| **invented** | Chosen here with no source. Sensitivity noted. Treat every one as suspect |

`ddn/assumptions.py` mirrors this table and is what code reads;
`tests/test_assumptions.py` fails if the two drift apart.

## Fleet (§4.2, Open Question 9)

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `MOTORBIKES_TOTAL` | 120 | §4.2 fleet table | document (§10) |
| `VANS_TOTAL` | 10 | §4.2 fleet table | document (§10) |
| `PICKUP_VANS_EARMARKED` | 6 | §5.1.3 | document (§10) |
| `PICKUP_VANS_AFTER_TAPER` | 2 | §5.1.3 | document (§10) |

## Capacities (§4.1, §7.1, Open Question 1)

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `ENVELOPES_PER_BIKE` | 25 | §4.2's allocation figure | document — §4.2's "~25 envelopes per motorbike per day", from §7.4's arithmetic |
| `MAILBAGS_PER_VAN` | 40 | §4.1, §7.1, §10 all say `[TBD]` | **invented.** Binds only if bags per trip falls below demand; at the 200 g default weight the 500 kg limit never binds first |
| `STOPS_PER_ROUTE_SOFT_MAX` | 25 | §7.2 | derived: §7.4's 480 min ÷ 10 min = 48 stops before travel, and §4.2's ~25 per bike is the same figure |

## Service times (§7.4, Open Question 10)

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `PICKUP_STOP_MIN` | 10 | §7.4, §5.1.2 | **invented.** Scales pickup route length directly |
| `FACILITY_UNLOAD_MIN` | 30 | §7.4 | **invented.** Feeds every depot's latest van departure |
| `RETURN_STOP_MIN` | 10 | not tagged `[TBD]`; already hard-coded at `returns.py:52` | **invented.** Recorded here so it stops being invisible |

## Timing (§3.1, §5.1.5, §5.1.6, §5.6)

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `ROUTE_RELEASE` | 07:00 | §3.1, all facilities | **invented.** Delivery happens on D+1, so this is tomorrow's clock; line-haul departs the day before |
| `SHIFT_START` / `SHIFT_END` | 07:00 / 15:00 | §5.6 | derived: §7.4's 8-hour shift from the release time |
| `PROCESSING_CUTOFF` | 18:00 | §5.1.6, §5.6 | **invented** |
| `RETURN_RUN_DISPATCH` | 16:30 | §5.6 | **invented** |
| `VAN_IDLE_RETURN_MIN` | 45 | §5.1.5 | **invented** |

## Processing throughput (§5.2.5, Open Question 4)

Open Question 4 asks for "envelopes per hour for reconciliation, geocoding,
assembly (clean-room) and sorting; clean-room operating hours; share of
envelopes requiring assembly". None is supplied. These drive `expected_ready_at`,
which drives what line-haul can carry, so **a readiness figure computed from
them is a property of these numbers and not of the operation** — the same
warning `capacity-finding.md` carries about the 12.8 figure.

They are deliberately *not* fitted to §10's "assembly clears 700 of the 900":
that split is illustrative and priority-ordered, and tuning an invented rate to
reproduce it would manufacture agreement between two things that are both
guesses.

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `RECONCILE_PER_HOUR` | 1200 | §5.2.1, §5.2.5 | **invented** |
| `ASSEMBLY_PER_HOUR` | 120 | §5.2.3; §8.3 calls the clean room the likely bottleneck | **invented.** The one that decides how much rolls to tomorrow |
| `SORT_PER_HOUR` | 2400 | §5.2.4 | **invented** |

Geocoding has no rate here: §5.2.2 puts it before the bag arrives in the usual
case, so it is off the critical path for readiness.

## Pickups (§5.1.5, §3.2)

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `REOPT_CADENCE_MIN` | 30 | §5.1.5 | document — §5.1.5's own "e.g. every 30 minutes" |
| `EQUIDISTANT_MARGIN_M` | 2000 | §3.2 flags zips "straddling two facilities' areas"; Open Question 14 asks how | **invented.** Too small and nothing is flagged; too large and every envelope is. **What now acts on it (e2e-2 §2 item 1):** a zip-centroid envelope inside the margin is kept at the hub pending address geocoding rather than committed to a depot it may have to leave again — `processing.keep_straddlers_at_hub`, reported as `held-straddle`. §3.2 says only "[flag these]", so the flag is the parent's and the rule is e2e-2's, marked there for promotion. Open Question 14 is still open. |

## Outcomes (§6, §11)

Rates, not counts. §6 names the four outcomes and gives no frequencies; §10's
worked day does, so these are read off it: of the 2,950 envelopes §10
dispatches (its 3,100 morning pool less 150 unassigned — D1's 20 and 130 across
D2–D6) it delivers 2,750, rejects 50, finds 30 defective and postpones 120.

**That makes any simulated delivery total a restatement of §10, not a
prediction.** A run that reproduces 2,750 has confirmed the stages are wired
together, and nothing about the operation.

They were 935/16/10/39 until v0.13 closed §10's arithmetic — derived from
2,880 of 3,080. Nothing noticed for two revisions, because a rate read off a
document is checked by nothing that reads the document.

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `DELIVERED_PER_MILLE` | 932 | §6 gives no rates | document — §10's 2,750 of 2,950 |
| `REJECTED_PER_MILLE` | 17 | §6 | document — §10's 50 |
| `RETURNED_PER_MILLE` | 10 | §6 | document — §10's 30 defective |
| `POSTPONED_PER_MILLE` | 41 | §6 | document — §10's 120 |
| `POSTPONED_UNAVAILABLE_PER_MILLE` | 500 | §6 names three reasons and splits them nowhere | **invented** |
| `POSTPONED_BAD_ADDRESS_PER_MILLE` | 200 | as above; the remainder is "driver out of time" | **invented.** §6 re-geocodes these, which can move an envelope to another facility |

## The §8 objective

**This is the blocking unknown, and these are stand-ins for it.** §8 ranks
delivered work first and distance third and never says what one is worth against
the other. Until it does, every figure produced by a solve — which envelopes are
declined, and therefore `capacity-finding.md`'s envelopes-per-bike — describes
these four numbers as much as it describes the operation.

They were not absent before this page listed them. `contract.PRIZE_SCALE` and the
`models/*.json` objective blocks each carried one, unlabelled, while this document
said no placeholder existed. Registering them is not a decision to invent a ratio;
it is a decision to stop hiding the one already in use.

One cost unit is one metre, which is the platform's own convention
(`vrp/evaluator.py:46-51`).

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `PRIZE_SCALE` | 100 | §8 gives no envelope-versus-distance ratio | **invented.** Multiplies §8.1's score into a prize. Measured ceiling: above ~1,000 the solver returns routes past the end of the shift |
| `COST_PER_METRE` | 1 | §8 | **invented**, and it is also what PyVRP applies to an unpriced vehicle, so declaring it changes no plan — it makes the rate a decision rather than a default |
| `VEHICLE_FIXED_COST` | 50000 | §8 | **invented.** 50 km of riding to put one bike on the road. The term that decides whether a bike is deployed at all |
| `COST_PER_SECOND` | 0 | §8 | **invented.** §7.4 makes the shift a hard bound, so duration is a constraint here and not a cost |
| `READINESS_WEIGHT` | 0.0 | e2e-1 §2.1's λ; §9 asks how strongly pickup sequencing should bend toward the clean room | **invented, and zero on purpose.** At 0 the planner is §5.1.4 unchanged. Measured on §10's day a bag's readiness runs to a few thousand against legs of hundreds to thousands of seconds, so λ ≈ 0.1 trades them evenly and above 1 route cost stops mattering. Non-zero is a policy about clean-room idle time against van-hours, not a tuning choice |
| `BAND_URGENT_LOW` / `BAND_STANDARD_LOW` / `BAND_LOW_LOW` | 1000 / 100 / 1 | §8.1, Open Question 11: the categories and their gaps | document — §8.1's own illustration, "Urgent 1,000–1,999, Standard 100–199, Low 1–99" |

## Deferred

- **§8's objective 4, "balance workload across vehicles at the same facility".**
  Not implemented and not placeholdered. `docs/solver-capabilities.md` confirms no
  balance or equity primitive, so any encoding would be invented — and CLAUDE.md
  forbids inventing a solver capability. It is also not measurable yet: balance is
  a trade against the other three objectives, and those only acquired real weights
  in the change that added this section. Revisit once a run has been re-measured
  against them.

## Returns and the taper

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `RETURN_VEHICLE_TYPE` | van | §5.5, Open Question 13: does the van-only security rule apply to returns? | **invented**, cautiously: a van cannot breach a security rule that turns out to apply, a motorbike can |
| `PICKUP_TAPER_FROM` | 12:00 | §5.1.3 says the earmark "should taper during the afternoon" and gives no hour | **invented** |

## Facility placeholders (§3.1)

**Synthetic.** The real geography is unsupplied, and `capacity-finding.md` is
clear that depot placement moves the delivery answer more than any solver
setting does. These sit on Costa Rican towns because the OSRM graph
`simulation/on_road.py` uses is Costa Rica; they are **not** the operation's facilities,
and no figure measured on them describes the operation.

| ID | Placeholder locality | lat | lon | Transit from hub (min) | Provenance |
|---|---|---|---|---|---|
| HUB | San José | 9.9333 | -84.0833 | — | invented |
| D1 | Heredia | 9.9981 | -84.1197 | 30 | invented |
| D2 | Cartago | 9.8638 | -83.9199 | 45 | invented |
| D3 | Alajuela | 10.0162 | -84.2141 | 40 | invented |
| D4 | Puntarenas | 9.9763 | -84.8384 | 90 | invented |
| D5 | Limón | 9.9907 | -83.0359 | 195 | invented |
| D6 | Liberia | 10.6346 | -85.4377 | 240 | document (§10: "the 4-hour D6 transit") |

## Deliberately absent

- **§11's eleven metric targets.** A target is a commitment, not an input.
  Inventing "SLA compliance ≥ 98%" fabricates a customer promise. The one
  figure the spec does supply — 20–30 envelopes per bike per day — is in §11
  already.

  One had crept in anyway. `PICKUP_RESPONSE_TARGET_H = 4` was registered
  against §5.1.6, whose third bullet reads "request to collection within [TBD]
  hours" — and that bullet *is* §11's first row, *Pickup responsiveness*. The
  argument for keeping it was that §5.1.6 calls it "a service metric, not a
  solver constraint", which is true and beside the point: the objection to a
  target is not that the solver reads it. Nothing read it at all, in four
  months. Deleted.
- **§9.2's reason code for an unreachable address.** Needs a spec change, not
  a value. `ddn/lastmile/plan.py` carried one, unemitted and in neither
  `REASONS` set; it is gone, and `docs/spec-proposals/v0.15.md` proposes the
  reason so the pruned envelopes it was meant to explain can be reported.
