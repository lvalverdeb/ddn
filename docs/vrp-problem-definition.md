# Document Delivery Network — VRP Problem Definition

**Status:** Draft v0.17
**Date:** 23 September 2026
**Owner:** [TBD]
**Audience:** Operations, IT integration team, VRP solution vendor/maintainers

---

## 1. Purpose and scope

This document defines the operational problem that the existing Vehicle Routing Problem (VRP) solution must solve for our document delivery service. It describes the network, the flows, the constraints and the objectives in enough detail for the solver to be configured and for its outputs to be validated against operational reality.

**In scope (decided by the solver, or by a planning step immediately upstream of it):**
- Daily allocation of the shared fleet across the hub and the six secondary depots.
- Routing of mailbag pickups from customer sites, which arise throughout the day, using vans earmarked at the hub.
- Assignment of ready envelopes to vehicles at the consolidation depot and at each secondary depot.
- Sequencing of stops (routes) for each vehicle on a given day.
- Clustering of distant envelopes into van loads for line-haul to secondary depots.
- Routing of the end-of-day return-to-customer run.
- Identification of envelopes that cannot be served with the available fleet on a given day.

**Out of scope (handled by existing systems or people):**
- Hub processing itself: manifest reconciliation, clean-room assembly and sorting. The solver only sees envelopes once they are *ready* (§5.2), but it needs their expected ready time.
- Geocoding of delivery addresses (done upstream; the solver receives coordinates and their source).
- Package filtering and prioritisation, including SLA proximity (done by the existing prioritisation algorithm; the solver receives a priority value per envelope).
- SLA negotiation with customers.
- Fleet sizing (total fleet is fixed; only its distribution varies).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Envelope (package)** | A single document consignment with one delivery address, weighing 200–1,000 g. The unit of delivery and of the solver's assignment decisions. |
| **Mailbag** | A sealed bag in which a customer accumulates envelopes during the day. The unit of pickup. Carries a manifest and a tamper-evident safety device. |
| **Manifest** | The list of envelopes a mailbag contains, supplied by the customer with the bag. |
| **Upload file** | Data file supplied by the customer per mailbag: package_id, customer_id, recipient_id, package_type, delivery address and coordinates (actual or zip-code centroid). |
| **Pickup** | Collection of one or more sealed mailbags from a customer site and their transport to the consolidation depot, **by van only** for security reasons. Requested as bags become available during the day. |
| **Consolidation depot (hub)** | Central facility where mailbags are opened, reconciled against manifests, envelopes assembled if needed, sorted by target depot and made ready. Also a delivery depot for nearby envelopes. |
| **Reconciliation** | Tallying a mailbag's contents against its manifest; discrepancies are reported to the customer. |
| **Assembly** | Clean-room process at the hub that turns unfinished customer material into a finished envelope. Applies only to certain package types. |
| **Sorting** | Grouping ready envelopes by target facility (hub or D1–D6) by proximity of their coordinates. |
| **Ready** | An envelope that has been reconciled, assembled if required, and sorted. Ready envelopes are line-hauled the same day (if depot-bound) and delivered the next day. |
| **Secondary depot** | One of six regional facilities. Receives ready envelopes by van from the hub and dispatches them by motorbike. |
| **Line-haul** | Van movement between facilities carrying sorted envelopes: hub → depot (primary), depot → depot (transfer), depot → hub (returns). |
| **Transfer** | Movement of an envelope from the depot where it currently sits to a different depot, because its correct dispatching facility has changed or was wrongly assigned. Carried on inter-depot van legs. |
| **Last mile** | Motorbike delivery from the hub or a secondary depot to the recipient. |
| **Priority** | A numeric score from the existing prioritisation algorithm; already incorporates SLA proximity. |
| **SLA date** | Latest delivery date for an envelope. After this date it is returned to the customer. |
| **Outcome** | Result of a delivery attempt: Delivered, Rejected, Returned, Postponed (§6). A **Cancelled** envelope is recorded in the same table but is not the result of an attempt. |
| **Cancelled** | Of an **envelope**: withdrawn by the customer before delivery (§6). Of a **transfer**: abandoned before it is carried, leaving the envelope at its current depot (§5.3.2). Of a **pickup request**: withdrawn before collection (§5.1.7). Three distinct events on three distinct entities; only the first is a §6 outcome. |
| **Return run** | End-of-day trip returning rejected, defective, cancelled and SLA-expired envelopes to customers. |
| **Earmarked capacity** | Hub vans reserved for pickups during the day and therefore not available for line-haul until released. |

---

## 3. Network and data foundations

### 3.1 Facilities

| ID | Name | Type | Coordinates (lat, lon) | Morning route release | Transit from hub | Latest van departure from hub |
|---|---|---|---|---|---|---|
| HUB | Consolidation depot | Hub + delivery depot | [TBD] | [TBD] | — | — |
| D1 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |
| D2 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |
| D3 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |
| D4 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |
| D5 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |
| D6 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [release − transit − unload] |

The hub is the main processing point: every envelope passes through it before dispatch. **The daily rhythm is a one-day lag:** pickup, consolidation, sorting and transport to depots all happen on day D; delivery — from the hub and from every depot — happens on day D+1. Each depot's constraint is therefore that the van arrives before its morning route release, which for distant depots means an overnight run. The latest van departure from the hub is derived per depot from release time minus transit minus unloading.

### 3.2 Coordinates and their source

Each envelope arrives with coordinates from the customer's upload file, which are one of two kinds:

| Source | Precision | Fit for depot assignment | Fit for last-mile routing |
|---|---|---|---|
| **Actual delivery coordinates** | Address level | Yes | Yes |
| **Zip-code centroid** | Zip area (may be several km) | Yes, in most cases | No — must be geocoded from the delivery address first |

Consequences:
- **Sorting by target facility** can use zip centroids directly; the nearest-facility decision is coarse enough that zip precision is acceptable except for zips straddling two facilities' areas [flag these].
- **Last-mile routing** needs address-level coordinates. Envelopes carrying only a zip centroid must pass through an address geocoding step at the hub before they are marked ready. Where geocoding fails or returns low confidence, the envelope is held and flagged rather than routed, since an incorrect location is a leading cause of postponement.
- The solver input therefore carries a `coord_source` field (actual / geocoded-address / zip-centroid) and a confidence level.

### 3.3 Facility assignment rule

Each envelope is assigned to exactly one dispatching facility (HUB or D1–D6) using **nearest facility by road distance** from its delivery coordinates (address-level where available, otherwise zip centroid). No fixed service-area polygons are used.

- **Hub-direct:** nearest facility is the hub → delivered by motorbike from the hub.
- **Depot-bound:** nearest facility is a secondary depot → transported there by van, then delivered by motorbike.

---

## 4. Fleet

### 4.1 Vehicle types

| Type | Delivery capacity | Pickup capacity | Roles |
|---|---|---|---|
| Motorbike | 35 envelopes (shift time usually binds first, §7.4) | — | Last-mile delivery; return run |
| Van | 500 kg | [TBD] mailbags / 500 kg | Mailbag pickups; line-haul; return run |

**Mailbags are collected by vans only**, for security: sealed bags are not carried on motorbikes. Motorbikes therefore do last-mile delivery exclusively (and the return run, subject to Open Question 13). Van pickup capacity is in **mailbags** (bags are collected whole) with the 500 kg weight limit as a secondary bound; a bag's weight is estimable from its manifest count. *[Open Question 1.]*

This makes **vans the contested resource**: the same fleet must cover pickups throughout the day and line-haul in the afternoon and overnight. Van count and scheduling, not motorbike count, will determine whether bags reach the hub in time and envelopes reach depots before cut-off.

Envelope weight: actual where recorded, default 200 g. At the default a van carries up to 2,500 envelopes on line-haul, so weight rarely binds there.

### 4.2 Shared fleet and daily allocation

The fleet is a **single shared pool** distributed across facilities according to daily requirements.

| Type | Total fleet |
|---|---|
| Motorbikes | [TBD] |
| Vans | [TBD] |

Fleet allocation is a planning decision made before routing. A two-stage approach is recommended: an allocation step assigns motorbikes to facilities based on forecast or known ready-envelope counts per facility at ~25 envelopes per motorbike per day, and splits the vans between pickup and line-haul duty across the day; the VRP solver then routes each facility independently. Relocation between distant facilities has a time cost, so allocation should change at a frequency where that cost is small — likely weekly with daily adjustment for nearby facilities. *[Open Question 2.]*

### 4.3 Van usage

Vans perform four roles: mailbag pickups (throughout the day, exclusively by van), hub → depot line-haul (once envelopes are ready), inter-depot transfers (§5.3.2), and the end-of-day return run. Vans run between depots as well as from the hub, so a line-haul trip may be a multi-leg circuit (e.g. hub → D1 → D3 → hub) carrying hub-origin loads, transfer loads and returns on the same vehicle. A van on pickups is available for line-haul only after it has returned to the hub and unloaded. Because all vans are hub-based, the daily plan must decide how many run pickups and how many are held for line-haul, and at what time pickup vans are released to line-haul. On high-inflow days these two demands collide in the afternoon; the pickup earmark and line-haul departure times must be planned together.

---

## 5. Daily flow

The operation decomposes into five stages. Stages 1, 3, 4 and 5 are routing problems; stage 2 is hub processing, which the solver does not perform but whose output timing it depends on.

### 5.1 Stage 1 — Mailbag pickups (dynamic, throughout the day)

#### 5.1.1 How pickups arise

Customers accumulate envelopes during the day in general mail bags, placing them in as they come. When a bag is ready the customer seals it with a tamper-evident safety device, attaches the manifest, and submits the upload file. Pickup requests therefore arrive continuously and refer to **specific sealed bags**, each with a known envelope count.

The upload file **usually arrives before the mailbag**. This lets the hub start work before the bag is physically present: envelopes can be geocoded and pre-sorted to a target facility, assembly demand can be counted and scheduled in the clean room, and line-haul and next-day allocation can be forecast from confirmed inflow rather than estimates. On arrival, the bag only needs reconciliation against a manifest the hub already holds. Where the file is late or missing, the bag is processed in the slower order (open, key in, then geocode) and its envelopes are flagged as late-ready.

#### 5.1.2 Request lifecycle

| Stage | What happens | System of record |
|---|---|---|
| 1. Request | Customer indicates one or more bags are ready. Request records site, number of bags, envelope count per bag (from manifest), package types. | Call centre / customer portal [TBD] |
| 2. Qualification | Site geocoded; request checked against the day's collection cut-off. | Same |
| 3. Dispatch decision | Solver inserts the stop into an earmarked van's route (§5.1.4). | VRP solver |
| 4. Collection | Driver collects sealed bags, scans bag IDs, confirms seal intact. Service time [TBD] minutes per stop. | Driver app |
| 5. Return to hub | Van returns when bag capacity is reached, when its route ends, or at cut-off. Bags handed to hub processing (§5.2). | WMS |

#### 5.1.3 Earmarked capacity

[TBD] vans are reserved for pickups each day. Earmarking no longer costs motorbike delivery capacity; its cost is **line-haul availability**: a van on pickups cannot depart for a depot until it is back and unloaded. The earmark should be sized from historical bag-request volume per weekday, and should taper during the afternoon so that vans are progressively released to line-haul as depot departure times approach.

#### 5.1.4 Vehicle assignment

All pickups are by van. Assignment is therefore only a question of **which van**, decided by remaining bag capacity, remaining weight capacity and least additional route cost. A site with more bags than any single van can take is split across vans or served by a second visit.

Because manifests give exact envelope counts, the hub also knows the expected envelope inflow per hour, which feeds processing capacity planning (§5.2.5) and the timing of van release to line-haul.

#### 5.1.5 Route building and re-optimisation

The pickup fleet operates as a **dynamic VRP**:

- Earmarked vans start the shift with empty routes, or with standing pickups for regular customers if these exist *[Open Question 3]*.
- New requests are inserted into the route of whichever van can serve them at least additional cost, subject to bag and weight capacity, cut-off, and that van's scheduled release time to line-haul.
- Re-optimisation runs at a fixed cadence (e.g. every 30 minutes); stops already visited are frozen.
- A van returns to the hub when bag capacity is reached, when it could not otherwise get back before cut-off or its line-haul release time, or after [TBD] minutes idle. After unloading it either re-enters the pickup pool (multi-trip) or is released to line-haul.

**Fallback (static batching):** if dynamic insertion is not supported, requests are collected into waves (e.g. 09:00, 12:00, 15:00), each solved as a static CVRP.

#### 5.1.6 Timing and cut-off

- Bags collected and delivered to the hub before the **processing cut-off** [TBD] can be made ready the same day.
- Bags that cannot be back at the hub before the processing cut-off are **not admitted to today's pickup plan** and are scheduled for next-day collection. *(Working rule; confirm — Open Question 12.)*
- Target responsiveness: request to collection within [TBD] hours (service metric, not a solver constraint).

#### 5.1.7 Exceptions

| Situation | Handling |
|---|---|
| Bag not ready / no bag on arrival | Record visit; no bag collected; re-request. |
| Seal broken or missing on collection | Collect but flag; hub performs full reconciliation with customer notified. |
| More bags than expected | Collect what fits; remainder assigned to another van or a second visit. |
| Site closed | Failed pickup; re-request next day. |
| Request cancelled after dispatch | Stop removed at next cycle. |

### 5.2 Stage 2 — Hub processing (not routed, but timed)

Every bag passes through the following steps at the consolidation depot. The solver does not perform them, but it must know **when each envelope will be ready**, since only ready envelopes can be assigned to line-haul or delivery.

#### 5.2.1 Reconciliation

The bag is opened and its contents tallied against the manifest. Discrepancies (missing, extra, or damaged envelopes; broken seal) are reported back to the customer. Envelopes that reconcile move on; envelopes in dispute are held and are not routable until resolved.

#### 5.2.2 Geocoding (where needed)

Envelopes whose upload file carried only a zip-code centroid have their delivery address geocoded. Because the file normally precedes the bag, this step runs **before arrival** in the usual case, so geocoding is off the critical path. Where the file is late or missing (§5.1.1) it runs after reconciliation instead, and is on the critical path. Low-confidence results are held and flagged (§3.2).

#### 5.2.3 Assembly (some package types only)

Some customers supply envelopes that are not finished products. These go through an assembly process in a clean room at the hub before they can be sorted. Assembly is a **capacity-constrained step**: throughput is limited by clean-room staffing and hours, so on high-volume days assembly may be the bottleneck that determines how many envelopes are ready by cut-off. The `package_type` field identifies which envelopes require it, and since the upload file arrives early, the clean room knows its day's workload before the bags do — assembly slots can be scheduled and, where the queue exceeds capacity, prioritised by envelope priority and SLA date.

#### 5.2.4 Sorting

Reconciled (and assembled) envelopes are sorted by target facility using nearest-facility proximity (§3.3). Sorting produces per-facility groups: hub-direct and D1–D6.

#### 5.2.5 Readiness and cut-offs

An envelope is **ready** when it has cleared all applicable steps. The hub must publish, per envelope or per bag, an **expected ready time**, so that:

- line-haul planning knows how many envelopes per depot will be ready by each van's latest departure;
- next-day delivery routing at the hub and at each depot is built on the envelopes that will be there by morning;
- allocation planning can forecast tomorrow's ready volume per facility.

Processing throughput per hour for reconciliation, geocoding, assembly and sorting are needed to compute expected ready times. *[Open Question 4.]* With the upload file in hand before arrival, the expected ready time for each envelope can be computed at request time as: van's expected return to hub + reconciliation time + (assembly queue time if required) + sorting time. Where the file is late or missing (§5.1.1), geocoding cannot have run ahead of the bag and joins the sequence after reconciliation, in §5.1.1's order — open, key in, then geocode: van's expected return to hub + reconciliation time + geocoding time + (assembly queue time if required) + sorting time. Such an envelope cannot be computed at request time, only on arrival. Envelopes not ready by a depot's latest van departure miss that day's line-haul and travel the following day; hub-direct envelopes not ready by end of processing join the following day's hub routes instead of tomorrow's. Assembled envelopes follow the same rule as any other: once ready and sorted they go on today's line-haul if depot-bound, or into tomorrow's hub routes if hub-direct.

#### 5.2.6 Envelope status lifecycle

```
Requested (usually: upload file received, geocoded and pre-sorted — §5.1.1's exception defers both to after Reconciled) → Collected → Received at hub → Reconciled → [Assembled] → Sorted → Ready
   → (Line-haul → At depot) → Dispatched → {Delivered | Rejected | Returned | Postponed}
   → Postponed: back to Ready for next attempt, until SLA date
   → Postponed with facility change, or misassignment found: Transfer requested → In transfer → Ready (at new depot)
   → Transfer requested → Ready (transfer cancelled: address corrected again, or operations override)
   → Transfer requested → Return run (destination cannot be reached before SLA date)
   → Ready → Return run (SLA date passed without delivery)
   → Ready → Return run (cancelled by the customer before dispatch)
   → Dispatched → Return run (cancelled by the customer while on a route, stop not yet visited)
   → Rejected / Returned / Cancelled / SLA expired: Return run → Returned to customer
```

Only envelopes in **Ready** (at the hub or at a depot) are solver inputs for delivery routing.

### 5.3 Stage 3 — Line-haul between facilities

#### 5.3.1 Hub → depot

Sorted, ready depot-bound envelopes are loaded onto vans.

- **Problem type:** Assignment of loads to van trips. In the simple case each van serves one depot per trip; with transfers (§5.3.2) a trip may visit several facilities in sequence.
- **Inputs:** ready envelopes per depot and their ready times, vans available after pickups, depot cut-off times, hub → depot transit times (minutes to several hours).
- **Decision:** which van goes to which depot and when. A van may wait for more envelopes to become ready if it can still reach the depot before its morning release; otherwise it departs with what is ready and later envelopes roll to the next day's line-haul.
- **Constraint:** arrival at the depot before its morning route release, so its envelopes are delivered on day D+1. For distant depots this is an overnight run and the van (and driver) are unavailable until they return. A van that cannot make the release deadline should not depart; its load waits for the next day's line-haul.

#### 5.3.2 Depot → depot (transfers)

An envelope already at a depot may need to move to another depot. Vans run between depots, so this is served by inter-depot legs.

**Triggers**

| Trigger | Source | Spec |
|---|---|---|
| Address correction after a postponement changes the nearest facility | Outcome event with sub-reason "incorrect address", then re-geocode | §6 |
| Zip-centroid pre-sort was wrong: address-level geocode places the envelope nearer another depot, discovered after line-haul | Late geocoding / low-confidence resolution | §3.2, §12 Q14 |
| Operational rebalancing: a depot has more ready envelopes than its allocated motorbikes can deliver before SLA, and a neighbouring depot has slack | Allocation step or operations override | §4.2, §8.3 |

**Rule per trigger.** For the first two, the envelope is transferred if it can reach the correct depot before its SLA date; otherwise it is returned to the customer via the hub. For rebalancing, the choice between moving envelopes and moving motorbikes is a cost comparison made by the allocation step: transfer if a van leg between the two depots already exists or can be added within van-hours, and the moved envelopes arrive before the receiving depot's morning release; otherwise reallocate motorbikes or leave the envelopes unassigned by priority.

**How transfers ride.** A transfer is a load with an origin facility, a destination facility and a deadline (receiving depot's morning release, bounded by SLA date). The line-haul planner builds each van's trip as an ordered sequence of facility legs starting and ending at the hub (or ending at a depot overnight and returning next day), carrying on each leg: hub-origin loads for facilities still ahead on the circuit, transfer loads picked up at earlier depots, and returns bound for the hub. Capacity is 500 kg across all loads on board.

**Problem type.** This generalises §5.3.1 from an assignment into a small pickup-and-delivery VRP over at most seven nodes (hub + six depots) with per-load deadlines. Node count is tiny; the difficulty is timing against release deadlines and van-hours, not combinatorics.

**Timing.** Transfer requests known before the evening line-haul plan are included in that night's circuits. Requests arising after departures wait for the next day's plan. A transfer therefore normally costs one day; via the hub it would cost two.

**Priority.** When van capacity or van-hours are short, hub-origin loads and transfers compete. Both are ranked by the same priority score as delivery (§8.1); a transfer whose envelope is due on the receiving depot's next morning release is a hard inclusion, as an SLA-today delivery is at §6.1. A transfer for an envelope due **today** is not raised at all (§7.1): line-haul runs on D and delivery on D+1, so it could not arrive in time.

### 5.4 Stage 4 — Last mile (Hub and each depot → recipient)

Each facility dispatches its ready envelopes by motorbike.

- **VRP variant:** Capacitated VRP with a route-duration limit, solved independently per facility. Time windows not required by default.
- **Inputs:** ready envelopes present at the facility at morning route release — made ready at the hub the previous day and, for depots, delivered by overnight or early line-haul — plus postponed envelopes held at the facility; address-level coordinates, priority, SLA date; motorbikes allocated; shift window.
- **Outputs:** one route per motorbike; unassigned envelopes with reason.

### 5.5 Stage 5 — Return run (end of day)

Once delivery routes are closed, rejected, defective and SLA-expired envelopes are returned to customers.

- **When:** after delivery routes complete; a distinct late-shift problem.
- **Vehicles:** hub vehicles that have finished delivery, pickup or line-haul routes. *[Open Question 13: if the security rule for mailbags also applies to returns, the return run is van-only.]*
- **VRP variant:** static CVRP from the hub to customer sites, solved each evening.
- **Depot returns:** envelopes rejected at a secondary depot travel back to the hub on the van's return leg and join the following evening's return run.

### 5.6 Daily timeline (indicative)

| Time | Event |
|---|---|
| Morning release | Delivery routes start at hub and all depots, on envelopes made ready and transported the previous day (D−1) plus postponed envelopes held locally |
| Throughout | Bag requests and upload files arrive; envelopes geocoded and pre-sorted on file receipt; pickup vans run and are re-optimised; bags flow into hub processing (reconciliation, assembly, sorting); envelopes become ready continuously |
| Afternoon | Pickup vans progressively released to line-haul; vans to nearer depots depart as their latest-departure times approach |
| [TBD] | Processing cut-off: bags received after this are processed next day |
| [TBD] | Delivery shift ends; outcomes recorded |
| [TBD] | Return run solved and dispatched |
| Evening / overnight | Line-haul to distant depots; remaining processing; tomorrow's allocation and delivery routes solved on the pool that will be at each facility by morning |

---

## 6. Delivery outcomes and their consequences

| Outcome | Definition | Handling | Effect on next day's pool |
|---|---|---|---|
| **Delivered** | Accepted by the recipient. | Closed. | None. |
| **Rejected** | Recipient refuses. | Back to facility, then customer via return run. | Removed. |
| **Returned** | Defective or incomplete; customer must reprocess. | Back to facility, then customer via return run. | Removed; re-enters as a new envelope if resubmitted. |
| **Postponed** | Attempt not completed (recipient unavailable, incorrect address, driver out of time…). | Held at facility in Ready state. | Retried while SLA date not passed. Incorrect address → re-geocode; if the nearest facility changes, a transfer request is raised (§5.3.2) and the envelope enters *In transfer* until it arrives. |
| **Cancelled** | Withdrawn by the customer before delivery. **Not the result of an attempt**: it may arrive at any moment up to delivery, including while the envelope is on a route. | Leaves the pool at once if not yet dispatched; if already on a route, the stop is removed at the next plan refresh provided it has not been visited. Then back to the customer via the return run. Refused once Delivered, which is terminal (§5.2.6). | Removed. |

### 6.1 Attempt limits and SLA

No fixed attempt maximum. An envelope may be retried until its **SLA date**; after that it is returned to the customer via the return run.

The priority score **already incorporates SLA proximity**, so the solver uses priority as its sole ranking signal and applies no second SLA weighting. Two refinements:
- **SLA date = today** is treated as a hard must-deliver-today constraint, subject to feasibility.
- If the solver has native due-date handling, it may be cleaner to feed it the raw SLA date and remove SLA from the priority score. Whichever component handles SLA, only one should. *[Open Question 5.]*

---

## 7. Constraints

### 7.1 Hard constraints (thirteen)

- A motorbike never carries more than 35 envelopes.
- A van never carries more than 500 kg.
- A vehicle's route starts and ends at its home facility within its shift (service time + travel time ≤ shift).
- An envelope is assigned to at most one vehicle per day.
- Only **ready** envelopes are assigned to line-haul or delivery.
- Depot-bound envelopes are dispatched from a depot only after physical arrival; an envelope in transfer is not routable until it arrives at the destination depot.
- A van's combined load across hub-origin, transfer and return envelopes never exceeds 500 kg on any leg.
- A transfer is not raised for an envelope that cannot reach the destination before its SLA date; it goes to the return run instead.
- A van does not depart on line-haul until back from any pickup route and unloaded.
- An envelope is not dispatched for delivery after its SLA date.
- Mailbags are collected by vans only; motorbikes are never assigned pickup stops.
- A van never carries more than [TBD] mailbags.
- Sealed mailbags are collected whole; bags are never split at the customer site.

### 7.2 Soft constraints

- Stop count per route above [TBD].
- Postponed envelopes not retried on the next available day.
- Envelopes approaching SLA date left unassigned.
- Fleet reallocation between facilities on consecutive days.
- Line-haul departing with capacity to spare when more envelopes would be ready shortly.
- Transfers deferred a day when a same-night circuit could have carried them.
- Rebalancing by transfer when reallocating motorbikes would have been cheaper, or vice versa.

### 7.3 Time windows

Deliveries are "any time during the shift". Optional per-envelope windows are retained in the data contract for exceptions.

### 7.4 Service times

- **Delivery: 10 minutes per envelope.** Recipient opens, reviews, asks questions, signs. Multiple envelopes to one recipient on one day is rare and not modelled separately.
- Pickup stop: [TBD] minutes (bag scan and seal check).
- Facility loading/unloading: [TBD] minutes.

**Implication.** 35 envelopes require 350 minutes of service time. With an 8-hour shift, **shift duration, not envelope count, is the binding constraint**; effective capacity is ~20–30 envelopes per motorbike per day. Route duration must be a hard constraint. The 10-minute service time is the largest lever on throughput.

---

## 8. Objectives

Daily volume is **2,500–5,000 envelopes**. On peak days not all ready envelopes will be deliverable; choosing which to leave unassigned is a first-class decision.

1. **Maximise priority-weighted envelopes delivered.**
2. **Minimise unassigned envelopes.**
3. **Minimise total route time / distance.**
4. **Balance workload across vehicles** at the same facility.

### 8.1 Priority format

The solver receives a **numeric score**: weighted-sum objectives are universally supported, fine-grained ranking matters when capacity binds, and SLA-today envelopes are already protected by a hard constraint. If class-like behaviour is wanted ("never drop an Urgent for any number of Standards"), classes are encoded as widely spaced tiers within the score (e.g. Urgent 1,000–1,999, Standard 100–199, Low 1–99), which gives lexicographic behaviour from a plain weighted sum.

**Requirement for the prioritisation algorithm:** one numeric score per envelope; categories, if used, applied as tier offsets.

### 8.2 Operational override

Operations may force an envelope in or out of the plan. The solver must support re-running with **locked** assignments.

The two directions are not symmetric in the solver. *In* is a lock on a vehicle (`locked_vehicle_id`, §9.1). *Out* is **not** a lock: no single lock kind expresses "this envelope, on no vehicle", and encoding it as one forbid-lock per vehicle would mean one per motorbike at the hub and would make a conflicting override impossible to diagnose. An envelope marked `excluded_by_ops` is therefore withheld before the solve and reported in §9.2's unassigned list with the reason *excluded by operations* — visible in the output, and never silently dropped.

### 8.3 Structural capacity gaps

Two capacity checks should be made before the solver is expected to meet SLA targets:

- **Delivery:** total motorbikes × ~25 versus the 2,500–5,000 daily range.
- **Vans:** van-hours available per day versus the sum of pickup route hours and line-haul circuit hours, including inter-depot legs for transfers and transit of several hours to distant depots. This is the most likely operational bottleneck after clean-room assembly; transfers add to it, so their historical volume should be measured (§12 Q15).
- **Processing:** hub throughput per day for reconciliation, geocoding, assembly and sorting versus the same range. If processing, and especially assembly, cannot make the day's inflow ready by cut-off, a backlog forms upstream of the solver and no routing quality will recover it.

---

## 9. Data contract

### 9.1 Solver input

**Envelopes** (only Ready ones are routable; others carried for forecasting)
| Field | Type | Notes |
|---|---|---|
| package_id | string | From customer upload file |
| customer_id | string | From upload file; used for return run grouping |
| recipient_id | string | From upload file |
| package_type | enum | From upload file; determines whether assembly is required |
| mailbag_id | string | Bag the envelope arrived in |
| status | enum | Lifecycle state (§5.2.6) |
| expected_ready_at | datetime | Computed at upload-file receipt, or on arrival where the file was late or missing (§5.1.1); refined as the envelope progresses; actual time once Ready |
| lat, lon | float | Delivery coordinates |
| coord_source | enum | actual / geocoded_address / zip_centroid |
| geocode_confidence | enum | high / medium / low |
| facility_id | string | HUB or D1–D6 from nearest-facility rule |
| priority | number | From prioritisation algorithm; includes SLA proximity; tiers if categorical |
| weight_g | integer | Actual; default 200 |
| sla_date | date | Hard deadline when = today |
| time_window_start / end | datetime, optional | Rare |
| service_time_min | number | Default 10 |
| attempt_number | integer | |
| previous_outcome | enum, optional | Postponed + sub-reason |
| locked_vehicle_id | string, optional | Operations override: force in, onto this vehicle |
| excluded_by_ops | boolean, optional | Operations override: force out. Not offered to the solver; reported unassigned |
| late_ready | boolean, optional | §5.1.1: the bag's file was late or missing, so geocoding was on the critical path for this envelope |

**Mailbags / pickup requests** (arrive incrementally)
| Field | Type | Notes |
|---|---|---|
| mailbag_id | string | |
| customer_id | string | |
| site lat, lon | float | |
| requested_at | datetime | |
| envelope_count | integer | From manifest |
| expected_weight_g | integer | count × actual or default weight |
| assembly_required_count | integer | Envelopes needing clean-room assembly, from package_type |
| pickup_window_start / end | datetime, optional | |
| seal_id | string | Safety device identifier |
| file_received_at | datetime, optional | When the customer's upload file arrived. Null where it never did — §5.1.1's "late **or missing**" |

**Transfer requests**
| Field | Type | Notes |
|---|---|---|
| transfer_id | string | |
| package_id | string | |
| from_facility_id, to_facility_id | string | |
| reason | enum | address_correction / misassignment / rebalancing |
| created_at | datetime | |
| deadline | datetime | min(receiving depot's next morning release, SLA date) |
| weight_g | integer | |
| priority | number | §8.1's score for the envelope, so §5.3.2 can rank a transfer against a hub-origin load |

**Return-run stops** (built at end of day)
| Field | Type | Notes |
|---|---|---|
| customer_id, site lat, lon | | |
| package_ids | list | Rejected / defective / SLA-expired |

**Vehicles**
| Field | Type | Notes |
|---|---|---|
| vehicle_id | string | |
| type | enum | motorbike / van |
| facility_id | string | Facility allocated for the day |
| role | enum | delivery / pickup / linehaul / return; pickup and linehaul are van-only |
| linehaul_release_at | datetime, optional | Time a pickup van must be back at the hub for line-haul duty |
| capacity_envelopes | integer | 35 for motorbikes |
| capacity_mailbags | integer | Vans only |
| capacity_weight_g | integer | 500,000 for vans; 35,000 for motorbikes |
| shift_start / end | datetime | |

**Facilities** — as §3.1, plus `transit_from_hub_min`, `route_release_time` and derived `latest_van_departure`.

### 9.2 Solver output

- **Fleet allocation** (if solved by the tool): vehicle_id, facility_id, role per day.
- **Routes:** vehicle_id; ordered stops (package_id / mailbag_id / customer site) with ETA and stop type; total distance and time.
- **Unassigned envelopes:** package_id; reason (time / count / not ready by cut-off / low geocode confidence / SLA expired / in dispute / excluded by operations).
- **Line-haul plan:** per van, an ordered list of legs (from_facility, to_facility, departure, expected arrival) and per leg the loads on board (hub-origin package_ids by destination, transfer_ids, return package_ids) with total weight; transfers not carried, with reason.

---

## 10. Worked example (illustrative — peak day)

*Fleet and throughput numbers are placeholders.*

- Shared fleet: 120 motorbikes, 10 vans. Motorbike allocation: HUB 48, D1 24, D2 18, D3 14, D4 8, D5 6, D6 2. Vans: 6 on pickups from shift start, tapering to 2 by mid-afternoon as 4 are released to line-haul; 4 held for line-haul from the outset.
- Morning: delivery routes at all facilities run on the 3,100 envelopes made ready and transported yesterday (2,700 new + 400 postponed held locally).
- During the day: 180 bag requests arrive from 70 customer sites, totalling 4,800 envelopes, 900 of which need assembly. The 6 pickup vans collect them over several trips each, carrying up to [TBD] bags per trip.
- Hub processing: upload files arrive ahead of the bags, so the 1,300 zip-only envelopes are geocoded before collection (40 low-confidence, held) and the clean room schedules its 900 assembly jobs by priority; on arrival reconciliation flags 10 discrepancies (held); assembly clears 700 of the 900 by cut-off, and the 200 rolled to tomorrow are the lowest-priority ones. Ready = 4,800 − 200 − 40 − 10 = 4,550. By cut-off 4,550 envelopes are Ready and sorted: HUB 1,600; D1 850; D2 650; D3 550; D4 400; D5 320; D6 180.
- Line-haul: the 4 held vans plus 3 released from pickups depart for D1–D5 through the afternoon and evening, each timed to arrive before its depot's morning release; one late van takes late-ready envelopes to D1 and D2; D6's 180 go on an overnight run. The 4-hour D6 transit ties up one van and driver until the next day. All 4,550 ready envelopes — hub-direct and depot-bound, including the 700 assembled — are positioned for delivery tomorrow.
- Delivery (today's routes, on the morning pool of 3,100 = HUB 1,150; D1 620; D2–D6 1,330): HUB 48 bikes × 25 = 1,200 vs 1,150 → all assigned. D1 24 × 25 = 600 vs 620 → 20 unassigned. D2–D6 48 bikes × 25 = 1,200 vs 1,330 → 130 unassigned across the five. Total unassigned 150; dispatched 2,950.
- End of day: 2,750 delivered, 50 rejected, 30 defective, 120 postponed (= 2,950 dispatched). Return run: 80 envelopes to 30 customer sites, 2 vans. Envelopes rejected at depots ride back to the hub on the return line-haul leg and join the *following* evening's run.
- Tomorrow's delivery pool: 4,550 positioned today + 150 unassigned + 120 postponed = 4,820. This exceeds fleet capacity (120 × 25 = 3,000), so tomorrow's routes will leave ~1,800 envelopes unassigned by priority — see §8.3.

---

## 11. Success metrics

| Metric | Definition | Target |
|---|---|---|
| Pickup responsiveness | Request to bag collection | [TBD] |
| Same-day readiness | Envelopes Ready by cut-off / envelopes received before cut-off | [TBD] |
| Reconciliation discrepancy rate | Disputed envelopes / received | [TBD] |
| First-attempt delivery rate | Delivered on attempt 1 / dispatched | [TBD] |
| Postponement rate | Postponed / dispatched | [TBD] |
| Unassigned rate | Unassigned / Ready pool | [TBD] |
| SLA compliance | Delivered on or before SLA date / total | [TBD] |
| SLA expiry rate | Returned for SLA expiry / total | [TBD] |
| Cancellation rate | Envelopes cancelled / envelopes Ready | [TBD] |
| Distance per envelope | Total km / delivered | [TBD] |
| Envelopes per motorbike per day | Delivered / motorbikes deployed | [TBD, expect 20–30] |
| Solver run time | Wall-clock per run | [TBD] |

---

## 12. Open questions

1. **Mailbag capacity per van:** how many sealed bags fit in a van?
2. **Fleet reallocation:** frequency, relocation time to each depot, and whether rider or bike relocates.
3. **Standing pickups:** do regular customers have fixed daily collection times that can be planned statically?
4. **Hub processing throughput:** envelopes per hour for reconciliation, geocoding, assembly (clean-room) and sorting; clean-room operating hours; share of envelopes requiring assembly.
5. **Solver capabilities to confirm:** dynamic stop insertion; flexible vehicle-to-depot assignment; native due-date handling; locked assignments; ready-time constraints on stops.
6. **Upload file timing:** files usually precede the bag — how often do they arrive late or not at all, and is there a rule for processing bags without a file?
7. **Earmarked pickup capacity:** vans reserved for pickups per weekday, and the rule for releasing them to line-haul during the afternoon.
8. **Per-depot data:** morning route release time, shift windows and hub → depot transit time for D1–D6; whether overnight line-haul drivers are the same pool as daytime van drivers.
9. **Total fleet size.**
10. **Service times:** pickup stop; facility loading/unloading.
11. **Priority tiers:** categories and gaps, if used.
12. **Late requests:** collected same day and processed next day, or scheduled for next-day collection?
13. **Return run:** does the van-only security rule also apply to envelopes returned to customers? Extended shift or separate crew?
14. **Zip-centroid ambiguity:** how to treat zips whose centroid is near-equidistant from two facilities — hold for address geocoding before sorting?
15. **Transfers:** historical volume per day and reason; whether vans and drivers are ever based at depots or all circuits start from the hub; whether inter-depot legs run in daytime as well as overnight; the cost basis for comparing an envelope transfer with a motorbike reallocation.

---

## 13. Service interface (API)

The workflow is exposed as an HTTP API built with FastAPI. The API is a thin layer: it accepts inputs, starts jobs, reports status and returns results. Stage logic lives in the modules of §5 and is never implemented inside request handlers.

### 13.1 Principles

- **Jobs, not synchronous calls.** Any endpoint that invokes the solver returns `202 Accepted` with a job id; callers poll `GET …/{id}` or register a webhook. Jobs run on a dedicated queue with retry and visibility, not in-process background tasks.
- **Schemas are the data contract.** Request and response models are generated from §9 and shared with the internal model; the OpenAPI document is the published form of §9.
- **Lifecycle by events.** Callers append events (collected, reconciled, assembled, sorted, outcome recorded); the §5.2.6 state machine validates each transition. Status is never set directly.
- **Idempotency.** Ingest and event endpoints require an idempotency key so retried calls from driver apps or customer systems cannot duplicate envelopes or outcomes.
- **Constraint visibility.** Every routing result carries its §7.1 violation list (empty on success).
- **Audit.** Overrides (§8.2) and outcome events record the actor and time.

### 13.2 Resources

| Resource | Endpoints | Spec |
|---|---|---|
| Envelopes | `POST /envelopes/batch` (upload-file ingest); `GET /envelopes/{id}`; `POST /envelopes/{id}/events` (outcome, address correction, cancellation) | §5.1.1, §6, §9.1 |
| Pickups | `POST /pickups` (mailbag ready); `POST /pickups/{id}/events` (collected, seal check, failed); `GET /pickups/plan` (current van routes) | §5.1 |
| Processing | `POST /processing/events` (reconciled, discrepancy, assembled, sorted); `GET /processing/ready?facility=&by=` (expected ready counts) | §5.2 |
| Allocation | `POST /allocation/runs`; `GET /allocation/runs/{id}` | §4.2 |
| Line-haul | `POST /linehaul/plans`; `GET /linehaul/plans/{id}`; `POST /linehaul/plans/{id}/events` (leg departed, leg arrived) | §5.3 |
| Transfers | `POST /transfers` (raise, with reason); `GET /transfers?status=`; `POST /transfers/{id}/events` (loaded, arrived, cancelled) | §5.3.2 |
| Delivery routes | `POST /routes/runs` (facility, day); `GET /routes/runs/{id}`; `POST /routes/runs/{id}/locks` (overrides, triggers re-run) | §5.4, §8.2 |
| Returns | `POST /returns/runs`; `GET /returns/runs/{id}` | §5.5 |
| Simulation | `POST /simulation/days`; `GET /simulation/days/{id}` | §5.6, §10 |
| Metrics | `GET /metrics?day=` | §11 |
| Health | `GET /health`; `GET /solver/capabilities` | §12 Q5 |

### 13.3 Scheduled orchestration

Two processes run on a schedule and call the same internal functions as the endpoints; they do not call the API over HTTP:

- **Pickup re-optimisation** every [TBD, default 30] minutes during the collection window, publishing the plan read by `GET /pickups/plan`.
- **Nightly cycle**: allocation → line-haul plan → next-day delivery routes for every facility, on the pool that will be positioned by morning release.

### 13.4 Out of scope for the API

Reconciliation, assembly and sorting are physical hub processes; the API only receives their events. Customer-facing pickup booking and the driver app are separate clients of this API, not part of it.

---

## 14. Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-14 | [TBD] | Initial draft |
| 0.2 | 2026-09-14 | [TBD] | Answers to open questions 1–10; pickups in scope; dual capacity; SLA rule; return flow; overrides |
| 0.3 | 2026-09-14 | [TBD] | 10-minute service time; shift as binding constraint; capacity gap |
| 0.4 | 2026-09-14 | [TBD] | Dynamic pickups; shared fleet; return run; SLA via priority; default weight; priority format discussion |
| 0.5 | 2026-09-14 | [TBD] | Numeric priority score with tier offsets |
| 0.6 | 2026-09-16 | [TBD] | Expanded pickup process |
| 0.16 | 2026-09-23 | [TBD] | §9.1's transfer record gains `priority`, so §5.3.2's ranking rule has the score it asks for; §5.3.2's Priority paragraph reworded — its "SLA-today hard inclusion" named a case §7.1 forbids, because a transfer raised on D arrives on D+1 and an envelope due today cannot be served by it |
| 0.15 | 2026-09-22 | [TBD] | Cancellation: a **Cancelled** outcome in §6, two §5.2.6 edges (`Ready → Return run` and `Dispatched → Return run`), a §2 glossary entry separating the envelope, transfer and pickup-request senses of the word, the §13.2 event, and a §11 cancellation rate |
| 0.14 | 2026-09-19 | [TBD] | §8.2's "out" direction given a representation: `excluded_by_ops` in §9.1 and an "excluded by operations" reason in §9.2, withheld upstream of the solver rather than encoded as locks |
| 0.13 | 2026-09-19 | [TBD] | Corrections from conformance audit: §10 arithmetic closed (10 discrepancies; D2–D6 leave 130 unassigned; 2,750 delivered; tomorrow's pool 4,820); §5.2.6 gains cancel, transfer-to-return and SLA-expiry edges; §5.1.6 late-request rule stated; §7.1 bullet count stated |
| 0.12 | 2026-09-18 | [TBD] | Inter-depot transfers: triggers, rules, multi-leg van circuits, lifecycle state, transfer request entity, constraints, API resource |
| 0.11 | 2026-09-18 | [TBD] | Added §13 service interface: FastAPI resources, job model, event-based lifecycle, scheduled orchestration |
| 0.10 | 2026-09-16 | [TBD] | One-day lag confirmed: pickup, processing, sorting and line-haul on day D, all delivery on D+1; depot cut-off replaced by morning route release and latest van departure; assembled envelopes follow the same routing |
| 0.9 | 2026-09-16 | [TBD] | Upload file precedes bag: pre-geocoding, pre-sorting, assembly scheduling, ready-time computed at request |
| 0.8 | 2026-09-16 | [TBD] | Pickups van-only for security; motorbikes delivery-only; vans identified as the contested pickup/line-haul resource; release-to-line-haul scheduling |
| 0.7 | 2026-09-16 | [TBD] | Mailbags as pickup unit; manifest and upload file; hub processing stage (reconciliation, geocoding, assembly, sorting, readiness); coordinate sources; envelope lifecycle; data contract and example reworked |