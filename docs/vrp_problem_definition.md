# Document Delivery Network — VRP Problem Definition

**Status:** Draft v0.5
**Date:** 14 September 2026
**Owner:** [TBD]
**Audience:** Operations, IT integration team, VRP solution vendor/maintainers

---

## 1. Purpose and scope

This document defines the operational problem that the existing Vehicle Routing Problem (VRP) solution must solve for our document delivery service. It describes the network, the flows, the constraints and the objectives in enough detail for the solver to be configured and for its outputs to be validated against operational reality.

**In scope (decided by the solver, or by a planning step immediately upstream of it):**
- Daily allocation of the shared fleet across the hub and the six secondary depots.
- Routing of customer pickups, which arrive throughout the day, using earmarked hub capacity.
- Assignment of packages to vehicles at the consolidation centre and at each secondary depot.
- Sequencing of stops (routes) for each vehicle on a given day.
- Clustering of distant packages into van loads for line-haul to secondary depots.
- Routing of the end-of-day return-to-customer run.
- Identification of packages that cannot be served with the available fleet on a given day.

**Out of scope (handled by existing systems or people):**
- Geocoding of addresses (done upstream; the solver receives coordinates).
- Package filtering and prioritisation, including SLA proximity (done by the existing prioritisation algorithm; the solver receives a priority value per package).
- SLA negotiation with customers (the solver receives the resulting latest delivery date per package).
- Fleet sizing (total fleet is fixed; only its distribution varies).

---

## 2. Glossary

| Term | Definition |
|---|---|
| **Package** | A single document envelope with one delivery address, weighing 200–1,000 g. The unit of assignment and delivery. |
| **Pickup** | Collection of packages from a customer site, brought to the consolidation centre. Requested throughout the day; performed by earmarked hub vehicles. |
| **Consolidation centre (hub)** | Central facility where all packages arrive, are georeferenced, consolidated and assigned. Also acts as a delivery depot for nearby packages and is the main processing point for new packages. |
| **Secondary depot** | One of six regional facilities. Receives packages by van from the hub and dispatches them by motorbike. |
| **Line-haul** | Van movement from the hub to a secondary depot carrying a clustered load of packages. |
| **Last mile** | Motorbike delivery from the hub or a secondary depot to the end-user. |
| **Service area** | The geographic region served by a given depot, defined implicitly by nearest-depot assignment. |
| **Priority** | A value produced by the existing prioritisation algorithm indicating the relative importance of delivering a package today. Already incorporates SLA proximity. |
| **SLA date** | The latest delivery date for a package, calculated from customer-negotiated delivery windows. After this date the package is returned to the customer. |
| **Outcome** | The result of a delivery attempt: Delivered, Rejected, Returned, Postponed (see §6). |
| **Delivery attempt** | One visit by a driver to a package's delivery address. |
| **Return run** | End-of-day trip returning rejected, defective and SLA-expired packages to customers. |
| **Earmarked capacity** | Vehicles or shift time at the hub reserved for pickups and not available for deliveries. |

---

## 3. Network

### 3.1 Facilities

| ID | Name | Type | Coordinates (lat, lon) | Cut-off time | Transit from hub | Dispatch mode |
|---|---|---|---|---|---|---|
| HUB | Consolidation centre | Hub + delivery depot | [TBD] | [TBD] | — | Same day |
| D1 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |
| D2 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |
| D3 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |
| D4 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |
| D5 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |
| D6 | [TBD] | Secondary depot | [TBD] | [TBD] | [TBD] | [same day / next day] |

The hub is the main processing point: all new packages are processed there first. Whether a given secondary depot can dispatch on the same day depends on whether the van arrives before that depot's cut-off, which in turn depends on transit time. The dispatch mode column should be filled once transit times are known; depots with transit of several hours will in practice operate next-day.

### 3.2 Depot assignment rule

Each package is assigned to exactly one dispatching facility (HUB or D1–D6) using **nearest facility by road distance** from the delivery coordinates. No fixed service-area polygons are used.

- **Hub-direct:** packages whose nearest facility is the hub are delivered directly by motorbike from the hub.
- **Depot-bound:** all other packages are assigned to their nearest secondary depot and transported there by van.

*Note:* nearest-by-road should be preferred over straight-line distance where the road network makes them differ materially. If road distances are not available to the solver, straight-line is an acceptable first approximation to be validated against operations.

### 3.3 Georeferencing

Addresses are geocoded upstream. Each package arrives at the solver with coordinates and a geocoding confidence level. Packages with low confidence [define threshold] should be flagged rather than routed, since an incorrect address is a known cause of postponement.

---

## 4. Fleet

### 4.1 Vehicle types

| Type | Capacity | Capacity unit | Roles |
|---|---|---|---|
| Motorbike | 35 envelopes | Count (each envelope 200–1,000 g, so max 35 kg) | Last-mile delivery; customer pickups; return run |
| Van | 500 kg | Weight | Hub → depot line-haul; customer pickups; return run |

The solver carries both a count and a weight per package. Actual weight is used where recorded; otherwise a **default of 200 g** applies. At the default weight a van carries up to 2,500 envelopes, so in practice van weight capacity will rarely bind; the number of van trips per depot will be driven by depot cut-off times and van availability rather than by weight. Motorbike capacity is effectively bounded by shift time, not by count (§7.4).

### 4.2 Shared fleet and daily allocation

The fleet is a **single shared pool** distributed across facilities according to daily requirements, not a fixed complement per depot.

| Type | Total fleet |
|---|---|
| Motorbikes | [TBD] |
| Vans | [TBD] |

This makes fleet allocation a planning decision that precedes routing. Each day (or at whatever frequency reallocation is practical — see Open Question 1), the plan must decide how many motorbikes go to each facility and how many hub vehicles are earmarked for pickups. Two approaches are possible:

- **Two-stage:** an allocation step assigns vehicles to facilities based on forecast or known package counts per facility (e.g. proportional to priority-weighted demand, at ~25 envelopes per motorbike per day); the VRP solver then routes each facility independently. Simpler and matches how most VRP tools work.
- **Integrated:** the solver treats all packages and all vehicles in one multi-depot problem where a vehicle's home facility is itself a decision variable. More optimal, but requires a solver that supports flexible vehicle-to-depot assignment, and relocation time must be modelled.

The two-stage approach is recommended as the starting point. *[Confirm the solver's capabilities before deciding.]*

**Relocation:** moving a motorbike to a distant depot takes time and the rider is unavailable for deliveries during transit. Allocation changes should therefore be made at a frequency where relocation cost is small relative to the benefit — probably weekly or on a rolling forecast, with daily adjustment only for nearby facilities. *[Open Question 1.]*

### 4.3 Van usage

Vans perform three roles: customer pickups (throughout the day), hub → depot line-haul (after consolidation) and the end-of-day return run. A van used for pickups is available for line-haul only after it has returned to the hub and been unloaded.

---

## 5. Daily flow

The operation decomposes into four linked sub-problems.

### 5.1 Pickups → Hub (dynamic, throughout the day)

Customers call to request pickup at any point during the day. Requests are not all known at the start of the shift, so this is a **dynamic VRP**: routes for the earmarked pickup vehicles are built incrementally as requests arrive.

- **Earmarked capacity:** a fixed number of hub motorbikes and vans [TBD] are reserved for pickups and excluded from the delivery pool. Vehicle choice per request depends on expected volume: large collections require a van.
- **Solver mode:** the solver must support inserting new stops into active routes (re-optimisation on each new request, or at fixed intervals such as every 30 minutes). Pickup vehicles return to the hub when full or at consolidation cut-off.
- **Cut-off:** packages collected after the consolidation cut-off are processed the next day.
- **Fallback:** if the solver in use does not support dynamic insertion, pickups can be batched into fixed waves (e.g. 09:00, 12:00, 15:00), each solved as a static CVRP. This loses some responsiveness but keeps the tooling simple.

### 5.2 Hub → Secondary depots (line-haul)

Depot-bound packages are clustered by destination depot and loaded onto vans.

- **Problem type:** Primarily an assignment problem. Each van serves one depot per trip.
- **Inputs:** packages per depot, vans available after pickups, depot cut-off times, hub → depot transit times (minutes to several hours).
- **Decision:** which van goes to which depot and when, and which depot-bound packages must be held to the next day if a van cannot reach the depot before cut-off.
- **Constraint:** packages must arrive at the depot before its cut-off to be dispatched the same day; otherwise they are dispatched next day.

### 5.3 Last mile (Hub and each depot → end-user)

Each facility dispatches its assigned packages by motorbike.

- **VRP variant:** Capacitated VRP with a route-duration limit, solved independently per facility. Time windows are not required by default.
- **Inputs:** packages at the facility (coordinates, priority, SLA date), motorbikes allocated to the facility, shift window.
- **Outputs:** one route per motorbike; list of unassigned packages with reason.

### 5.4 Return run (end of day)

Once daily delivery routes are closed, rejected, defective and SLA-expired packages are returned to customers.

- **When:** after all delivery routes have completed; a distinct, late-shift problem.
- **Vehicles:** any hub vehicles that have finished their delivery or pickup routes.
- **VRP variant:** static CVRP from the hub to customer sites. Since it runs after routes close, it can be solved as a separate static problem each evening; no interaction with pickup routing is needed.
- **Depot returns:** packages rejected at a secondary depot travel back to the hub on the van's return line-haul leg and enter the return run the following evening.

### 5.5 Daily timeline (indicative)

| Time | Event |
|---|---|
| Shift start | Delivery routes released at hub and depots; earmarked pickup vehicles on standby |
| Throughout | Pickup requests arrive; pickup routes re-optimised as they do |
| [TBD] | Consolidation cut-off: pickups for today's processing returned to hub |
| [TBD] | Georeferencing and prioritisation complete; solver input frozen for tomorrow |
| [TBD] | Line-haul vans depart for depots (same-day depots) |
| [TBD] | Delivery shift ends; outcomes recorded |
| [TBD] | Return run solved and dispatched |
| Overnight | Line-haul to next-day depots; next-day allocation and routing solved |

---

## 6. Delivery outcomes and their consequences

| Outcome | Definition | Package handling | Effect on next day's pool |
|---|---|---|---|
| **Delivered** | Accepted by the end-user. | Closed. | None. |
| **Rejected** | End-user refuses the package. | Back to dispatching facility, then to customer via return run. | Removed from delivery pool. |
| **Returned** | Package is defective or incomplete and must be reprocessed by the customer. | Back to dispatching facility, then to customer via return run. | Removed from delivery pool. Re-enters as a new package once the customer resubmits it. |
| **Postponed** | Attempt not completed: end-user unavailable, incorrect address, driver out of time, etc. | Held at facility. | Re-enters pool for another attempt, provided the SLA date has not passed. Incorrect address → re-geocode before routing (may change facility). |

### 6.1 Attempt limits and SLA

There is no fixed maximum number of attempts. A package may be retried on any day up to and including its **SLA date**. Once the SLA date has passed without delivery, the package is returned to the customer via the return run.

The priority value from the existing algorithm **already incorporates SLA proximity**. The solver should therefore use priority as its sole ranking signal and must not apply a second SLA weighting on top of it, which would double-count. Two exceptions are worth exploring:

- **SLA date as a hard deadline:** a package whose SLA date is today should be treated as must-deliver-today (hard constraint, subject to feasibility) rather than merely high priority. This is a constraint, not a weight, so it does not double-count.
- **Solver-native SLA features:** if the solver has built-in due-date handling, it may be cleaner to feed it the raw SLA date and have the prioritisation algorithm stop including SLA proximity. This is a design choice to evaluate once the solver's capabilities are confirmed *[Open Question 3]*; whichever component handles SLA, only one should.

---

## 7. Constraints

### 7.1 Hard constraints (must never be violated)

- A motorbike never carries more than 35 envelopes.
- A van never carries more than 500 kg.
- A vehicle's route starts and ends at its home facility for the day within its shift window (service time + travel time ≤ shift).
- A package is assigned to at most one vehicle per day.
- Depot-bound packages are only dispatched from a depot after they have physically arrived there.
- A van does not depart on line-haul until it has returned from any pickup route and been unloaded.
- A package is not dispatched for delivery after its SLA date.
- Earmarked pickup vehicles are not assigned delivery stops.

### 7.2 Soft constraints (penalised, not forbidden)

- Stop count per route above [TBD].
- Postponed packages not retried on the next available day.
- Packages approaching SLA date left unassigned.
- Fleet reallocation between facilities on consecutive days (relocation cost).

### 7.3 Time windows

Deliveries are "any time during the shift"; per-package time windows are not required by default. The data contract retains an optional time-window field for exceptional cases.

### 7.4 Service times

- **Time per envelope delivered: 10 minutes.** The recipient must open the envelope, review the contents, ask questions and sign a receipt. Multiple envelopes to the same recipient on the same day are a rare exception and are not modelled separately; each envelope takes 10 minutes.
- Time per pickup stop: [TBD] minutes.
- Loading time at facility: [TBD] minutes.

**Implication for capacity.** A full load of 35 envelopes requires 350 minutes of service time before any travel. With an 8-hour shift that leaves roughly 2 hours for riding, which is unlikely to be enough for 35 dispersed stops. The **shift duration, not the 35-envelope limit, is the binding constraint**; effective capacity will be closer to 20–30 envelopes per motorbike per day depending on stop density. The solver must enforce route duration as a hard constraint.

The 10-minute service time is the single largest lever on throughput: shortening it (pre-notifying recipients, digital receipts) has more effect on daily capacity than fleet changes.

---

## 8. Objectives

Daily volume ranges from **2,500 to 5,000 packages**. On peak days not all packages will be deliverable, so the solver must treat "which packages to leave unassigned" as a first-class decision.

Proposed objective hierarchy:

1. **Maximise priority-weighted packages delivered.**
2. **Minimise number of unassigned packages.**
3. **Minimise total route time / distance.**
4. **Balance workload across vehicles** at the same facility.

### 8.1 Priority format

The prioritisation algorithm can output either a numeric score or a categorical class. **The solver should receive a numeric score**, for three reasons:

- A weighted-sum objective (each delivered package earns its score) is supported by virtually every VRP tool; strict lexicographic objectives over classes often are not.
- With shift time binding and peak days exceeding capacity, the solver is constantly choosing which packages to drop; a numeric score lets it rank within what would otherwise be a tie.
- Truly urgent packages (SLA date = today) are already protected by a hard constraint (§6.1), so the weighted sum cannot sacrifice them.

If class-like behaviour is still wanted ("never drop an Urgent package for any number of Standard ones"), encode classes as widely spaced tiers within the numeric score, e.g. Urgent 1,000–1,999, Standard 100–199, Low 1–99. One Urgent package then outweighs ten Standard ones, while the solver still ranks within each tier. This yields lexicographic behaviour from a plain weighted sum and avoids depending on solver support for tiered objectives.

**Requirement for the prioritisation algorithm:** output a single numeric score per package; if categories are used, apply them as tier offsets within that score.

### 8.2 Operational override

Unassigned packages are selected by priority first. Operations may then override (force a package in or pull one out). The solver must support re-running with a set of **locked** assignments so that overrides are respected while the remainder is re-optimised.

### 8.3 Structural capacity gap

At ~25 effective envelopes per motorbike per day, total daily capacity is roughly (total motorbikes minus earmarked pickup bikes) × 25. If this is below typical daily volume, the solver cannot prevent a growing backlog; it can only choose which packages wait. This figure should be checked against the 2,500–5,000 range before the solver is expected to meet SLA targets. Because the fleet is shared, the check applies to the fleet as a whole rather than per depot.

---

## 9. Data contract

### 9.1 Solver input

**Packages**
| Field | Type | Notes |
|---|---|---|
| package_id | string | Unique |
| lat, lon | float | From geocoder |
| geocode_confidence | enum | high / medium / low |
| facility_id | string | HUB or D1–D6, from nearest-facility rule |
| priority | number | From prioritisation algorithm; includes SLA proximity; classes encoded as tiers (§8.1) |
| weight_g | integer | Actual weight; default 200 if unknown |
| sla_date | date | Latest delivery date; used as hard deadline when = today |
| time_window_start / end | datetime, optional | Rarely used |
| service_time_min | number | Default 10 |
| attempt_number | integer | 1 for new packages |
| previous_outcome | enum, optional | Postponed + sub-reason |
| locked_vehicle_id | string, optional | Set by operations override |

**Pickup requests** (arrive incrementally during the day)
| Field | Type | Notes |
|---|---|---|
| pickup_id | string | |
| requested_at | datetime | Time the request was received |
| lat, lon | float | Customer site |
| expected_count | integer | |
| expected_weight_g | integer | |
| pickup_window_start / end | datetime, optional | |

**Return-run stops** (built at end of day)
| Field | Type | Notes |
|---|---|---|
| customer_site_id | string | |
| lat, lon | float | |
| package_ids | list | Rejected / defective / SLA-expired packages for this customer |

**Vehicles**
| Field | Type | Notes |
|---|---|---|
| vehicle_id | string | |
| type | enum | motorbike / van |
| facility_id | string | Facility allocated for the day |
| role | enum | delivery / pickup / linehaul / return |
| capacity_count | integer | 35 for motorbikes; null for vans |
| capacity_weight_g | integer | 500,000 for vans; 35,000 for motorbikes |
| shift_start / end | datetime | |

**Facilities** — as in §3.1, plus `transit_from_hub_min` and `dispatch_mode`.

### 9.2 Solver output

**Fleet allocation** (if solved by the tool rather than upstream)
| Field | Notes |
|---|---|
| vehicle_id, facility_id, role | Per day |

**Routes**
| Field | Notes |
|---|---|
| vehicle_id | |
| stops | Ordered list of stop (package_id / pickup_id / customer_site_id) with ETA and stop type |
| total_distance, total_time | |

**Unassigned packages**
| Field | Notes |
|---|---|
| package_id | |
| reason | time / count / cut-off missed / low geocode confidence / SLA expired |

**Line-haul plan**
| Field | Notes |
|---|---|
| van_id, destination facility, package_ids, departure time, expected arrival | |

---

## 10. Worked example (illustrative — peak day)

*Fleet numbers are placeholders.*

- Shared fleet: 120 motorbikes, 10 vans. Allocation for the day, based on forecast demand: HUB 48 (of which 8 earmarked for pickups), D1 24, D2 18, D3 14, D4 8, D5 6, D6 2.
- 5,000 packages in the pool: 4,600 new plus 400 postponed. Nearest-facility split: HUB 1,800; D1 900; D2 700; D3 600; D4 450; D5 350; D6 200.
- Hub delivery: 40 bikes × ~25 = 1,000 effective capacity → 800 hub-direct packages unassigned (reason: time), lowest priority first, all SLA-today packages included.
- Pickups: 8 bikes and 3 vans handle ~60 pickup requests arriving through the day; routes re-optimised every 30 minutes.
- Line-haul: 3,200 depot-bound packages at 200 g average ≈ 640 kg → weight is not binding; 7 vans make one trip each. D6 is 4 hours away and operates next-day; D1–D5 receive before cut-off.
- D1: 24 bikes × 25 = 600 for 900 packages → 300 unassigned, rolled to tomorrow.
- End of day: 3,400 delivered, 60 rejected, 40 defective, 250 postponed. Return run: 100 packages to 35 customer sites, solved as a static CVRP with 4 hub bikes and 1 van. Tomorrow's pool: 250 postponed + 1,100 unassigned + D6's 200 + new arrivals.
- Tomorrow's allocation shifts 4 bikes from D4/D5 (which cleared their pools) to D1.

---

## 11. Success metrics

| Metric | Definition | Target |
|---|---|---|
| First-attempt delivery rate | Delivered on attempt 1 / total dispatched | [TBD] |
| Postponement rate | Postponed / dispatched | [TBD] |
| Unassigned rate | Unassigned / packages in pool | [TBD] |
| SLA compliance | Delivered on or before SLA date / total | [TBD] |
| SLA expiry rate | Returned to customer for SLA expiry / total | [TBD] |
| Pickup responsiveness | Time from request to collection | [TBD] |
| Distance per package | Total km / packages delivered | [TBD] |
| Envelopes per motorbike per day | Delivered / motorbikes deployed | [TBD, expect 20–30] |
| Solver run time | Wall-clock time per run | [TBD] |

---

## 12. Open questions

Resolved in v0.4–0.5: numeric priority score with optional tiers; pickup model (dynamic, earmarked capacity); SLA already in priority; return run as end-of-day problem; hub as main processing point; shared fleet; default weight 200 g; no reduction for multiple envelopes.

Remaining:

1. **Fleet reallocation:** how often can vehicles be moved between facilities (daily / weekly), and how long does relocation take to each depot? Is it the rider who relocates, or is the bike transported?
2. **Priority tiers:** if categories are to be encoded as tiers within the numeric score, what are the categories and the desired tier gaps?
3. **Solver capabilities to confirm:** dynamic stop insertion for pickups; flexible vehicle-to-depot assignment; native due-date handling; locked assignments for overrides.
4. **Earmarked pickup capacity:** how many hub motorbikes and vans should be reserved for pickups on a typical day? Does this vary by weekday?
5. **Per-depot data:** shift windows, cut-off times, hub → depot transit time, and same-day vs next-day dispatch mode for D1–D6.
6. **Total fleet size:** motorbikes and vans.
7. **Service times:** per pickup stop and facility loading/unloading.
8. **Return run staffing:** do return-run vehicles work an extended shift, or is the return run a separate crew?

---

## 13. Revision history

| Version | Date | Author | Change |
|---|---|---|---|
| 0.1 | 2026-09-14 | [TBD] | Initial draft |
| 0.2 | 2026-09-14 | [TBD] | Incorporated answers to open questions 1–10; pickups in scope, dual capacity model, SLA rule, return-to-customer flow, override mechanism |
| 0.3 | 2026-09-14 | [TBD] | 10-minute per-envelope service time; shift duration as binding constraint; capacity gap analysis |
| 0.5 | 2026-09-14 | [TBD] | Priority format decided: numeric score, classes as tier offsets |
| 0.4 | 2026-09-14 | [TBD] | Dynamic pickups with earmarked capacity; shared fleet with daily allocation; end-of-day return run; SLA handled by priority only; default weight 200 g; priority score vs classes explained |
