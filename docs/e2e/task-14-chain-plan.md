# Task 14 — Final Plan: Harness scaffold and chaining test

*All figures below were measured this session against the committed tree (seed 7, `tests/peak_day_inputs.build()`), not carried over from the design notes. Where a design note or a map disagreed, the measurement wins and the disagreement is named.*

---

## 1. Verdict

**Not as specified.** Task 14's chain test cannot be built from the committed tree, and the blocker is not the simulator — it is the hand-off models. `PositionedPool.positioned` is `dict[str, tuple[str, ...]]` (`ddn/e2e/handoff.py:108`): package ids only. But e2e-3 §3's own inputs table says slice 3 reads "priority, sla_date, weight_g, attempt_number, previous_outcome, locked_vehicle_id / locked_out", and `lastmile.select` (`ddn/lastmile/plan.py:119`) reads all of them off §9.1 records. So "feeding each slice the previous hand-off" is not achievable: slice 3 would have to re-read records from its own `scenario.py`, and every per-package assertion would then test a fixture file rather than the decomposition. The same defect, worse, sits one boundary up: `ReadyPool.ready` is `dict[str, tuple[Envelope, ...]]` (`handoff.py:86`) and `Envelope` has no `customer_lat` / `customer_lon` (verified: 22 fields, neither present) — yet `returns.sites` reads `batch[0]["customer_lat"]` (`ddn/returns/run.py:127`) and `_return_run` raises `ValueError` without it (`day.py:400-406`). Routing the pool through `Envelope` makes slice 3 **crash**, not differ. Second, `DayOutcomes == DayReport` is not constructible: 9 per-facility fields against 18 whole-day fields, three shared names, and one of those three (`violations`) compares `check_route_constraints` against `check_day_constraints` — different §7.1 bullets.

**What is *not* a blocker, contradicting the map's headline finding:** the per-package oracle is available **today**, through public keyword arguments, with **zero simulator change**. `deliver` (`day.py:483`) and `rates` (`day.py:482`) are public; `rates` is touched in exactly one place in the package (`rates.draw(rng)`, `day.py:228`). I ran it: a spy `deliver` returning `(list(offered), [])` — byte-identical to `_served_everything` (`day.py:148-152`) — plus a duck-typed `Rates` recorder produced `report == report` **True** and `next_state == next_state` **True** against the uninstrumented run, with 3,000 unique attempted ids in order and `Counter(drawn) == report.outcomes` **True**. So no `DayReport.outcome_of`, no promotion of `_doorstep`, no §9 edit, and no collision with *"do not adjust the simulator to match"* (`docs/claude-code-task-prompts.md:181`).

**Smallest honest alternative — split Task 14 in two.**

- **14a (no approval gate beyond this document):** fix the two hand-off models, pin the oracle and the spy's inertness in `tests/test_simulation.py`, build the three slice packages, write the AST purity test. Lands in small commits. No chain test yet.
- **14b (needs your explicit yes on §8 Q1–Q3):** promote five private seams out of `ddn/simulation/day.py` so the slices *call* rather than *copy*, then write `tests/e2e/test_chain.py`.

Without 14b's promotions there is no third option: a `run.py` either reimplements `_position`/`_ready_times`/the two `_collect` comprehensions — comparing a copy against its original, which is exactly the failure Task 14 exists to detect — or it imports a private, which the purity rule forbids. And one thing must be said before you read further: **this chain test can only ever prove wiring, cohort and ordering. It cannot prove logic**, because matching field-for-field forces `run_day`'s non-routing default `deliver`. Section 7 states that plainly and the test's docstring must too.

---

## 2. What the chain actually compares

### 2.1 The oracle

```python
inp   = tests.peak_day_inputs.build()          # 8 kwargs; supplies no seed
SEED  = 7                                       # as tests/test_simulation.py:44
start = State(day=inp.day.collection_day, pools={})

collected, night    = run_day(start, seed=SEED, **inp.kwargs)                    # day D
delivered, tomorrow = run_day(night,  seed=SEED, deliver=spy, rates=rec,
                              **inp.kwargs)                                      # day D+1
```

Two calls by hand, never `run_days(2, ...)` — that discards the intermediate `State` (`day.py:600-606`). Measured: day D `dispatched=0`, `positioned={HUB 1368, D1 767, D2 605, D3 500, D4 400, D5 320, D6 180}` (4,140), `uncollected=410`, `carried_into_tomorrow=4140`; `night.pool_size=4140`. Day D+1: `dispatched=3000` (= 120 bikes × `EFFECTIVE_PER_BIKE` 25, fleet-saturated), `outcomes={'Delivered': 2794, 'Postponed': 121, 'Rejected': 51, 'Returned': 34}`, `unassigned=1140` all reason `'count'`, `return_stops=15`, `violations=()`.

`spy` and `rec` live in `tests/e2e/test_chain.py`, never in `ddn/e2e/`. Their inertness is a property of `run_day` and is pinned in the **simulator's** suite (Step 2), not asserted by the chain about itself.

### 2.2 The mapping table — all 18 `DayReport` fields, four buckets

| `DayReport` field | Bucket | Compared against what |
|---|---|---|
| `day` | **COMPARED (D+1)** | every `DayOutcomes.delivery_day` |
| `tally` | **COMPARED (D+1), 13 of 19** | `sum((o.tally for o in outs), Tally())` — no `__radd__`, start value mandatory |
| `outcomes` (`dict[str,int]`) | **COMPARED (D+1)** | `Counter` of the merged per-package map; *and* the map itself vs `zip(spy ids, rec.drawn)` |
| `unassigned` (`tuple[Excluded]`) | **COMPARED (D+1)**, ordered tuple | concatenated `o.unassigned` in facilities order |
| `return_stops` (`int`) | **COMPARED (D+1)**, per package | hub `ReturnLoad` through `returns.sites` → 15 stops; *and* the hub/depot split checked per package against the spy (see §2.4) |
| `carried_into_tomorrow` | **COMPARED (D+1)**, with a cap | `next_state == READY` ∪ `unassigned`, vs `tomorrow.pools`; measured 1,261 = 1,140 + 121. **This is a miniature `_handover`; if it grows past five lines, promote `_handover` instead** |
| `positioned` (`dict[str,int]`) | **COMPARED (D)** | `{f: len(ids)}` from `PositionedPool`, *and* ordered ids vs `night.pools` |
| `uncollected` (410) | **COMPARED (D)** | needs a new `ReadyPool.uncollected: int` — §5.1.6's loss channel has no hand-off field today (Step 1) |
| `tally.received / received_before_cut_off / ready_by_cut_off / pickups / pickup_wait_seconds` | **COMPARED (D)** | against the `ReadyPool`, not against the summed `DayOutcomes` — `_count` (`day.py:448-470`) fills these from `inflow`/`dispatch`, which a delivery slice cannot see |
| `rolled`, `rolled_envelopes` | **VACUOUS** | `{}` and `0` on this fixture; asserted *vacuous* by the ledger |
| `transfers_raised / carried / declined` | **VACUOUS** | `0 / 0 / {}` — `_raise_transfers` returns `[]` immediately when `regeocode is None` (`day.py:267-268`) |
| `violations` | **VACUOUS, and cross-check** | `()` on both sides, and the two sides call different §7.1 checks. Counted as zero evidence |
| `postponed_reasons` | **HOLE — named** | real value `{'recipient unavailable': 56, 'driver out of time': 38, 'incorrect address': 27}`. `DayOutcomes` has no sub-reason field. Proposed hand-off row, **deferred to Task 15** |
| `metrics`, `checks`, `allocation` | **NO SLICE OWNER** | `metrics` is `measure(tally)`, derivable; `checks` is §8.3 day-level capacity; `allocation` is a `FleetPlan` and e2e-3 §1 puts fleet allocation **outside the boundary** verbatim. Asserting these would be self-comparison |

`DayOutcomes`' nine fields map back: `delivery_day`/`outcomes`/`unassigned`/`tally`/`returns` → COMPARED; `facility_id` → structural (§2.3 G7); `next_state` → compared against `tomorrow.pools` and `tomorrow.returns_queue`, since `run_day` produces no `Status` at all; `transfers` → VACUOUS; `violations` → VACUOUS.

### 2.3 Guards — fail-closed, asserted before any comparison

| | Assertion | Measured | Why |
|---|---|---|---|
| G1 | `collected.tally.dispatched == 0` **and** `collected.tally.ready_pool == 0` | 0, 0 | Closes D1. Two equalities, not an inequality — the unbounded-guard bug this repo has shipped before |
| G2 | `collected.day == collection_day`, `delivered.day == delivery_day` | 2026-09-16 / -17 | Closes D4 at the oracle end |
| G3 | `night.pool_size == sum(collected.positioned.values())` | 4140 == 4140 | Closes D2. **Also the plan's biggest blind spot — see §7** |
| G4 | `len(spy.attempted) == delivered.tally.dispatched == len(rec.drawn)`, ids unique | 3000/3000/3000 | Instrument coverage. *Labelled an instrument check, not an oracle check* |
| G5 | `Counter(rec.drawn) == delivered.outcomes` | True | Same. `day.py:228-229` counts what `draw` returned, so this is near-tautological and says so |
| G6 | **Vacuity ledger** (§2.5) | | |
| G7 | `set(pos.positioned) == {f["id"] for f in inp.kwargs["facilities"]}` | 7 == 7 | `day.py:525` rebuilds pools from the `facilities` argument, so a facility omitted from the hand-off would be silently dropped with no error |

### 2.4 The assertions, ranked by what they can actually catch

- **A1 — ordered hand-off equality (needs no instrumentation).** `list(pos.positioned) == list(night.pools)` and per facility `pos.positioned[f] == tuple(e["package_id"] for e in night.pools[f])`. Catches both halves of D3's real mechanism: the `facilities`-argument key order (`day.py:525`) and the within-facility original pool order (`lastmile/plan.py:172` returns `[p for p in packages if p["package_id"] in kept]` — pool order, not priority order, even though `select` sorts internally at `:168`). Survives someone stripping the recorders.
- **A2 — flat attempt sequence.** `[pid for o in outs for pid in o.outcomes] == [e["package_id"] for e in spy.attempted]`. **This is the load-bearing assertion.** Measured: sorting each facility's positioned ids by `package_id` leaves the selected *set* identical, the whole `Tally` identical and `DayReport.unassigned` identical, while changing **392 of 3,000** per-package outcomes. Only order catches it.
- **A3 — per-package outcomes.** Merged `{**o.outcomes}` vs `{pid: Outcome(v) for pid, v in zip(spy ids, rec.drawn)}`. **Honest scoping: given A2, this is largely redundant.** The chain's outcome source is built in the test from the public `Rates()` and `sub_reason` over `random.Random(SEED + delivery_day.toordinal())`; with identical ids in identical order and one draw each, agreement follows mechanically. Its *independent* power is over draw count and interleaving only (CM4, CM5). It is kept for that, and labelled as such — not as "the test". Also assert once that the four bare strings map onto the enum (`Outcome("Returned") is Outcome.RETURNED`); nothing in the repo pins that agreement.
- **A4 — unassigned, ordered tuple** (not a set), 1,140 `Excluded`, same type both sides (`ddn/contract.py:114`).
- **A5 — next state**, cross-checked against what `run_day` does publish: POSTPONED ⊆ `tomorrow.pools` with `attempt_number` incremented and `status="Ready"` (`day.py:609-613`); **depot** Rejected/Returned ⊆ `tomorrow.returns_queue`.
- **A6 — return load, per package.** Measured: 85 going back = **33 hub-resident across 15 customers + 52 at depots**. A5 alone cannot see the 33, because `day.py:592-594` filters hub-residents out of `returns_queue` and Delivered also appears nowhere. **The spy closes this**: the oracle side reconstructs `{pid : outcome}` *with its facility*, so the hub `ReturnLoad`'s ids are compared exactly, then `len(returns.sites(...)) == delivered.return_stops == 15`. Without the spy, dropping any one of 28 of those 33 envelopes still yields 15 stops.
- **A7 — tally projection, 13 of 19**, explicitly the weakest assertion in the file and labelled so: D3 proves tallies are near-worthless here. The six exclusions sit in a literal dict with a reason each, and are compared at day D instead.
- **A8 — the partitions.** Three set equalities over the real type objects: `{f.name for f in fields(DayReport)} == COMPARED_D1 | COMPARED_D | VACUOUS | NO_SLICE_OWNER`; the same for `DayOutcomes.model_fields` and `fields(Tally)`. **A field added to any of the three reds the chain until a human classifies it.** The fourth bucket (`NO_SLICE_OWNER`) exists because three earlier drafts of this partition had nowhere honest to put `metrics`, `checks`, `allocation`, `postponed_reasons` and `carried_into_tomorrow` and could only be made to close by mislabelling them.
- **A9 — no shared transformation.** Structural rule, stated in the docstring: the oracle side of every `==` is built only from `run_day`'s two return values plus the two recorders; the chain side only from slice returns. Nothing reachable by both.
- **A10 — no in-place mutation.** `build()` twice and compare. **Caveat found by measurement:** seven of eight kwargs are fresh per call, but `allocation` **is the same object** (`a.kwargs['allocation'] is b.kwargs['allocation']` → True; it is `peak_day.BIKE_ALLOCATION` itself, `tests/peak_day_inputs.py:99`). So comparing the two with `==` compares one object to itself. Snapshot `dict(allocation)` before the run and compare against that.
- **A11 — order survives serialisation.** `DayOutcomes.model_validate_json(o.model_dump_json()).outcomes` preserves key order. A2 leans on it across the boundary `handoff.py:4-7` exists to make real, and nothing in `handoff.py` says the order is load-bearing. It must.

### 2.5 The vacuity ledger (G6) — assert that a field *is* empty

`transfers_raised == 0`, `transfers_carried == 0`, `transfers_declined == {}`, `rolled == {}`, `rolled_envelopes == 0`, `violations == ()`, `tally.sla_expired == 0`, `tally.distance_m == 0`, `tally.solver_seconds == 0.0`, every attempted envelope has `attempt_number == 0` (measured: `Counter({0: 3000})`, so `delivered == delivered_first_attempt == delivered_within_sla == 2794` and three "summable" tally fields carry no independent information), zero `deliver` refusals (so `unassigned` is 1,140 × `'count'` and A4 never exercises the count/time interleaving at `day.py:205-208`), `night.returns_queue == ()` and `tomorrow` is the chain's first consumer of one (so §5.5's day boundary is never exercised), and `TransferOutcomes` is **100% vacuous** — slice 2 can contain no transfer code whatsoever and pass.

Each carries the message *"this proves nothing on this fixture; if this fires the fixture gained coverage and the exclusions above must be revisited."* This converts the classic false green — an empty comparison that silently starts mattering — into a red. **It does not make the covered paths covered.** No comment makes a `() == ()` discriminate.

---

## 3. The four traps, and what closes each

| | Trap | What closes it |
|---|---|---|
| **D1** | 1→2→3 is not the order a day runs in | Verified: `_attempt(532) → _doorstep(533) → _raise_transfers(535) → _collect(540)`. Closed by construction — two threaded calls, day D given no pools (dispatches 0), day D+1 comparing the delivery half. G1 fails loudly if anyone reseeds. **Correction to the trap text:** "dispatches 0" holds *only* for the empty start; with `build().pools` a single `run_day` dispatches 2,910 |
| **D2** | cohort | Closed by writing **no cohort number** into the chain test. Every expectation is read off the oracle's own report at runtime. The literals live in Step 2's pin, in the simulator's suite, where they belong. **Correction:** the trap's "~2,750 vs ~2,794" is wrong — 2,750 is `peak_day.DELIVERED`, a §10 *document* constant, never a simulator output; the pool is 3,100 / 4,140 / 4,446 depending on start state, three cohorts not two |
| **D3** | RNG stream: identical aggregates, different per-package outcomes | Closed by **A1 and A2**, not by A3. **Correction: the trap's mechanism is misnamed.** It is not `sorted(pools)` and not `state.pools`' order — `day.py:525` rebuilds the dict keyed by the `facilities` *argument*, so two independent things must match: the facilities sequence *and* each facility's pool sequence. Measured at seed 7: facility reorder → **386 of 3,000** differ with byte-identical aggregates *and a byte-identical `_Doorstep`* (`outcomes`, `reasons`, `within_sla`, `first_attempt`, `len(postponed)`, `len(going_back)` all equal). Within-facility sort → **392** differ, same story. The design note's 386 is right for this configuration; the maps' 376/400/501 were measured on other start states |
| **D4** | `today` is passed, not read | Verified: `select(packages, *, capacity, today)` at `lastmile/plan.py:119`, no `date.today()` on any slice path. Closed by threading one date, and by constructing the chain's rng from the **delivery** day's ordinal (`day.py:522` folds the date into the seed). The fixture's deliberate one-day offset — every second-of-day field relative to `collection_day`, the state on `delivery_day` — is **reproduced, not normalised** |

A fifth trap the design notes miss, and it shapes slice 3's API: the stream is **interleaved** — `sub_reason(rng)` is drawn from the same `Random` inside the outcome loop, only for postponements (`day.py:227-238`). A slice drawing all outcomes then all sub-reasons diverges even with byte-identical ordering.

---

## 4. Steps

### Step 1 — Fix the two hand-off models. *(Blocker. Lands first, alone.)*

`ddn/e2e/handoff.py`:

- `PositionedPool` gains `envelopes: dict[str, tuple[dict[str, Any], ...]]` — §9.1 records in positioned order, hub-direct under `HUB` — keeping `positioned` (ids) as the ordered index e2e-2 §5 row 2 actually names. Slice 3 selects from `envelopes`; A1 compares `positioned`.
- `ReadyPool.ready` changes from `dict[str, tuple[Envelope, ...]]` to `dict[str, tuple[dict[str, Any], ...]]`, and `ReadyPool` gains `uncollected: int = 0` (§5.1.6, `day.py:362-372`, 410 on this fixture).
- `handoff.py`'s module docstring (lines 9–14) is rewritten in the same commit. Its argument — §9 is the only schema definition, so a hand-off composes entities rather than restating fields — is sound *in principle* and **false for this entity**: `Envelope` cannot hold `customer_lat`/`customer_lon`, §9.1 puts the sender's site on the Mailbags and Return-run-stops tables, and `_return_run` (`day.py:400-406`) raises rather than degrades without it. Changing the type and leaving the prose is `MEMORY.md`'s *prose about code fails by agreeing*, exactly.
- Document that `positioned`'s tuple **order is load-bearing** (A1, A2, A11) — nothing says so today.
- `ddn/e2e/__init__.py` describes `scenario.py` and `run.py` in the present tense for a directory holding only `__init__.py` and `handoff.py` (verified). Corrected in this commit, not later.

This is a §9-adjacent change to a hand-off model, so per CLAUDE.md I quote the section and state the interpretation in the commit: §9.1's Envelope table has no sender coordinates; carrying `dict[str, Any]` §9.1 records is *not* a new schema, it is the shape every §5 module already produces and consumes.

### Step 2 — Pin the oracle and the instrument in `tests/test_simulation.py`. *(No production code.)*

Two module-local tests (no `conftest.py` exists anywhere; creating the repo's first is a new convention, not a step of this task):

1. `test_the_peak_day_threaded_from_an_empty_start` — pins day D (`dispatched 0`, the seven `positioned` counts, `carried 4140`, `uncollected 410`) and day D+1 (`dispatched 3000`, the four outcome counts, `ready_pool 4140`, `unassigned 1140` all `'count'`, `return_stops 15`, `day == 2026-09-17`) as literals, with a comment saying these are §10's day *as this simulator answers it* and that moving them is a spec question.
2. `test_a_recording_deliver_and_rates_change_nothing` — asserts `report == report` **and** `next_state == next_state` between the plain and instrumented runs. This is the anti-tamper mechanism and it is **not** self-comparison: it compares two independent `run_day` calls.

If a later refactor moves either pin, the refactor is wrong. **Do not re-pin.**

### Step 3 — Promote five seams out of `ddn/simulation/day.py`. *(Needs your yes — §8 Q1.)*

Each keeps its body byte-identical and changes `day.py` to call the promoted version, so exactly one implementation exists. Each ships with tests in its own module's test file.

| Seam | Today | Proposed home | Why the chain needs it |
|---|---|---|---|
| `_ready_times` (`day.py:352-359`) | private | `ddn/pickups/dispatch.py` (it reads only `.routes`/`.returned_at`) | the only `Dispatch` → `processing.schedule` `arrival_of` bridge; nothing public converts one to the other |
| `_position` (`day.py:375-394`) | private | `ddn/processing/` | stamps `expected_ready_at` **and carries the sender's site**; without it slice 3 crashes with `ValueError`, not differs |
| the hub/depot inflow split (`day.py:336-338`) | an inline comprehension | `ddn/linehaul/plan.py` | e2e-2 §1's entire boundary decision, and not a function at all |
| the post-plan roll strip (`day.py:344-348`) | an inline loop | `ddn/linehaul/plan.py`, beside `LinehaulPlan.rolled` | ditto |
| `_expired` (`day.py:616-618`) | private | `ddn/lastmile/plan.py`, beside `select` | §6.1's sweep. **Do not substitute `contract.triage`** — it applies the same SLA test but *also* excludes on geocode confidence, status and `excluded_by_ops`, silently changing the cohort |

Acceptance: all 844 existing tests green, and Step 2's two pins byte-identical.

**Deliberately not promoted:** `_attempt`, `_doorstep`, `_raise_transfers`, `_handover`, `_count`, `_checks`. Promoting `_attempt`/`_doorstep` is what makes the chain agree with itself — both sides would then run the same function on the same inputs in an order the test file hands to both. `_raise_transfers`' owning module is genuinely unclear (§5.3.2's trigger, raised by delivery and carried by line-haul) and it is a no-op on this fixture. `_handover` is discussed in §7.

**Overlap with later tasks, stated rather than hidden:** the hub/depot split and roll strip touch what Task 16's `linehaul/plan.py` bullet owns; `_ready_times`/`_position` touch Task 17's readiness bullet. These are *promotions of existing bodies*, not the new rules those tasks add. If you would rather they wait, say so — the consequence is in §8 Q1.

### Step 4 — The three slice packages.

`ddn/e2e/pickups_to_hub/`, `hub_to_depots/`, `depot_delivery/`, each `__init__.py` + `scenario.py` + `run.py`. **`handoff.py` stays shared** at `ddn/e2e/handoff.py` rather than one per package as Task 14's first bullet says: three copies of one contract are three places to drift, and `handoff.py:9-14` already argues it. Deviation stated, not silent.

- `pickups_to_hub.run` → `pickups.run` → promoted arrival bridge → `processing.schedule` → promoted position step → `ReadyPool`.
- `hub_to_depots.run(ready, ...)` → promoted split → `linehaul.plan` → promoted roll strip → `check_day_constraints(Day(...))` built exactly as `day.py:559-564` builds it → `PositionedPool` + `TransferOutcomes`.
- `depot_delivery.run(pos, *, facility_id, delivery_day, bikes, deliver, outcome_source)` → promoted expiry predicate → `lastmile.select` → `outcome_source` → `replace_record` (public, `day.py:609`) → `returns.goes_back`/`sites` → `DayOutcomes`.

Three API decisions, each forced by a document or a rule:

1. **Outcomes are an input, not a draw.** e2e-3 §3 lists them under "Driver app, operations (§13.2)" and puts outcome rates under "Assumptions … (simulation only)". The injected protocol is stated in `run.py`: `outcome(envelope)` is called exactly once per attempted envelope in attempt order, and `reason(envelope)` **immediately after, only for a postponement** — that contract is what makes `day.py:227-238`'s interleaving reproducible. This is also what keeps `ddn/e2e/` free of any `ddn.simulation` import.
2. **No `allocation.place` in slice 3.** e2e-3 §1 puts fleet allocation outside the boundary verbatim; bike counts arrive as a parameter.
3. **`deliver` is a parameter with no default.** The non-routing stub lives in `tests/e2e/test_chain.py` — never in `ddn/e2e/`, which would be a solver mock outside `tests/`. `scenario.py` likewise ships **no** fabricated outcome table for the chain's use; the fixed table rows C1–C12 will want is Task 15's, and it belongs with those rows.

Fields with no producer anywhere in `ddn/` stay at their defaults, each with a comment naming the row that will fill it: `ReadyPool.rolled_assembly` (A3 — `processing.schedule` applies no cut-off at all), `ReadyPool.held` straddle/disputed (A4/A6/B6 — `Sorted.straddles` is a bool and nothing converts it; `output.IN_DISPUTE` has no producer by its own admission), `VanRelease.unloaded` (B8, `[TBD]` in e2e-1 §5), `TransferOutcomes.to_returns` (B3 — `lifecycle.TRANSITIONS` permits the edge and no code performs it), `DayOutcomes.next_state` needs `outcome_to_status` in `ddn/model/lifecycle.py` (`may`/`advance` *validate* a chosen transition; nothing chooses). Writing any of them into `run.py` would be new logic in an e2e file.

### Step 5 — `tests/e2e/test_run_purity.py`.

AST allow-list over **every** module in each slice package except `scenario.py` (permitted nodes: `Module, Import, ImportFrom, alias, FunctionDef, arguments, arg, Expr, Assign, AnnAssign, Name, Store, Load, Return, Call, keyword, Attribute, Constant, Tuple, List, Dict, Starred`; everything else fails by omission — `ListComp`, `DictComp`, `GeneratorExp`, `Lambda`, `IfExp`, `For`, `If`, `Compare`, `Subscript`). Import rules: every module in a named `PUBLIC_SEAMS` list; **no imported name begins with `_`** (this is what makes Step 3 mandatory rather than optional — it is exactly what forbids `from ddn.simulation.day import _attempt`, which `tests/test_simulation.py:423` and `:570` already do because there is no other way); no `ddn.simulation` in any form; `ddn.solver_adapter` contributes only the two check functions; `ddn.contract.to_problem` and `ddn.solver_adapter.problem` denied by name.

**Honest limitation, in the test's own docstring, because a guard that overclaims is the next artifact to fail by agreeing.** This is a syntactic check on each file's own source. It is *not* a proof and *not* an import-graph check: `ddn/lastmile/plan.py:33-37` already reaches `ddn.solver_adapter.output` for reason strings, so a runner importing `select` transitively touches solver_adapter and the test will not see it. Three evasions are **open, not closed**: a filtering loop moved into `scenario.py` needs no imports at all, so an import-based rule on `scenario.py` does not catch it; `builtins.sorted(xs, key=operator.itemgetter('p'))` and `from builtins import sorted as rank` both pass a bare-`Name` deny-list because the call target is an `Attribute` or an alias; and `functools.reduce`/`itertools.starmap` likewise. Earlier drafts claimed these were closed; they are not. The allow-list's real job is to make evasion **visible in review** — evading it requires writing something that plainly does not look like a sequence of calls.

### Step 6 — `tests/e2e/test_chain.py`.

One module, module-local fixtures. **Not an acceptance row**: no `xfail`, no `_not_built`, no row id — `tests/e2e/rows.py` pins `EXPECTED = {"A": 9, "B": 9, "C": 12}` and offers only `document(row_id)`; the chain spans all three documents and sits outside that contract. It cites the three documents in its docstring instead.

Hard rules written into the file: no `pytest.approx` anywhere (`tests/test_simulation.py:90` is `approx(2880, rel=0.06)` — a band from 2,707 to 3,053; that is the simulator's tolerance for §10's prose, and the chain has no such licence); no literal from `tests/fixtures/peak_day.py` as an expected value and no remembered figure; cardinality before content, so an empty chain cannot pass vacuously; and one paragraph saying what a failure means — *the decomposition is wrong; do not adjust `ddn/simulation/`; the simulator's answer is pinned by name in `tests/test_simulation.py`; a genuine disagreement about what the day means is a spec change to `docs/vrp-problem-definition.md`, made there first.*

Plus the **negative control**, in the same file and properly floored: re-run the chain feeding `sorted(facilities)`, assert the id **sets** are equal (so a slice that drops a facility does not pass as "differs"), the flat sequences differ, the per-package map differs, **and** the aggregate `Counter` still matches. That last clause pins a fixture-dependent coincidence, not a property — it will red on any `Rates` or cohort change, with a message that says so.

### Step 7 — Front doors, every commit that adds a test.

`CLAUDE.md:40` and `README.md:36` both read `# 844 tests` today and the suite collects exactly 844 (verified). `tests/test_front_doors.py` compares both against `request.session.testscollected` and requires them equal to each other. Move both in the **same** commit as each test-adding change, from `uv run pytest tests/ -q --collect-only | tail -1`. Miss it and `make test` reds on the front doors rather than on the thing that changed.

---

## 5. Commits

Each row is one reviewable commit; the `[gate]` marker is where §8's answers are required before proceeding.

1. `fix(e2e): a hand-off of Envelopes drops the sender's site the return run needs` — Step 1, model + docstring + `__init__` prose.
2. `test(simulation): §10's peak day threaded from an empty start, pinned here` — Step 2(1), moves both front doors.
3. `test(simulation): a recording deliver and rates change nothing about the day` — Step 2(2), moves both front doors.
4. `[gate]` `refactor(pickups): arrival times read off Dispatch, not out of the simulator`
5. `refactor(processing): positioning carries the customer site, and it was private`
6. `refactor(linehaul): the hub/depot split and the roll strip were comprehensions in _collect`
7. `refactor(lastmile): the SLA-expiry predicate, promoted beside select`
8. `feat(model): an outcome's next status, which §5.2.6 draws and nothing chose`
9. `feat(e2e): slice 1, pickups to hub`
10. `feat(e2e): slice 2, hub to depots`
11. `feat(e2e): slice 3, depot delivery — outcomes arrive, they are not drawn`
12. `test(e2e): what a run.py may import, and what it may not loop over`
13. `test(e2e): the chain over two threaded days, in order and per package`
14. `test(e2e): reordering the chain changes 386 packages and no tally`

Commits 4–7 are a cross-module refactor and land **before** any `run.py` exists, so review can tell a promotion from a rewrite. CLAUDE.md: *do not refactor across modules in the same change as a feature.*

---

## 6. Mutation plan

Every mutation below was either measured this session or is mechanically forced by a measured fact. `CM*` namespace, deliberately distinct from `docs/audit/conformance-2026-09-19.md:642-652`'s `M1-M8` — **that dated audit record is not amended** (Task 15 already reserves "mutation M6" against its own numbering).

| | Mutation | Caught by | Not caught by |
|---|---|---|---|
| CM1 | slice 2 groups positioned by `sorted(facility_id)` | A1, A2, A3 (**386 of 3,000 differ**, measured), A4 | A7 — `Tally`, `outcomes` and `postponed_reasons` byte-identical, and the whole `_Doorstep` identical |
| CM2 | slice 2 sorts each facility's ids by `package_id` | **A1, A2, A3 only** (392 differ, measured) | everything else: selected set identical (symmetric difference 0), whole `Tally` identical, `DayReport.unassigned` identical. **This trap is outside D1–D4 and is A2's whole justification** |
| CM3 | slice 3 uses the collection day as `today` | A2 (cohort moves — §6.1 forces SLA-today in), A3 (different rng stream entirely), A7, `delivery_day` | — |
| CM4 | slice 3 seeds its own rng, or one per facility | **A3 only** | A1, A2 — ids and order unchanged |
| CM5 | slice 3 asks the outcome source for selected-but-refused envelopes | **A3 only** | as above |
| CM6 | the chain feeds slice 3 e2e-3 §7's own 3,100 MRN-\* scenario instead of slice 2's 4,140 PKG-\* output | A1 (size and ids), A2, A7, G3 | — |
| CM7 | the chain is compared against a single `run_day` | G1, G2 | — |
| CM8 | slice 1 assigns facilities via `processing.presort` instead of reading `facility_id` off the record | A1 | — **and this is the perverse one**: `presort` and `model.nearest_facility` exist and implement §3.3/§5.2.4 correctly, and `run_day` calls **neither** (inflow arrives with `facility_id` pre-set, `tests/peak_day_inputs.py:67-72`). The one place an existing public function covers a slice-document requirement is the place where using it breaks the chain |
| CM9 | slice 3 returns outcome counts instead of a per-package map | A8's `DayOutcomes` partition, A2, A3 | — |
| CM10 | slice 3 mislabels a **hub** reject as Delivered | **A6** (per-package hub load, via the spy) | A5 — hub residents appear in neither `tomorrow.pools` nor `returns_queue` (`day.py:592-594`), so A5 alone is blind to all 33. `return_stops` alone is blind to 28 of them (customer batch sizes `Counter({1:5, 2:4, 3:4, 4:2})`) |
| CM11 | slice 3 puts hub rejects in the depot return flow, or vice versa | A5 + A6 together | — |
| CM12 | slice 1 drops the `customer_lat`/`customer_lon` stamping | caught as a `ValueError` out of `ddn/returns/run.py`, not as a mismatch — worth its own test asserting the error so the failure is legible | — |
| CM13 | slice 2 omits a facility whose pool is empty | **G7** | A1 — it iterates what the hand-off contains. On this fixture every facility is non-empty, so without G7 this is invisible today and loses envelopes on any other day (`day.py:525`, `day.py:445`) |
| CM14 | slice 2 drops the rolled-ids filter | **NOT CAUGHT** — `rolled == {}` on this fixture | ledgered (G6); owned by rows B4/B6 |
| CM15 | slice 3 skips the SLA-expiry sweep | **NOT CAUGHT** — `tally.sla_expired == 0` | ledgered (G6); owned by row C10 |
| CM16 | slice 2 contains no transfer code at all | **NOT CAUGHT** — `TransferOutcomes` is fully vacuous | ledgered (G6); owned by row B3 |
| CM17 | a slice mutates a §9 record in place | A10, using the snapshot — **not** the naive `==`, which compares `allocation` to itself |

Three uncaught mutations, all ledgered, none hidden. Closing them needs a second chain scenario (one regeocode, a handful of stale envelopes) — §8 Q4.

---

## 7. What this plan does NOT do

**It does not prove routing, and it structurally cannot.** Matching field-for-field forces `run_day`'s default non-routing `deliver` (`day.py:148-152` returns `(list(offered), [])`). A chain that routes cannot match; a chain that matches does not route. e2e-3 §1 puts "Per-facility CVRP with route-duration limit, 10 min per envelope, 35-envelope cap" **inside** slice 3's boundary, and Step 5's import rule permanently forbids `run.py` from importing `lastmile.plan_facility`, the only per-facility CVRP. This is the caveat `day.py`'s own module docstring makes at lines 14–18, and the test's docstring must repeat it.

**It does not prove the slices are right — only that they are wired the same way the simulator is.** This is the sharpest criticism raised against every candidate design and it is accepted, not answered. Each slice document's headline rule *breaks* this test: e2e-2 §1 puts the nearest-facility rule inside slice 2 while `run_day` reads `facility_id` off the record (`day.py:336-338`); e2e-3 §2.1 says selection ties break "by SLA date, then attempt number" while `lastmile/plan.py:168` sorts by `(-priority, package_id)` — and 1,140 envelopes are declined here, so the ties are live; e2e-2 §2.1's straddle keep-at-hub, e2e-1 §5.2.3's assembly roll-off and e2e-3 §2.2's cancellation are the same shape. **Green therefore means "no slice has yet implemented a rule the simulator lacks."** Task 15's third bullet is pool ordering per e2e-3 §2.1 — so this test will go red on Task 15's first commit, and Task 17's own text already settles the policy (*"the simulator must call the same admission function — fix that in `simulation/`"*). That is the test working. It should be said now, not discovered in Task 16.

**It does not exercise `_handover`, retry, or §5.5's day boundary.** From the empty start, `night.pools == collected.positioned` exactly (4,140 == 4,140, measured) because held-postponed and yesterday's capacity-declined are both empty — so `_handover` (`day.py:413-445`) is the identity on day D, and G3 *certifies* that a stage with no slice home can be skipped. On any non-empty start it is 4,446 vs 4,140. Likewise `returns_queue` is `()` entering both days, so `returning=`, `rode_home` and the depot-rides-home leg are dead. And e2e-3 §2.1's pool is "positioned yesterday ∪ held postponed ∪ transfers that arrived overnight" — only the first term is ever exercised.

**The oracle is not, strictly, §10's peak day.** `docs/vrp-problem-definition.md:476` describes a morning delivering 3,100 envelopes (2,700 new + 400 postponed held locally) and `:480` gives "dispatched 2,950"; the empty-start D+1 dispatches 3,000 (the fleet cap) on the disjoint 4,550-envelope PKG-\* cohort. CLAUDE.md names §10 as the integration fixture. Choosing the start state so the cohorts line up is, at one level up, the same move the task forbids. **Accepted, and it is Q2 for you.** The alternative — seeding `build().pools` — hands D+1 a pool of 4,446 that the chain cannot produce without a slice-side `_handover`, so the chain fails for a reason that has nothing to do with decomposition.

**Held back for Tasks 15–17, by name:** cancellation and the §5.2.6 edge (Task 15, spec-proposal first); pool ordering per e2e-3 §2.1 (Task 15); the straddle rule and `held-straddle` (Task 16 — note `ddn/linehaul/plan.py:77-78` defines exactly two roll reasons, `NO_VAN` and `NOT_READY_IN_TIME`, against e2e-2 §5's four, and `circuit.OVER_CAPACITY` is a *transfer* decline reason, not an envelope roll); rebalancing proposals (Task 16); λ-weighted admission and the assembly queue projection (Task 17); `_raise_transfers`' home; the transfer-bearing chain variant (which needs a fourth call, 1→2→3→2′, because `run_day` raises transfers at `day.py:535` and carries them in the *same* evening's `_collect`). Also held back: Task 14's "reused by `ddn/api/schemas.py` (no duplicates)" bullet — verified unmet today (`grep` finds no reference), and nothing needs it until an endpoint returns a hand-off. Flagged, not silently dropped.

**Vocabulary that does not match the documents, unreconciled here:** e2e-3 §4 says "locked-out" where `ddn/contract.py` says `EXCLUDED_BY_OPS == "excluded by operations"`. Spec-first, not the chain's to decide, and not exercised on this fixture.

**Task text ambiguity, stated per CLAUDE.md:** Task 14 says "the §10 v0.13 peak day" while CLAUDE.md pins Draft v0.14 and `tests/fixtures/peak_day.py` carries v0.14 constants. Interpretation taken: v0.14, the fixture as committed.

---

## 8. Open questions for Luis

1. **Do the five promotions in Step 3 have your approval?** They edit `ddn/simulation/day.py` in five places. My argument is that promotion *removes a copy* rather than *adjusts to match* — no value changes, and Step 2's pins are the mechanical proof — but the task text says "do not adjust the simulator", the licence for this shape of change is Task 17's, and CLAUDE.md requires approval before code on anything multi-module. **If the answer is no:** `run.py` must import `ddn.simulation.day`'s privates, Step 5's no-underscore rule comes out, and the chain then compares a copy against its original for the slice-1 and slice-2 halves — it would still catch CM1/CM2/CM3/CM6/CM13, but it would be measuring argument-threading, not decomposition, and the test should say so.
2. **Which start state?** `State(day=collection_day, pools={})` — the chain's own cohort, matches, but is not §10's morning (see §7) — or the pools-seeded run `tests/test_simulation.py:42` already has, which cannot match? I recommend the empty start with the deviation written into the docstring. This is the one decision everything else hangs from.
3. **Does slice 3 accept outcomes or draw them?** I recommend accept: e2e-3 §3 lists outcomes under "Driver app, operations (§13.2)" and rates under "(simulation only)", and injection is what keeps `ddn/e2e/` free of any `ddn.simulation` import. The cost is that A3 becomes largely redundant with A2 (§2.4) — the chain's honest discriminators are order and cohort.
4. **Add a second, nastier chain scenario now** (one D1→D2 regeocode, as `tests/test_simulation.py:345-351` already defines, plus a handful of stale envelopes)? It is the only way CM14, CM15 and CM16 are ever caught, and it is scope Task 14 did not ask for. My recommendation: no, ledger them, and let Tasks 15–17's rows own them.
5. **`ReadyPool.uncollected: int` and the `PositionedPool.envelopes` field** (Step 1) are hand-off rows not in the slice documents' tables. `handoff.py`'s closing paragraph refuses to invent a hand-off for exactly this reason. I am proposing to add them because without them the chain cannot be fed and `uncollected` has no counterpart — confirm, or I promote the rows into e2e-1 §4 / e2e-2 §5 first.
6. **`DayOutcomes.postponed_reasons: dict[str, str]`** (package_id → sub-reason) would give `DayReport.postponed_reasons` a counterpart and e2e-3 §8 asks for "postponement rate by sub-reason". Deferred to Task 15 in this plan. Confirm that, or pull it into Step 1.
7. **Homes for the promoted seams** (Step 3's table): `_ready_times` → `pickups` or `processing`? `_expired` → `lastmile/plan.py` beside `select`, or `model/records.py` beside `Envelope.must_deliver_today`? Yours to choose.