# Mini end-to-end 1 — Mailbag pickups to the consolidation hub

**Status:** Draft v0.1 · 21 September 2026
**Parent:** `docs/vrp-problem-definition.md` v0.14 (§3.2, §4.1, §4.3, §5.1, §5.2, §7, §9, §13)
**Purpose:** Define a self-contained, testable slice covering everything from a customer declaring a mailbag ready to that bag's envelopes becoming Ready at the hub, so this slice can be built, run and accepted independently of line-haul and delivery.

This document scopes; it does not restate. Where a rule is already in the parent spec, it is cited, not copied. Where this slice needs something the parent does not say, it is stated here and flagged for promotion to the parent.

---

## 1. Boundary

**Starts when:** a customer submits an upload file and/or declares a mailbag ready (§5.1.1).
**Ends when:** every envelope in the collected bags is in one of: Ready (sorted to a facility), Held (disputed / low-confidence geocode), or Rolled (not ready by the processing cut-off).

**Inside the boundary**
- Upload-file receipt and pre-processing: geocoding, pre-sorting to a facility, assembly demand counting (§5.1.1, §5.2.2, §5.2.3).
- Dynamic van-only pickup routing with earmarked vans, re-optimisation, multi-trip, and release to line-haul (§5.1.3–5.1.5).
- Admission against the processing cut-off (§5.1.6).
- Hub processing timing: reconciliation, assembly queue, sorting, expected-ready-time (§5.2.1–5.2.5).
- Pickup exceptions (§5.1.7).

**Outside the boundary**
- Line-haul departures (E2E-2). This slice only *produces* the ready-by-facility pool and the van release times that E2E-2 consumes.
- Delivery and outcomes (E2E-3).
- The return run (§5.5), except that returning vans may be the ones released here.

## 2. What this slice must do that the parent does not spell out

### 2.1 Pickups consider the contents of the bag, not just its count

A bag's manifest and upload file tell the hub, before collection, how many of its envelopes are **finished** (ready after reconciliation and sorting) and how many are **to-be-assembled** (must pass through the clean room, §5.2.3). Because assembly is the likely bottleneck (§8.3), when a bag arrives matters differently for the two kinds:

- A finished envelope becomes Ready roughly `reconcile + sort` minutes after the bag arrives.
- A to-be-assembled envelope becomes Ready after `reconcile + assembly queue wait + assembly + sort`, and the queue wait depends on how much assembly work arrived before it.

**Rule for this slice.** Pickup insertion cost includes the downstream effect on readiness: among otherwise equal insertions, the planner prefers collecting bags with assembly content earlier in the day, so the clean room is fed continuously rather than flooded at cut-off. Concretely, each bag carries a **readiness value** = Σ over its envelopes of priority × P(ready by the relevant cut-off | arrival at time t), and the dynamic planner's insertion objective is (least additional route cost) − λ × (readiness value gained). λ is a registered placeholder. *[Promote to parent §5.1.4 once validated.]*

The relevant cut-off per envelope is its **pre-sorted facility's** latest van departure (§3.1) for depot-bound envelopes, and the end of hub processing for hub-direct ones — so the pickup planner needs the pre-sort result from the upload file (§5.2.4) at insertion time. This is the concrete reason the upload file preceding the bag (§5.1.1) matters to routing.

### 2.2 Assembly queue is priority-ordered from file receipt

Per §5.2.3, when queued assembly work exceeds clean-room capacity to cut-off, the queue is ordered by (priority desc, SLA date asc, arrival). This slice computes the queue as files arrive, so `expected_ready_at` for to-be-assembled envelopes reflects their queue position, not just their bag's arrival.

### 2.3 Late admission

A bag that cannot be back at the hub before the processing cut-off is not admitted today (§5.1.6 v0.13). Its envelopes' `expected_ready_at` is tomorrow's, and they are visible to E2E-2's next-day planning immediately.

## 3. Inputs (from §9.1)

| Entity | Fields used here | Source |
|---|---|---|
| Pickup requests / mailbags | mailbag_id, customer_id, site coords, requested_at, envelope_count, assembly_required_count, expected_weight_g, pickup_window (optional), seal_id | Customer / call centre |
| Envelopes (pre-arrival) | package_id, package_type, lat/lon, coord_source, geocode_confidence, priority, sla_date, weight_g | Upload file |
| Vehicles | vans only: capacity_mailbags, capacity_weight_g, shift, role=pickup, linehaul_release_at | Allocation (§4.2) |
| Facilities | hub coords; per-depot latest_van_departure (for the readiness cut-off) | §3.1 |
| Assumptions | reconcile / assembly / sort throughput, clean-room hours, pickup stop service time, re-optimisation cadence, λ, processing cut-off | `docs/assumptions.md` |

## 4. Outputs

| Output | Consumer |
|---|---|
| Pickup plan per van: ordered stops, ETAs, trips, return-to-hub times; refreshed each cycle | Driver app (§13.2 `GET /pickups/plan`) |
| Not-admitted bags with reason (late / no van capacity / site closed) | Call centre |
| Per envelope: status transition Requested → Collected → Received → Reconciled → [Assembled] → Sorted → Ready, with `expected_ready_at` at each step | E2E-2 (ready-by-facility pool), E2E-3 (next-day hub-direct pool) |
| Held envelopes with reason (disputed / low-confidence geocode / straddling zip) | Operations |
| Van release events: van_id, time back at hub, unloaded | E2E-2 (line-haul availability) |
| Assembly queue state: queued, in progress, cleared, rolled — with the rolled set identified | Clean-room operations, §11 same-day readiness metric |

## 5. Constraints active in this slice

From §7.1: van mailbag capacity; 500 kg; route within shift; van does not depart on line-haul until back and unloaded; pickups van-only; bags collected whole; only Ready envelopes are routable (enforced here by *producing* the Ready state correctly).
From §7.2: none binding, except that admission should not defer a bag today that a same-day trip could have collected.
Service times: pickup stop [TBD]; hub unloading [TBD] (§7.4).

## 6. Objective for this slice

Maximise priority-weighted envelopes Ready by their relevant cut-off, then minimise van route time. This is the pickup-side projection of §8 objectives 1 and 3; distance matters only after readiness.

## 7. Acceptance scenarios

Numbers from §10 v0.13; fleet and throughput placeholders from `docs/assumptions.md`.

| # | Scenario | Expected |
|---|---|---|
| A1 | 180 bags at 70 sites over the day, 6 pickup vans tapering to 2 by mid-afternoon (4 released) | All 180 collected; every van back before its `linehaul_release_at` or shift end; no bike receives a stop |
| A2 | Same day; 900 of 4,800 envelopes need assembly | Bags with assembly content are on average collected earlier than bags without; clean room is never idle while assembly work is queued at a customer site |
| A3 | Assembly throughput limits same-day clearance to 700 | Exactly 200 assembly envelopes roll; they are the 200 lowest-priority (ties by latest SLA) |
| A4 | Ready pool at cut-off | 4,550 Ready split HUB 1,600 / D1 850 / D2 650 / D3 550 / D4 400 / D5 320 / D6 180; 10 disputed and 40 low-confidence Held |
| A5 | A bag declared at 15:30 whose round trip cannot beat the cut-off | Not admitted today; envelopes carry tomorrow's `expected_ready_at`; visible to next-day planning |
| A6 | A bag arrives with a broken seal | Collected, flagged; envelopes held pending full reconciliation; customer notified |
| A7 | Two bags at one site exceed a van's remaining bag capacity | Split across vans or second visit; neither bag split |
| A8 | Re-optimisation cycle with a new request | Visited stops unchanged; new stop inserted on the van with least (cost − λ·readiness); plan published within the cycle |
| A9 | Upload file arrives *after* the bag (exception) | Envelopes processed in the slower order; `expected_ready_at` later than a same-time bag with a file; flagged late-ready |

## 8. Metrics (subset of §11)

Pickup responsiveness; same-day readiness; reconciliation discrepancy rate; assembly rolled count and its priority profile; van-hours on pickups vs planned earmark.

## 9. Open questions specific to this slice

- λ (readiness weight) — how strongly should pickup sequencing bend toward feeding the clean room?
- Clean-room operating hours and whether assembly continues after the pickup cut-off.
- Pickup stop service time and hub unloading time (§12 Q10).
- Standing pickups (§12 Q3).
- Bags per van (§12 Q1).
