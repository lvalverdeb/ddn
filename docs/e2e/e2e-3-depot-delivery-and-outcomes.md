# Mini end-to-end 3 — Delivery from each depot and outcome handling

**Status:** Draft v0.1 · 21 September 2026
**Parent:** `docs/vrp-problem-definition.md` v0.15 (§4.1–4.2, §5.2.6, §5.4, §5.5, §6, §7, §8, §9, §13)
**Purpose:** Define a self-contained slice covering a single facility's delivery day: from morning route release on the positioned pool, through motorbike routing and delivery attempts, to every outcome's consequence — delivered, rejected, returned (defective), postponed with retry, cancelled, and SLA expiry — and the hand-offs those create for the return run and for transfers. The same slice applies to the hub acting as a delivery depot.

---

## 1. Boundary

**Starts when:** a facility's morning route release fires on the pool positioned there (E2E-2's output) plus envelopes it already held (postponed from earlier days).
**Ends when:** every envelope dispatched that day has an outcome recorded and its next-state assigned, and the facility's end-of-day hand-offs are produced.

**Inside the boundary**
- Per-facility CVRP with route-duration limit, 10 min per envelope, 35-envelope cap, motorbikes only (§5.4, §7.4).
- Selection under shortage: priority-weighted, SLA-today forced in, locks honoured (§8, §8.2).
- Outcome recording and consequences (§6, §6.1).
- Retry scheduling: postponed envelopes re-enter the facility pool; address correction → re-geocode → same facility or transfer request (§6, §5.3.2).
- **Cancellation** (new; see §2.2).
- SLA expiry sweep at end of day.
- Hand-offs: return load for the van leg (depot) or return-run input (hub); transfer requests.

**Outside the boundary**
- Positioning envelopes at the facility (E2E-2).
- Carrying transfers or returns (E2E-2); routing the return run itself (§5.5 — may be a fourth slice).
- Fleet allocation (§4.2); this slice takes its motorbikes as given.

## 2. What this slice must make explicit

### 2.1 The daily pool and its ordering
Pool at release = positioned yesterday ∪ held postponed (any earlier day) ∪ transfers that arrived overnight. Unassigned selection is priority first (§8.1), with two hard rules: SLA-today envelopes are always in (§6.1); locked-in envelopes are always in and locked-out never are (§8.2). Ties by SLA date, then attempt number (more attempts first — an envelope that has failed twice is costlier to hold).

### 2.2 Cancellation
The parent's outcome table (§6) has no cancellation. This slice defines it: **a customer withdraws an envelope before delivery.** Rules:
- If the envelope is in the pool and not yet on a route, it leaves the pool immediately and joins the return-to-customer flow (Rejected-like handling, reason "cancelled").
- If it is on an active route, the driver is notified at the next plan refresh; the stop is removed if not yet visited; if already visited and delivered, cancellation is refused (state is terminal).
- Lifecycle edges: Ready → Return run (cancelled before dispatch) **and**
  Dispatched → Return run (cancelled on a route, stop not yet visited). The
  second is not in the bullet above and the slice cannot be built without it:
  §5.2.6 drew nothing out of Dispatched but the four attempt outcomes.
  *[Landed in parent v0.15; see `docs/spec-proposals/v0.15-cancellation.md`.]*

### 2.3 Retry
Postponed envelopes (§6) re-enter the same facility's pool for the next day with `attempt_number + 1`, unless the sub-reason is incorrect address, in which case re-geocoding runs first and may raise a transfer (E2E-2 carries it; this slice raises it). No attempt cap; SLA date bounds (§6.1). Priority is not modified by this slice — the prioritisation algorithm already reflects SLA proximity.

### 2.4 SLA expiry
At end of day, any envelope still in the pool whose SLA date is today, or a postponed envelope whose SLA date has passed, moves Ready → Return run (SLA expired) (§5.2.6 v0.13). It is never dispatched again.

### 2.5 Depot vs hub hand-off
At a depot: Rejected, Returned, Cancelled and SLA-expired envelopes stay at the depot as a return load for the next van leg (§5.5, §10 v0.13); they appear in the return run the *following* evening. At the hub: they enter *tonight's* return run directly.

## 3. Inputs

| Entity | Fields used here | Source |
|---|---|---|
| Envelopes (positioned + held) | package_id, lat/lon (address-level), priority, sla_date, weight_g, attempt_number, previous_outcome, locked_vehicle_id / locked_out | E2E-2, previous days |
| Vehicles | motorbikes at this facility: capacity_envelopes=35, shift | Allocation |
| Facility | coords, route_release_time, shift | §3.1 |
| Events during the day | outcome per stop (delivered / rejected / returned / postponed + sub-reason), cancellations, locks | Driver app, operations (§13.2) |
| Assumptions | ENVELOPES_PER_BIKE (planning only), outcome rates (simulation only), service time 10 min | `docs/assumptions.md` |

## 4. Outputs

| Output | Consumer |
|---|---|
| Routes per motorbike with ETAs; §7.1 violation list (empty on success) | Drivers, §13.2 `GET /routes/runs/{id}` |
| Unassigned envelopes with reason (time / count / locked-out / SLA expired) | Operations, next-day pool |
| Envelope state after outcome: Delivered (terminal); Rejected / Returned / Cancelled / SLA-expired → return flow; Postponed → Ready (retry) or Transfer requested | E2E-2 (returns, transfers), next-day pool |
| Return load at the facility (package_ids, weight) | E2E-2 (depot) or return run (hub) |
| Transfer requests raised (package_id, from, to, reason=address_correction, deadline) | E2E-2 |
| Day metrics (§11 subset) | Reporting |

## 5. Constraints active in this slice

From §7.1: 35 envelopes; route within shift (service + travel); one vehicle per envelope per day; only Ready envelopes; not dispatched after SLA date; route starts and ends at home facility.
From §7.2: postponed not retried on next available day; SLA-approaching left unassigned.
Service time 10 min per envelope, no reduction for same recipient (§7.4).

## 6. Objective for this slice

§8 as written: priority-weighted delivered, then unassigned count, then route time, then balance across this facility's motorbikes. Because 35 × 10 min exceeds the shift, expect routes of 20–30 stops and the shift, not the count, to bind (§7.4).

## 7. Acceptance scenarios

| # | Scenario | Expected |
|---|---|---|
| C1 | D1: 620 in pool, 24 bikes | 600 dispatched; 20 unassigned are the lowest-priority non-SLA-today, non-locked; no route exceeds shift; each route ≤ 35 and starts/ends at D1 |
| C2 | HUB: 1,150 in pool, 48 bikes | All dispatched |
| C3 | D2–D6 combined: 1,330 in pool, 48 bikes | 130 unassigned across the five, each depot's own lowest-priority |
| C4 | An SLA-today envelope ranked 619th of 620 at D1 | Dispatched; a higher-priority non-SLA-today envelope is the one left |
| C5 | Operations locks in 5 envelopes and locks out 3 at D1 | Re-run: the 5 are on routes, the 3 are unassigned with reason "locked-out", other assignments change minimally |
| C6 | Outcomes at D1: 50 rejected, 30 defective, 120 postponed (40 incorrect address) | 80 join D1's return load for the next van leg, not tonight's return run; 80 postponed re-enter D1's pool with attempt+1; 40 re-geocoded — those whose nearest facility changed raise transfer requests, the rest re-enter D1's pool |
| C7 | Same as C6 but at HUB | Rejected/defective enter tonight's return run |
| C8 | Customer cancels an envelope at 10:00 that is on a route with ETA 14:00 | Stop removed at next refresh; envelope → return flow with reason "cancelled" |
| C9 | Customer cancels an envelope already delivered | Cancellation refused; state stays Delivered |
| C10 | Envelope with SLA date today is postponed (recipient unavailable) | Not re-entered; moves to return flow with reason "SLA expired" at end of day |
| C11 | Envelope postponed three times, SLA in five days | Re-enters each day; no cap applied; ranking tie-break favours it over a same-priority first attempt |
| C12 | Mutation: disable the home-facility check | At least one test fails on the violation *detail* text |

## 8. Metrics (subset of §11)

First-attempt delivery rate; postponement rate by sub-reason; unassigned rate by reason; SLA compliance; SLA expiry rate; cancellations; envelopes per motorbike per day; transfers raised.

## 9. Open questions specific to this slice

- Cancellation: can a customer cancel by API only, or also by phone via operations? Is there a cut-off after which cancellation is refused even if not yet visited?
- Whether a driver can record a partial outcome (e.g. recipient present but refuses to sign) and which §6 outcome that maps to.
- Whether postponed envelopes should ever be re-prioritised by this system rather than by the prioritisation algorithm (§6.1 says no; confirm).
- Return-run vehicle type for hub returns (§12 Q13).
