# Mini end-to-end 2 — Hub to depots: keep or transport

**Status:** Draft v0.1 · 21 September 2026
**Parent:** `docs/vrp-problem-definition.md` v0.15 (§3.1–3.3, §4.1–4.3, §5.2.4–5.2.5, §5.3, §7, §8.3, §9, §13)
**Purpose:** Define a self-contained slice covering everything from an envelope becoming Ready at the hub to its being positioned, by morning route release, at the facility that will deliver it — either kept at the hub or transported to a depot — including inter-depot transfers and returns riding the same circuits.

---

## 1. Boundary

**Starts when:** an envelope becomes Ready at the hub (E2E-1's output), or a transfer request or return load is raised at a depot.
**Ends when:** every Ready envelope is either **positioned** at its dispatching facility before that facility's morning route release, or explicitly **rolled** to the next day's line-haul with a reason.

**Inside the boundary**
- The keep-or-transport decision per envelope: nearest-facility rule (§3.3), including the straddle flag for zip-centroid coordinates (§3.2).
- Van availability from pickup release times and the pickup/line-haul split (§4.3).
- Line-haul circuit planning: hub-origin loads, transfers, returns, per-leg 500 kg, arrival before morning release, overnight runs (§5.3.1–5.3.2).
- Waiting decisions: whether a van waits for more envelopes to become Ready (§5.3.1).
- Ranking under van shortage (§5.3.2 priority rule).

**Outside the boundary**
- How envelopes became Ready (E2E-1); how transfers were raised (E2E-3 raises them; this slice carries them).
- Delivery from the facility (E2E-3).
- Motorbike allocation (§4.2), except that this slice consumes the van split and *may* propose rebalancing transfers (§5.3.2 third trigger).

## 2. The keep-or-transport decision

An envelope stays at the hub if the hub is its nearest facility by road distance (§3.3). Everything else is depot-bound. Three refinements this slice must make explicit:

1. **Coordinate precision.** The decision uses address-level coordinates when available and the zip centroid otherwise. A zip-centroid envelope whose centroid is within `EQUIDISTANT_MARGIN_M` of two facilities is flagged; the working rule is to **keep it at the hub** pending address geocoding rather than commit it to a depot it may have to leave again (§3.2, §12 Q14). *[Promote to parent once confirmed.]*
2. **Hub as a delivery depot.** Hub-direct envelopes are not "not transported"; they are positioned at the hub and enter E2E-3's hub pool. This slice reports them as positioned like any other.
3. **Deadline per envelope.** Depot-bound: the depot's morning route release (§3.1), bounded by SLA date. Hub-direct: the hub's morning route release — trivially met if Ready today, so hub-direct envelopes never roll.

## 3. Circuit planning

Per §5.3.2: facilities are nodes; a van trip is an ordered sequence of legs starting at the hub, ending at the hub or overnight at a depot; loads on each leg are hub-origin envelopes for facilities ahead, transfers picked up at earlier depots, and returns bound for the hub. The problem is a seven-node pickup-and-delivery with per-load deadlines and van-hours as the scarce resource.

**Decisions**
- Which van serves which sequence of depots, departing when.
- Whether to wait: a van bound for a depot whose latest departure is later than now may wait for envelopes still in processing (§5.3.1); it waits only if the expected additional Ready envelopes (from E2E-1's `expected_ready_at`) outweigh the risk to the release deadline.
- Which loads are dropped when van-hours or weight are short: hub-origin loads and transfers ranked together by priority score; SLA-today transfers are hard inclusions (§5.3.2).
- Rebalancing proposals: when a depot's projected pool exceeds its motorbikes × 25 before SLA and a neighbour has slack, propose transfers if a leg exists or fits within van-hours (§5.3.2, §12 Q15). Proposals go to allocation; this slice does not decide them alone.

## 4. Inputs

| Entity | Fields used here | Source |
|---|---|---|
| Envelopes (Ready at hub) | package_id, facility_id (pre-sort), coord_source, geocode_confidence, priority, sla_date, weight_g, expected_ready_at for those still processing | E2E-1 |
| Transfer requests | transfer_id, package_id, from/to facility, reason, deadline, weight_g | E2E-3 / operations |
| Return loads at depots | package_ids in Rejected / Returned / SLA-expired at each depot, weight | E2E-3 |
| Vehicles | vans: capacity_weight_g, shift, role, linehaul_release_at, current location | Allocation, E2E-1 release events |
| Facilities | coords, route_release_time, transit_from_hub_min, latest_van_departure, inter-depot transit (§9.1 v0.15 proposal) | §3.1, travel matrix |
| Assumptions | unload time, overnight-run rules, EQUIDISTANT_MARGIN_M, rebalancing cost basis | `docs/assumptions.md` |

## 5. Outputs

| Output | Consumer |
|---|---|
| Line-haul plan: per van, legs (from, to, depart, arrive), loads per leg (hub-origin by destination, transfer_ids, return package_ids), total weight per leg | Drivers, §13.2 `GET /linehaul/plans/{id}` |
| Positioned pool per facility for tomorrow: package_ids, with hub-direct listed under HUB | E2E-3 (morning route release) |
| Rolled envelopes with reason: no van / weight / deadline unreachable / held-straddle | Operations, next-day plan |
| Transfers carried, deferred (reason), or sent to returns (deadline unreachable) | E2E-3, operations |
| Rebalancing proposals: from, to, package_ids, expected leg | Allocation (§4.2) |
| §7.1 violation list from `check_day_constraints` — empty on success | §13.1 |
| Van-hours used vs available | §8.3 capacity check |

## 6. Constraints active in this slice

From §7.1: 500 kg per leg across all load types; van not on line-haul until back from pickups and unloaded; route within shift; envelopes dispatched from a depot only after arrival (this slice guarantees arrival before release); a transfer is not raised if it cannot meet SLA (here: not *carried*, and reported).
From §7.2: departing with spare capacity when more would be Ready shortly; deferring a transfer a same-night circuit could have carried; rebalancing by transfer when bike reallocation was cheaper or vice versa.

## 7. Objective for this slice

Maximise priority-weighted envelopes positioned before their facility's morning release (hub-origin and transfers together), then minimise van-hours. Distance is secondary; missing a release deadline costs a full day.

## 8. Acceptance scenarios

| # | Scenario | Expected |
|---|---|---|
| B1 | §10 day: 4,550 Ready; 1,600 hub-direct; 2,950 depot-bound; 4 held vans + 3 released, one late van; D6 at 4 h transit | All 4,550 positioned by each facility's morning release; D6 load departs on an overnight run and its van is unavailable next morning; no leg over 500 kg; violation list empty |
| B2 | No transfers, no returns | Plan leg-for-leg identical to the single-destination baseline (regression, Task 8) |
| B3 | 30 transfers layered on B1 (18 address corrections, 9 misassignments, 3 rebalancing), 2 of which cannot meet SLA | 28 carried on inter-depot legs and arrive before deadline; 2 reported to returns; hub-origin loads not displaced except by higher-priority transfers |
| B4 | Van-hours short by one circuit | Dropped loads are the lowest-priority; every SLA-today transfer and envelope is carried; rolled list carries reason "no van" |
| B5 | 200 envelopes still in assembly at 16:00, expected Ready by 17:30; D2's latest departure 18:00 | The D2 van waits and carries them; a van for D6 (latest departure 15:00) does not |
| B6 | 40 zip-centroid envelopes within EQUIDISTANT_MARGIN_M of two depots | Kept at hub and flagged; not on any van; appear in rolled list with reason "held-straddle" |
| B7 | 50 rejected at D3 during the day | Ride D3's return leg; arrive at hub; appear in *tomorrow's* return run input, not tonight's |
| B8 | A van released from pickups at 15:10 with unloading 20 min | Not assigned a leg departing before 15:30 |
| B9 | Rebalancing: D1 projected 900 vs 600 capacity, D2 400 vs 450 | Proposal to transfer ≤ 50 lowest-priority D1 envelopes to D2 if a D1→D2 leg fits; otherwise no proposal and D1 rolls by priority |

## 9. Metrics (subset of §11)

Envelopes positioned by release / Ready; transfers carried / deferred / to-returns; van utilisation (kg and hours); rolled count by reason.

## 10. Open questions specific to this slice

- Inter-depot transit times and whether inter-depot legs run in daytime (§12 Q15).
- Whether overnight drivers are the daytime van pool (§12 Q8).
- Rebalancing cost basis (§12 Q15).
- Whether a straddling zip envelope should be held at hub (this document's working rule) or committed to the nearer depot.
