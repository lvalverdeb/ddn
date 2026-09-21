# Claude Code task prompts — VRP integration

Run in order. Start each session with `/effort ultracode` for tasks 1–6; drop to `/effort high` for follow-up fixes. Each prompt assumes `CLAUDE.md` and `docs/vrp-problem-definition.md` are in the repo.

Before task 1, run once:

> Read CLAUDE.md and docs/vrp-problem-definition.md in full. Do not write code. Propose a module layout matching the architecture in CLAUDE.md, a build order, and a list of every `[TBD]` in the spec with the placeholder value you would put in docs/assumptions.md. Stop and wait for my review.

---

## Task 1 — Data model, lifecycle and fixtures

> Implement `model/` from §9.1 and §5.2.6 of the spec.
>
> - Entities: Envelope, Mailbag, PickupRequest, ReturnStop, Vehicle, Facility, with every field in §9.1 and nothing else. `coord_source` and `status` are enums with exactly the values listed.
> - Lifecycle: a state machine for Envelope with only the transitions in §5.2.6. Illegal transitions raise.
> - Derived values: `Facility.latest_van_departure` = route release − transit − unload (§3.1); `Envelope.is_ready`; `Envelope.must_deliver_today` (SLA date = today, §6.1).
> - Validation: default weight 200 g when absent (§4.1); priority is numeric (§8.1).
> - Fixture: encode the peak day in §10 as `tests/fixtures/peak_day` — 120 motorbikes, 10 vans, the facility split, 180 bag requests over 70 sites, 4,800 envelopes of which 900 need assembly, 1,300 zip-only. Use deterministic synthetic coordinates around placeholder facility locations from docs/assumptions.md.
> - Tests for every transition, every derived value, and fixture loading.
>
> Do not touch any other module.

## Task 2 — Solver adapter with constraint post-checks

> Implement `solver_adapter/` against the solver named in CLAUDE.md.
>
> - `to_solver(problem)` translates a per-facility last-mile problem (§5.4) and a pickup problem (§5.1) into the solver's input format. Service time is 10 min per envelope (§7.4); route duration ≤ shift is a hard limit; capacity is 35 envelopes for motorbikes, mailbag count and 500 kg for vans.
> - `from_solver(result)` produces the §9.2 output: routes with ETAs and stop types, unassigned envelopes with a reason from the §9.2 list, line-haul plan.
> - `check_hard_constraints(routes, problem)` tests every bullet in §7.1 and returns a list of violations. Any violation fails the run.
> - Capability probe: for each feature in §12 Q5 (dynamic insertion, flexible vehicle-to-depot assignment, native due-date handling, locked assignments, ready-time constraints), detect whether the solver supports it and write the result to docs/solver-capabilities.md. Where unsupported, implement the fallback the spec names.
> - Tests: a tiny 1-facility, 2-vehicle, 6-envelope problem round-trips; each §7.1 constraint has a test that deliberately violates it and expects a violation.

## Task 3 — Pickups, processing readiness and line-haul

> Implement `pickups/`, `processing/` and `linehaul/` (§5.1–§5.3).
>
> - Pickups: van-only dynamic routing with re-optimisation on a fixed cadence (default 30 min, from assumptions). Visited stops are frozen. Each van has a `linehaul_release_at`; insertion must respect it. Implement static wave batching as the fallback if docs/solver-capabilities.md says dynamic insertion is unsupported. Handle every exception in §5.1.7.
> - Processing: compute `expected_ready_at` per envelope at upload-file receipt as van return + reconciliation + assembly queue (if package_type requires) + sorting, using throughput values from assumptions (§5.2.5). Pre-sort to facility by nearest-facility rule on file receipt (§3.2–3.3); flag zip-only envelopes whose centroid is near-equidistant between two facilities (§12 Q14).
> - Line-haul: assign ready depot-bound envelopes and vans to depot trips so each arrives before the depot's morning release (§5.3). A van that cannot make the deadline does not depart. Vans released from pickups become available at their release time. Same-day line-haul, next-day delivery (§3.1).
> - Tests on the peak-day fixture: all 4,550 ready envelopes are either positioned at a facility by morning or explicitly rolled with a reason; no van departs late; pickup vans never receive delivery stops.

## Task 4 — Allocation, last mile and returns

> Implement `allocation/`, `lastmile/` and `returns/` (§4.2, §5.4, §5.5).
>
> - Allocation: two-stage. Given forecast ready envelopes per facility, assign motorbikes at ~25 envelopes per bike per day (from assumptions) and split vans between pickup duty (with taper schedule) and line-haul. Penalise reallocation between consecutive days (§7.2). Output the §9.2 fleet allocation record.
> - Last mile: per-facility CVRP with route-duration limit via the adapter. Input pool = envelopes positioned at the facility by morning release + postponed envelopes held there. Selection when capacity is short: priority-weighted (§8), with SLA-today envelopes forced in (§6.1) and `locked_vehicle_id` honoured (§8.2). Unassigned envelopes get a §9.2 reason.
> - Returns: static CVRP from the hub grouping rejected / defective / SLA-expired envelopes by customer site (§5.5). Vehicle type per docs/assumptions.md pending §12 Q13.
> - Tests: on the peak-day fixture, D1 leaves exactly the 20 lowest-priority non-SLA-today envelopes unassigned; a locked envelope is never unassigned; no route exceeds shift; return run covers every rejected/defective/expired envelope exactly once.

## Task 5 — Day simulator and metrics

> Implement `simulation/` (§5.6, §10, §11).
>
> - `run_day(state, day)` executes the full D / D+1 cycle: morning delivery routes on the positioned pool → pickups and processing through the day → line-haul → outcomes sampled with configurable rates → return run → carry-over into the next day's pool. Outcome sampling and postponement sub-reasons follow §6.
> - `run_days(n)` chains days so backlog growth is visible.
> - Metrics: every row of §11, computed per day and cumulative.
> - Capacity checks from §8.3: report delivery capacity vs pool, van-hours vs demand, processing throughput vs inflow, and flag whichever binds first.
> - Acceptance test: the peak-day fixture reproduces §10 (v0.13) within tolerance — ~2,750 delivered, 20 unassigned at D1 and 130 across D2–D6, 4,550 positioned for tomorrow, and a next-day pool of 4,820 that exceeds fleet capacity by roughly 1,800. Print a one-page day report.
>
> When done, list every place where a spec `[TBD]` materially changed the result, so we know which numbers to chase first.

## Task 6 — FastAPI service layer

> Implement `api/` per §13 of the spec, on top of the existing modules. Do not move logic into handlers.
>
> - Pydantic models generated from or shared with `model/` (§9); the OpenAPI document must list every §9.1 field with the same names and enums.
> - Every resource and endpoint in the §13.2 table. Solver-invoking endpoints return `202` with a job id; implement jobs on [queue library from CLAUDE.md] with a status endpoint per resource. No `BackgroundTasks`.
> - Event endpoints validate transitions through the §5.2.6 state machine and reject illegal ones with `409` and the current state.
> - Idempotency-Key header required on `POST /envelopes/batch`, `POST /pickups` and all `/events` endpoints; a replay returns the original response.
> - `GET /routes/runs/{id}` includes the §7.1 violation list from `solver_adapter.check_hard_constraints`.
> - Locks endpoint stores `locked_vehicle_id` and enqueues a re-run (§8.2).
> - Two scheduled workers per §13.3 (pickup re-optimisation, nightly cycle) calling internal functions directly.
> - Audit table recording actor and timestamp on locks and outcome events.
> - Tests: contract tests that every §13.2 endpoint exists with the documented status codes; an idempotency replay test; an illegal-transition test; an end-to-end test that drives the §10 peak day through the API (ingest → pickups → processing events → nightly cycle → next-day routes → outcomes → return run) and matches Task 5's numbers.
>
> Finish by pasting the full test runner output and the generated OpenAPI path list.

---

# Gap-fill tasks (v0.12 — inter-depot transfers)

The build from Tasks 1–6 exists. These tasks extend it; they must not rewrite it. Rules for every task below: keep all existing tests green, add rather than replace modules, and where an existing function's signature must change, keep the old one as a thin wrapper until the last task removes it. Start each with `/effort ultracode`.

Before Task 7, run once:

> Read §5.3.2, §6, §5.2.6, §7, §9 and §13 of docs/vrp-problem-definition.md (v0.12). Compare against the current code in `model/`, `linehaul/`, `lastmile/`, `simulation/` and `api/`. Do not write code. List (a) every place the existing implementation contradicts v0.12, (b) every place it is silent, and (c) the minimum set of changes to close the gaps without restructuring. Stop and wait for my review.

## Task 7 — Transfer entity and lifecycle

> Extend `model/` for §5.3.2 without changing existing entities' fields.
>
> - New entity `TransferRequest` with every field in §9.1 (transfer_id, package_id, from/to facility, reason enum, created_at, deadline, weight_g). `deadline` is computed as min(receiving depot's next morning release, SLA date).
> - Extend the Envelope state machine with the two transitions in §5.2.6: `Ready → TransferRequested → InTransfer → Ready(at new depot)`. `InTransfer` envelopes are not routable (`is_ready` is false). Existing transitions unchanged; existing transition tests must pass untouched.
> - Raising rule: a function `maybe_raise_transfer(envelope, new_facility, reason, today)` returns a TransferRequest if the envelope can reach `new_facility` before its SLA date, else marks it for the return run (§7.1). Reasons: address_correction, misassignment, rebalancing.
> - Hook: when an outcome event is Postponed with sub-reason incorrect_address and re-geocoding changes the nearest facility, call the raising rule (§6). This is an addition to the existing outcome handler, not a rewrite.
> - Fixture: add `tests/fixtures/peak_day_transfers` — 30 transfer requests layered on the existing peak day (18 address corrections, 9 misassignments, 3 rebalancing), with at least 2 that cannot meet SLA and must go to returns.
> - Tests for each transition, the raising rule in both outcomes, and fixture loading.

## Task 8 — Multi-leg line-haul with transfers

> Generalise `linehaul/` from single-destination trips to facility circuits per §5.3.2, preserving the existing behaviour when there are no transfers.
>
> - Represent a van trip as an ordered list of legs (from, to, depart, arrive) starting at the hub and ending at the hub or at a depot overnight; per leg, the loads on board: hub-origin envelopes by destination, transfers, returns. Weight ≤ 500 kg on every leg (§7.1).
> - Inputs add the open TransferRequests known at planning time and returns awaiting collection at each depot.
> - Solve as a small pickup-and-delivery problem over hub + six depots with per-load deadlines (each depot's morning release for hub-origin and transfer loads; none for returns). Use the existing solver via `solver_adapter` if it supports pickup-and-delivery with time constraints (check docs/solver-capabilities.md); otherwise implement a direct enumeration/insertion heuristic — seven nodes makes this tractable — and say so.
> - Ranking under shortage: hub-origin loads and transfers ranked together by priority score; SLA-today transfers are hard inclusions (§5.3.2). Transfers not carried are output with a reason (§9.2).
> - Backwards compatibility: with an empty transfer list and no returns, the plan must be leg-for-leg identical to the current implementation's output on the existing peak-day fixture. Add a regression test that asserts this before you change anything else.
> - Extend `check_hard_constraints` for the three new §7.1 bullets.
> - Tests on `peak_day_transfers`: 28 transfers carried and arriving before their deadline, 2 routed to returns, no leg over 500 kg, every circuit starts at the hub, and van-hours reported.

## Task 9 — Allocation rebalancing, API, simulation and metrics

> Close the remaining gaps across `allocation/`, `api/`, `simulation/` and `tests/`.
>
> - Allocation (§5.3.2 rebalancing rule): when a depot's ready pool exceeds its motorbike capacity before SLA and a neighbouring depot has slack, compare (a) raising rebalancing transfers that fit an existing or added van leg within van-hours and arrive before morning release, against (b) reallocating motorbikes, using the cost basis in docs/assumptions.md (placeholder until §12 Q15 is answered). Emit either TransferRequests or a changed allocation, never both for the same envelopes. Existing allocation output unchanged when no depot is over capacity.
> - API (§13.2): add the Transfers resource (`POST /transfers`, `GET /transfers?status=`, `POST /transfers/{id}/events`) and change line-haul events from trip-level to leg-level (`leg departed`, `leg arrived`) while keeping the old event names accepted for one release with a deprecation warning. Extend `GET /linehaul/plans/{id}` to return legs and per-leg loads (§9.2). Idempotency and audit rules apply.
> - Simulation: carry transfers through the day cycle — raise from postponement sub-reasons at the configured rate, include in the nightly line-haul plan, arrive next morning, become routable at the new depot. Add metrics: transfers raised / carried / deferred / sent to returns, and transfer van-hours as a share of total van-hours.
> - Regression: the original peak-day fixture must produce exactly the same numbers as before this task set. `peak_day_transfers` must show 28 envelopes delivered from their new depot on D+2 and 2 in the return run.
> - Remove any wrapper shims introduced in Tasks 7–8 and update CLAUDE.md's architecture list to mention transfers under `linehaul/`.
>
> Finish by pasting the full test runner output, the OpenAPI path list, and a diff summary of every file touched across Tasks 7–9.

---

# Remediation tasks (from conformance audit 2026-09-19)

Input: `docs/audit/conformance-2026-09-19.md` and spec v0.13, which corrects §10, §5.2.6, §5.1.6 and §7.1 in response to it. Run in a session that did not produce the audit. Same rules as the gap-fill tasks: extend, don't rewrite; existing tests stay green except where the audit shows a test was passing on noise — those are rewritten, and the commit message names the audit row. Start with `/effort ultracode`. Do not touch anything the audit did not flag.

## Task 10 — Wire the objective (audit gaps 1, 2, 8)

> §8 is currently inert: vehicles reach the solver with zero costs.
>
> - Make the `_vehicle` builders in `ddn/contract.py`, `ddn/solver_adapter/problem.py` and `ddn/returns/run.py` carry `fixed_cost`, `cost_per_metre`, `cost_per_second` from a single source. Delete `runner._priced()` and the `run.objective` blocks in `models/*.json` unless one of them becomes that source; do not leave two.
> - Move `PRIZE_SCALE` and `BANDS` out of `ddn/contract.py` into `ddn/assumptions.py` with rows in `docs/assumptions.md` labelled invented, and fix the Open Question citation (tiers are Q11). Change the band floors to match §8.1's illustration (Low starts at 1).
> - Rewrite `tests/test_assumptions.py:86-95` to assert the property (no numeric literal outside `ddn/assumptions.py` and `tests/` equals a registered placeholder) rather than grep variable names in one module. It must catch `SERVICE_SECONDS = 600` in `returns/run.py` and the model-JSON fleet sizes.
> - Add a test that a problem built from the shipped model reaches the solver with non-zero `cost_per_metre`, and a second that, given two feasible routings of equal priority, the shorter wins.
> - Implement §8.2's force-out using `FORBID_ORDER_ON_VEHICLE` / `FORBID_DEPLOY`; extend the locks endpoint and its contract test.
> - Objective 4 (workload balance): implement as a soft term only after the above passes, or record in `docs/assumptions.md` that it is deferred and why.

## Task 11 — Run the day-spanning checks and carry returns (gaps 3, 7)

> - `ddn/api/runner.py`: `linehaul_plan`, `allocation_plan` and `simulate` must compute their violation list by calling `check_day_constraints` (assembling a `Day` as `ddn/simulation/day.py` already does), never return `[]` as a literal. Add a contract test per endpoint that submits a plan with a known §7.1 breach and expects a non-empty list.
> - Pass `stage=PICKUP` from the pickup planning path so the three mailbag bullets are enforced in production, not only in tests.
> - Populate `Leg.return_ids` in `ddn/linehaul/circuit.py` and `plan.py` from envelopes at each depot in Rejected / Returned / SLA-expired state; include their weight in the 500 kg leg check; emit them in the §9.2 line-haul plan.
> - `ddn/simulation/day.py:225,367`: stop stamping `facility_id=hub_id` at outcome time. A depot rejection stays at the depot, rides the return leg, and joins the following evening's return run (§5.5, §10 v0.13). Test: a rejection at D3 on day D appears in the return run of day D+1, never D.

## Task 12 — Pin values and repair noise-passing tests (gaps 4, 5, 6, 9)

> - Assert `MAX_LOAD_G == 500_000` literally, as `test_returns.py:206` does for 35. Rewrite the two `test_transfers.py` cases so the violating input is `500_001`, not `MAX_LOAD_G + 1`.
> - `tests/test_solver_adapter.py`: every test for a bullet that shares a string with a `vrp.verify` invariant (`ROUTE_WITHIN_SHIFT` ↔ INV-4/INV-6, `ONE_VEHICLE_PER_DAY` ↔ INV-1) must assert on the violation *detail* text and use routes whose arrival times are consistent with the test travel matrix, so INV-4 does not fire. Re-run mutations M6 and M6b (`postcheck.py:246`, `:250`) and confirm each now fails at least one test; record the runs in the commit message.
> - `ddn/processing/readiness.py:108-109`: order the assembly queue by (priority desc, sla_date asc, arrival) per §5.2.3. Test on the peak-day fixture that the 200 rolled are the lowest-priority assembly envelopes.
> - Correct the prose counts in `postcheck.py:7,43`, `solver_adapter/__init__.py:4` and `check_day_constraints`' docstring to thirteen and five; remove the reference to `tests/test_postcheck.py` or create it. Replace `test_nothing_is_left_unenforced` with a test that parses §7.1's bullet count from the spec file and compares it with `len(BULLETS)`.
> - `tests/test_model.py:40-44`: add an anti-vacuity guard asserting `TRANSITIONS` has at least the number of edges §5.2.6 v0.13 draws.

## Task 13 — Registry, versions, fixture and spec alignment (gap 10, §12 table, §5, §6)

> - Wire `RETURN_STOP_MIN` into `ddn/returns/run.py` and delete `SERVICE_SECONDS = 600`; replace `POOL, BIKES = 4550, 120` and the literal `25` in `ddn/simulation/on_road.py` with registry reads; read `ROUTE_RELEASE` in `simulation/day.py:252,263` instead of `7 * HOUR`.
> - Remove the model-JSON second copies of shift windows and fleet sizes, or generate the JSONs from `ddn/assumptions.py` at build time.
> - Register the late-request rule (`admission.py:106`, now §5.1.6 v0.13), `EQUIDISTANT_MARGIN_M`, and the outcome and sub-reason per-mille rates in `docs/assumptions.md` with provenance; delete `PICKUP_RESPONSE_TARGET_H` (a §11 target with no consumer).
> - Fixture: fix `peak_day.py:410` so the morning pool carries exactly 400 postponed; set `DISPUTED = 10` to match §10 v0.13 and delete `SPEC_DISPUTED`/`SPEC_SHORTFALL`; wire `LINEHAUL_VANS`, `MORNING_NEW`, `ASSEMBLY_CLEARED` into builders or assertions so no fixture constant is dead; model the 4+3 van departure split, the late van and D6's overnight run; replace `POSITIONED_ON_ROADS = 4140` with a derivation from the spec or a documented road-network tolerance. Create `tests/fixtures/peak_day_transfers.py` per Task 7 and move `test_transfers.py` onto it.
> - Update every acceptance number touched by §10 v0.13: 2,750 delivered, 150 unassigned (20 D1 + 130 D2–D6), tomorrow's pool 4,820.
> - Add the three §5.2.6 v0.13 edges to `ddn/model/lifecycle.py` if absent and remove the docstring "readings" now that the spec states them. Propose spec v0.14 text adding `Facility.unload_min`, `Transfer.actor`, the inter-depot transit input, the `Violation` and `Problem` schemas and the pickup `cancelled` event to §9 / §13, since they are correct and should be described; do not edit the spec yourself — write the proposed diff to `docs/spec-proposals/v0.14.md`. Delete `UNREACHABLE_ADDRESS` or emit it from `reachable_subset` so pruned envelopes appear in the unassigned list with a reason, and include that in the v0.14 proposal.
> - Bump `CLAUDE.md`, `README.md`, `ddn/api/app.py` version, `docs/capacity-finding.md` and `docs/solver-capabilities.md` to the current spec version; reorder §14 chronologically in the proposal.
>
> Finish by re-running the audit's nine mutations and pasting a table of results, then the full runner output.

---

# Slice tasks (mini end-to-ends, docs/e2e/*.md)

Prerequisite: Tasks 10–13 complete and re-audited. Rules: slices orchestrate existing modules; `e2e/*/run.py` calls, never computes; new logic lands in the owning module with tests there; every acceptance test is named by its row ID and cites the slice document. Start with `/effort ultracode`.

## Task 14 — Harness scaffold and chaining test

> Create `ddn/e2e/` with one package per slice (`pickups_to_hub`, `hub_to_depots`, `depot_delivery`), each containing `scenario.py`, `run.py`, `handoff.py`. Read docs/e2e/e2e-1..3 for the inputs/outputs tables; the hand-off models are exactly those tables, as Pydantic models reused by `ddn/api/schemas.py` (no duplicates).
>
> - Hand-offs: `ReadyPool` (E2E-1 → E2E-2), `PositionedPool` + `ReturnLoads` + `TransferOutcomes` (E2E-2 → E2E-3), `DayOutcomes` + `ReturnLoad` + `TransferRequests` (E2E-3 → E2E-2 next day). Each serialises to JSON under `tests/e2e/handoffs/` so a slice can be run from a file.
> - `run.py` in each package: a single function composing the existing module calls in the order the slice document's §1 describes. Assert by inspection (and a test using AST) that `run.py` imports nothing from `ddn/solver_adapter/problem.py`-level internals and contains no loop over envelopes that filters or ranks — that belongs in modules.
> - Chaining test `tests/e2e/test_chain.py`: run 1 → 2 → 3 on the §10 v0.13 peak day, feeding each slice the previous hand-off, and assert the final `DayOutcomes` equals `simulation.run_day`'s report on the same fixture field for field. If they differ, the slice decomposition is wrong; do not adjust the simulator to match.
> - Stub acceptance tests for every row A1–A9, B1–B9, C1–C12 as `pytest.mark.xfail(strict=True, reason="row not built")`, so the count of open rows is visible and a row that starts passing unexpectedly fails the build.

## Task 15 — Slice 3: depot delivery and outcomes (docs/e2e/e2e-3)

> - Spec first: write `docs/spec-proposals/v0.14-cancellation.md` adding the Cancelled outcome and the `Ready → Return run (cancelled)` edge to §6 and §5.2.6. Stop and wait for approval before implementing.
> - `model/lifecycle.py`: add the edge. `lastmile/`: remove a not-yet-visited stop from an active route on cancellation; refuse cancellation for Delivered. `api/routers`: `cancelled` event on `POST /envelopes/{id}/events` with 409 on refusal.
> - Pool ordering per e2e-3 §2.1 (priority, SLA date, attempt number desc) — in `lastmile/`, not in the harness.
> - Depot vs hub hand-off per e2e-3 §2.5: depot outcomes accumulate as a return load for the next van leg; hub outcomes enter tonight's return run. Reuse Task 11's day-boundary fix.
> - SLA expiry sweep at end of day.
> - Turn C1–C12 from xfail into passing tests. C12 re-runs mutation M6 and asserts on detail text.

## Task 16 — Slice 2: hub to depots (docs/e2e/e2e-2)

> - `processing/sorting.py`: straddle rule per e2e-2 §2 item 1 — envelopes within EQUIDISTANT_MARGIN_M of two facilities are kept at the hub with reason "held-straddle" until address-geocoded. Register the rule in docs/assumptions.md.
> - `linehaul/plan.py`: wait decision per e2e-2 §3 using `expected_ready_at` from the ready pool; rebalancing proposals emitted as `TransferRequests` with reason=rebalancing for allocation to accept or reject (never applied unilaterally).
> - Hand-off: `PositionedPool` lists hub-direct envelopes under HUB; rolled envelopes carry a reason from the e2e-2 §5 list.
> - Turn B1–B9 into passing tests. B2 is the Task 8 regression test, moved here. B7 depends on Task 11.

## Task 17 — Slice 1: pickups to hub (docs/e2e/e2e-1)

> - `pickups/admission.py`: readiness-weighted insertion per e2e-1 §2.1 — insertion cost = route cost − λ × readiness value, where readiness value uses the pre-sort facility's latest departure and the assembly queue projection from `processing/readiness.py`. λ registered in `ddn/assumptions.py` and docs/assumptions.md as invented with a sensitivity note; λ = 0 must reproduce the current planner exactly (regression test).
> - `processing/readiness.py`: assembly queue ordered by (priority desc, SLA asc, arrival) computed at file receipt, so `expected_ready_at` reflects queue position (Task 12 did the ordering; this makes it incremental).
> - Late admission per §5.1.6 v0.13; late-file exception per e2e-1 A9.
> - Hand-off: `ReadyPool` with per-facility counts, held envelopes by reason, rolled assembly set, van release events.
> - Turn A1–A9 into passing tests. A2 asserts a distributional property (mean collection time of assembly-bearing bags < mean of others) — state the tolerance and why.
> - Re-run the chaining test; it must still equal `simulation.run_day`. If λ > 0 changes the simulated day, the simulator must call the same admission function — fix that in `simulation/`, not by setting λ = 0.
>
> Finish with the full runner output and the xfail count (must be zero).