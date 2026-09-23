# Plan: T9 — allocation rebalancing, API, simulation and metrics

`docs/claude-code-task-prompts.md:113`. Five bullets across `allocation/`,
`api/`, `simulation/` and `tests/`. CLAUDE.md requires a plan before code on
anything touching more than one module, so this waits for approval.

Everything below was checked against the tree at `7bb9222`, not inferred from
the prompt. Three of T9's sub-bullets turn out to be already built and one
metric turns out to be **wrong rather than missing** — those are the findings
that shape the steps.

---

## 1. What T9 asks, and what the tree already does

### Bullet 1 — allocation rebalancing (§5.3.2's third trigger)

| | Piece | State |
|---|---|---|
| 1.1 | Detect: depot over motorbike capacity, neighbour with slack, leg reaches | **built** — `linehaul.rebalancing` (`ddn/linehaul/plan.py:432`), capped at 50, lowest-priority-first, e2e-tested (`tests/e2e/test_slice_2_hub_to_depots.py:411`) |
| 1.2 | A consumer that *decides* (a) transfer vs (b) reallocate bikes | **absent** — `allocation/` is `__init__.py` + `fleet.py`; nothing imports `Proposal` |
| 1.3 | Cost basis for the comparison | **absent** — `docs/assumptions.md` has Fleet / Capacities / Service times and no relocation-cost row |
| 1.4 | "Either `TransferRequest`s or a changed allocation, never both for the same envelopes" | **absent** (there is no decision to constrain) |
| 1.5 | "Existing allocation output unchanged when no depot is over capacity" | holds upstream by construction (`rebalancing` returns `[]`), unpinned at the decision layer |

`linehaul.rebalancing`'s own docstring already says why 1.2 is not its job:
*"nothing here moves an envelope: §4.2 owns the fleet, and a planner that
rebalanced on its own authority would be making an allocation decision from
inside §5.3."* T9's allocation bullet is that missing consumer.

### Bullet 2 — API (§13.2)

| | Piece | State |
|---|---|---|
| 2.1 | `POST /transfers`, `GET /transfers?status=`, `POST /transfers/{id}/events` | **built** — `ddn/api/routers/transfers.py:27,:76,:87`, with idempotency and audit |
| 2.2 | Leg-level line-haul events (`leg departed`, `leg arrived`) | **absent** — `planning.py:66` still rejects anything but trip-level `departed` / `arrived` |
| 2.3 | Old event names accepted for one release, with a deprecation warning | **absent** |
| 2.4 | `GET /linehaul/plans/{id}` returns legs and per-leg loads (§9.2) | **probably already works.** `JobState.result` is `dict \| None` (`ddn/api/schemas.py:244`) — untyped, so nothing prunes it — and `runner.py:116` returns `asdict(trip)`, which recurses into `Leg`. This needs a **pin test**, not code. |

The code is behind the spec at 2.2: §13.2 (spec line 551) already names
"(`leg departed`, `leg arrived`)". The one-release deprecation is T9's
instruction, not the spec's.

### Bullet 3 — simulation

| | Piece | State |
|---|---|---|
| 3.1 | Raise from postponement sub-reasons at the configured rate | **built** — `day._raise_transfers` (`ddn/simulation/day.py:275`) |
| 3.2 | Include in the nightly plan, arrive next morning, routable at the new depot | **built** — `state.transfers` seeded at `day.py:582`, `_still_waiting` at `:372` |
| 3.3 | Metric: transfers **raised** | **wrong, not missing.** `transfers_raised=len(transfers)` (`day.py:628`) where `transfers = [*state.transfers, *raised]` (`:582`) — the *offered* set, carried-over plus new. The field's own comment at `:148` says "raised today". |
| 3.4 | Metric: **carried** | **built** — `day.py:629` |
| 3.5 | Metric: **deferred** | **absent** (derivable as offered − carried − declined; not reported) |
| 3.6 | Metric: **sent to returns** | **absent, and the behaviour is missing first** — a `MISSES_DEADLINE` decline drops out of `_still_waiting` and its envelope sits at the wrong depot. §5.3.2's "otherwise it is returned to the customer via the hub" is applied at *raise* time only. |
| 3.7 | Transfer van-hours as a share of total van-hours | **buildable.** `linehaul_hours` exists at `day.py:705`, `van_hours_available` at `:713`. What is absent is the *attribution*, i.e. a definition — see §3 below. |

`ddn/simulation/metrics.py` (`Tally`, 22 fields; `Metrics`, 12 §11 rows)
carries nothing about transfers or van-hours.

### Bullet 4 — regression

| | Piece | State |
|---|---|---|
| 4.1 | `peak_day` produces exactly the same numbers as before this task set | **no baseline is pinned.** After T9 touches allocation this becomes unverifiable. This is the one item that is unrecoverable if sequenced late — hence Step 0. |
| 4.2 | `peak_day_transfers`: 28 envelopes delivered from their new depot on D+2, 2 in the return run | **absent.** The fixture declares `CARRIED = 28` and it is referenced **nowhere** in `tests/` or `ddn/` — a dead constant. No multi-day test exists. |

### Bullet 5 — cleanup

| | Piece | State |
|---|---|---|
| 5.1 | Remove wrapper shims from Tasks 7–8 | **nothing to remove.** `grep -rn "shim\|deprecat\|_compat\|backward" ddn/ tests/` returns nothing. |
| 5.2 | CLAUDE.md architecture list mentions transfers under `linehaul/` | **already satisfied** — `CLAUDE.md:26` reads "hub → depot loads, **inter-depot transfers**, returns". |

---

## 2. Two notes on the prompt

**(i) The placeholder answers §12 Q15 *and* Q2; T9 names only Q15.** T9's
citation is correct — Q15 (spec line 526) ends *"the cost basis for comparing
an envelope transfer with a motorbike reallocation"*, which is exactly this
comparison. But Q15 asks for the *basis* and does not supply the number the
(b) arm needs. That is **Q2** (line 513): *"Fleet reallocation: frequency,
relocation time to each depot, and whether rider or bike relocates"* — the
question §4.2 marks at line 122 where it gives relocation a "time cost"
(`*[Open Question 2.]*`). So the placeholder row cites **both**: Q15 for why a
comparison is being made at all, Q2 for the minutes it is made in. Q2 is also
where rider-vs-bike is left open, which is what makes the key's name a
presumption (§7).

**(ii) Bullet 5 is already done — this one is a correction.** 5.1 has
nothing to remove and 5.2 is already written. Neither gets a step. Noted so
the closing report does not claim work that was not performed.

---

## 3. The readings this plan takes

CLAUDE.md: *"When the spec is ambiguous, quote the section, state the
interpretation you are taking, and continue."* Four.

**R1 — the cost comparison is time against time.** §4.2 gives relocation a
"time cost" and nothing else, so (a) and (b) are priced in minutes:

- (a) transfer = the van-minutes the leg **adds**. Zero when `Proposal.leg`
  is already in tonight's circuit — which is the case `rebalancing` gates on
  with `reaches(source, destination)` — and `BIKE_RELOCATION_MIN`-free.
- (b) reallocate = `BIKE_RELOCATION_MIN × ceil(overflow / ENVELOPES_PER_BIKE)`,
  using `fleet.capacity_for`'s existing per-bike figure.

Cheaper wins. **A tie goes to (a).** §4.2's own reason: relocation cost is why
allocation "should change at a frequency where that cost is small", so a tie
is not a reason to move the fleet.

**R2 — transfer van-hours are marginal, not apportioned.** §8.3 (line 388)
says van-hours available are compared against pickup hours plus line-haul
hours "**including inter-depot legs for transfers** … transfers **add to it**".
"Add to it" is a marginal question, so:

> transfer van-hours = (line-haul hours of the plan as run) − (line-haul hours
> of the same inputs planned with `transfers=()`)

`plan()` already takes `transfers: Sequence[Any] = ()` (`plan.py:185`), so the
counterfactual is one extra call with the same arguments. It reads **zero** on
a day where every transfer rode a leg that would have existed anyway — which
is the honest answer to "how much did transfers add", and the answer an
apportioned reading gets wrong.

**R2a — the predicate I am not using, and why.** The obvious attribution is
"a leg with empty `hub_loads` and non-empty `transfer_ids` is transfer-only".
It is wrong. `plan.py:318` attaches the trip's whole hub manifest to
`index == 0` only:

```python
hub_loads={facility_id: tuple(e["package_id"] for e in aboard)}
if index == 0 else {},
```

So leg 2 of `hub → D1 → D3 → hub` physically carries hub-origin envelopes for
D3 and reports `hub_loads == {}`. The predicate would bill those hours to
transfers. It also ignores `return_ids`, which accumulate homeward and would
make a returns-carrying leg read as transfer-only. Recorded here so the
predicate is not reintroduced as an "optimisation".

**R3 — the denominator is hours *used*, not hours available.** The share is of
`pickup_hours + linehaul_hours`, matching `capacity.check`'s own
`vans=Check("van-hours", van_hours_available, pickup_hours + linehaul_hours)`
(`ddn/simulation/capacity.py:110-111`, read directly — the call spans two
lines and the used-side is the second). A share of *available* hours would move
when a van's shift changes and nothing about transfers did. The numerator is a
subset of `linehaul_hours`, which is already inside the denominator — stated
so nobody later adds it a second time.

**R4 — what a deprecated trip-level event means once events are per-leg.**
§5.3 has one departure and one return per trip; §13.2 has one per leg. Taking
the old names to mean the circuit's endpoints: trip-level `departed` = the
**first** leg departed, trip-level `arrived` = the **last** leg arrived (the
van is home). Any other reading makes `arrived` advance envelopes at depots
the van has not reached.

---

## 4. Steps

### Step 0 — pin the `peak_day` baseline (before any T9 code)

Run the `peak_day` fixture at `7bb9222` and commit its numbers as assertions.
4.1 is unverifiable once Step 2 touches allocation, and no ordering recovers
it afterwards. Test-only; no production file changes.

Also pin the diff base for T9's closing "every file touched across Tasks 7–9":
T7 begins at `75fa17c`, so the range is **`c6ca0bd..HEAD`**.

### Step 1 — `BIKE_RELOCATION_MIN`

A row in `docs/assumptions.md` and the matching constant in
`ddn/assumptions.py`, in the same commit or `tests/test_assumptions.py` fails.

| Key | Value | Spec gap | Provenance |
|---|---|---|---|
| `BIKE_RELOCATION_MIN` | 45 | §4.2 / §12 Q15 (cost basis) and Q2 (relocation time per depot); Q2 leaves rider-vs-bike open, so this key presumes it is the bike | invented |

**The value cell must be a bare integer.** `_documented()` in
`tests/test_assumptions.py:32` matches `TIME` or `raw.isdigit()` and has no
`else` — `45 min` or `45.0` is silently skipped, the mirror test passes, and
the placeholder goes unchecked.

### Step 2 — `allocation.rebalance.decide`

New module `ddn/allocation/rebalance.py`. Consumes `linehaul.Proposal`, which
is why the dependency points this way: `linehaul/` imports nothing from the
rest of `ddn` (its own docstring's reason for returning `Proposal` rather than
`TransferRequest`), so `allocation/` is the importer.

```
decide(proposals, allocation, *, added_leg_minutes, capacity) -> Decision
```

`Decision` carries `transfers: tuple[TransferRequest, ...]`,
`allocation: FleetPlan`, and `by_depot: Mapping[str, str]` recording which arm
resolved each over-full depot. 1.4's invariant is then checkable rather than
asserted in prose: the package ids in `transfers` and the package ids at
depots resolved by reallocation are disjoint sets.

1.5 is a test: given no over-full depot, `proposals == []` and
`decide(...).allocation is allocation`, unchanged and not merely equal.

`TransferRequest` requires `priority` at construction (`records.py:242` raises
without one) and forbids `from_facility_id == to_facility_id`, so `decide`
carries §8.1's score through from the proposal's envelopes.

### Step 3 — §5.3.2's "otherwise" at decline time

Behaviour before metric (3.6). A transfer declined with a non-retriable reason
(`MISSES_DEADLINE`) currently drops out of the queue and leaves its envelope at
the wrong depot. §5.3.2: *"otherwise it is returned to the customer via the
hub"* — apply it at decline time, not only at raise time. Until this exists,
"sent to returns" can only ever report zero.

### Step 4 — metrics

- Rename `transfers_raised` → `transfers_offered` and give `transfers_raised`
  its stated meaning, today's new raises. This **changes expected values**, so
  per CLAUDE.md the commit message names why and names the tests:
  `tests/test_simulation.py:415,432,438,447,511,978,1014,1017` and
  `tests/e2e/test_chain.py:399`. The identity
  `carried + len(declined) == offered` survives the rename, so those rows move
  by name and not by number.
- Add `transfers_returned` (Step 3's channel) and `transfers_deferred`
  (`len(declined) − transfers_returned`: the declines that stay queued).
  **Not** `offered − carried − declined` — `carried + len(declined) ==
  offered` already holds, so that expression is identically zero and the
  metric would report nothing while looking like it reported something. The
  identity this step pins is `offered == carried + deferred + returned`, with
  `offered == yesterday's deferred + raised` closing it across days.
- Add `transfer_van_hours` and `transfer_van_hour_share` per R2/R3, via the
  `transfers=()` counterfactual — **plus the two fixtures that make the
  measurement discriminating.** Neither exists yet, and one alone proves
  nothing:
  - `rides_existing_legs` — every transfer's (from, to) pair is consecutive on
    a circuit the hub-origin plan flies anyway. Marginal hours 0, share 0.
  - `forces_added_leg` — one transfer whose destination sits on no hub-origin
    circuit, so `plan()` adds a leg for it. Marginal hours > 0, share > 0.

  The first separates marginal from apportioned (M6); the second separates a
  real counterfactual from one that cancels itself (M7). Each is blind to the
  other's mutant.
- Extend `simulation/report.py`'s transfer block and `metrics.Metrics`.

### Step 5 — API

- `leg departed` / `leg arrived` in `planning.py`, carrying the leg index.
- Old names accepted for one release. **The deprecation must be observable**:
  a `Deprecation: true` response header (plus `Sunset`), which the test client
  can assert. `warnings.warn` inside a handler is invisible to it.
- `tests/test_api_contract.py` parses §13.2's **paths**, not its event names
  (`paths_in_section_13_2`, line 28) — so event-name drift needs its own pin
  or this hole reopens exactly the way v0.12's Transfers resource did.
- A pin test for 2.4: `GET /linehaul/plans/{id}` returns legs and per-leg
  loads. Expected to pass on first run given `JobState.result: dict | None`;
  if it does, the commit is test-only and says so.

### Step 6 — `peak_day_transfers` regression

A multi-day run asserting **28 envelopes delivered from their new depot on
D+2** and **2 in the return run**. Both numbers are the fixture's own rather
than the prompt's: `TRANSFERS = 30`, `CANNOT_MEET_SLA = 2`, `CARRIED = 28`
(`tests/fixtures/peak_day_transfers.py:53-58`), and the 2 are identified by
`DUE_TODAY_AT = (0, 18)` (`:92`), whose comment already reads "§7.1 then sends
both to the return run". So **no fixture change is needed** — but the 2 can
only appear once **Step 3** exists, because the decline-time return channel is
what produces them. `CARRIED` and `CANNOT_MEET_SLA` are both dead constants
today; of the three only `DUE_TODAY_AT` is read (`:208`). This step is what
makes all of them live.

### Step 7 — front doors and the closing report

`CLAUDE.md:40` and `README.md:36` carry the suite size; `tests/test_front_doors.py`
holds them to it. They move in the same commit as the last test added. Then the
full runner output, the OpenAPI path list, and `git diff --stat c6ca0bd..HEAD`.

---

## 5. Commits

CLAUDE.md forbids refactor-plus-feature across modules in one change, and T9
spans four. One commit per step:

| | Commit | Modules |
|---|---|---|
| 0 | `test(simulation): peak_day's numbers, pinned before T9 moves allocation` | tests |
| 1 | `docs(assumptions): §4.2's relocation time, the placeholder §12 Q15 prices` | docs + config |
| 2 | `feat(allocation): §5.3.2's third trigger decided, not just detected` | allocation |
| 3 | `fix(simulation): §5.3.2's "otherwise" was applied at raise time only` | simulation |
| 4 | `feat(simulation): §11's transfer rows, and §8.3's van-hours share` | simulation |
| 5 | `feat(api): §13.2's leg-level events, with the trip-level names for one release` | api |
| 6 | `test(e2e): peak_day_transfers' 28 on D+2 and 2 in the return run` | tests |
| 7 | `docs: front doors follow the suite` | docs |

---

## 6. Mutation plan

| | Mutation | Killed by |
|---|---|---|
| M1 | `decide` emits transfers *and* moves bikes for the same depot | the disjointness assertion on `Decision.by_depot` |
| M2 | `decide` returns a new `FleetPlan` when no depot is over capacity | 1.5's identity check (`is`, not `==`) |
| M3 | the tie rule flips to (b) | a fixture with equal costs moves the fleet |
| M4 | `BIKE_RELOCATION_MIN` is read as 0 | (b) always wins; no transfer is ever raised by rebalancing |
| M5 | `BIKE_RELOCATION_MIN`'s doc cell is written `45 min` | `test_assumptions` still passes — **this is the mutation that survives today**, and is why Step 1 states the parser constraint |
| M6 | transfer van-hours computed apportioned instead of marginal | `rides_existing_legs`: marginal reads 0, apportioned reads > 0. `forces_added_leg` does **not** kill it — both read > 0 there |
| M7 | the counterfactual is called with the *same* transfers, so the difference cancels | `forces_added_leg`: the true share is > 0 and the mutant reads 0. `rides_existing_legs` does **not** kill it — both read 0 there |
| M8 | `transfers_raised` keeps counting the offered set | a two-day run where day 2 raises nothing still reports raises |
| M9 | the deferred metric counts the returned declines too | `offered == carried + deferred + returned` fails on Step 6's run, where returned is 2. A day with no returns kills nothing, so this row needs Step 3 landed first |
| M10 | Step 3's return channel `continue`s | `transfers_returned` is always 0 and the envelope is in neither pool |
| M11 | trip-level `arrived` maps to the *first* leg | envelopes advance at depots the van has not reached |
| M12 | the deprecation header is dropped | the header assertion (not a `warnings` filter) |
| M13 | a §13.2 event name is renamed in the spec only | Step 5's event-name pin (the path parser does not see it) |

---

## 7. What this does not do

- **Rider-vs-bike is left open.** §12 Q2 asks "whether rider or bike
  relocates"; `BIKE_RELOCATION_MIN` presumes the bike. If the answer is the
  rider, the key is misnamed and the value is probably wrong. Directionally
  the comparison is right; numerically it is arbitrary until Q2 lands.
- **No global optimum.** `decide` resolves each over-full depot against its own
  proposals. §5.3.2 describes a per-depot rule and this implements it; it does
  not search combinations across depots.
- **`peak_day`'s equality is pinned for the fixture's own run**, not proved
  across every caller of it.
- **§12 Q15's other half stays open.** Q15 also asks for historical transfer
  volume, which is what would let §8.3's van-hours check be *sized* rather than
  measured. The share this adds reports what the simulated day did, which is
  not the same thing.
- **`8.3-van-hours-check` stays `PARTIAL`.** `docs/audit/conformance-2026-09-19.md:420`
  downgraded it because the fixture's vans carry `shift_start` and no
  `shift_end`, so `_shift_hours` falls back. The share this adds uses hours
  *used*, so it is unaffected — but the available-hours side is still not real.

---

## 8. Questions

1. **`BIKE_RELOCATION_MIN = 45`** is invented. Is there a real figure, or does
   it stay a placeholder until Q15/Q2 are answered?
2. **Step 4 changes the transfer field set.** `transfers_raised` becomes
   `transfers_offered`, a new `transfers_raised` takes the honest meaning
   (today's new raises), and `transfers_deferred` / `transfers_returned` join
   them — five fields held together by `offered == carried + deferred +
   returned` and `offered == yesterday's deferred + raised`. The alternative
   is to leave the name and fix only the number, which breaks the `carried +
   declined == raised` identity four tests assert. Rename preferred; confirm
   the field set as well as the name, since the second identity is what makes
   `deferred` mean anything.
3. **Step 5's deprecation window** — "one release" against what marker? The
   API's published spec version (`tests/test_api_contract.py`) is the only
   release number this repo has. Take it as: accepted while the published
   version is v0.16, removed at v0.17?

   **Answered, then overtaken.** Built as v0.17 (`planning.SUNSET = (0, 17)`).
   The document then outran the driver app: three v0.17 proposals were queued
   against v0.16 at once, and the bump that closed this window was §5.1.1's
   late-file exception (56e5aee) — nothing to do with line-haul vocabulary.
   `SUNSET` is at `(0, 18)` as a labelled stopgap; retiring the trip-level
   names is Luis's call, not a side effect of an unrelated spec bump.
