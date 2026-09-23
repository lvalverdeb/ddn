# Plan: T17 — Slice 1, pickups to hub

**Status:** **done** in 56e5aee. Step 0's gate was approved ("approved,
continue with steps 2-5") and Steps 0–5 are all in the tree. Step 5's
regression held: `tests/test_peak_day_baseline.py` and
`tests/test_e2e_handoff.py` are unchanged to the byte.
**Date:** 23 September 2026
**Task:** `docs/claude-code-task-prompts.md:200`

---

## 1. Six of T17's seven bullets are already in the tree

This plan is one item, not seven. The evidence for that claim:

| T17 bullet | Where it lives | State |
| --- | --- | --- |
| λ readiness-weighted insertion (e2e-1 §2.1) | `ddn/pickups/dispatch.py:118` | done |
| λ registered as invented, with sensitivity note | `ddn/assumptions.py:201`, `docs/assumptions.md:132` | done |
| λ = 0 reproduces the planner exactly | `tests/test_pickups.py:293` | done |
| Assembly queue ordered from file receipt | `ddn/processing/readiness.py:64` | done (T12 ordered it; `schedule` made it incremental) |
| Late admission per §5.1.6 | `ddn/pickups/admission.py:112`, `dispatch.py:132,304` | done |
| `ReadyPool` hand-off | `ddn/e2e/handoff.py`, `ddn/e2e/pickups_to_hub/run.py:33` | done |
| Chaining test equals `simulation.run_day`; xfail zero | `tests/test_e2e_handoff.py`; suite reports 0 xfail | done |
| **Late-file exception per e2e-1 A9** | — | **the gap** |

Two notes on the prompt's wording, said rather than glossed:

1. The prompt puts readiness-weighted insertion in `pickups/admission.py`.
   It is in `pickups/dispatch.py`. `admission.assign` is a pure capacity-fit
   loop; λ belongs where the sequencing decision is made, and that is
   `dispatch`. Nothing moves — naming the discrepancy is the whole action.
2. "λ > 0 changes the simulated day → fix `simulation/`, not λ = 0" does not
   arise: the simulator calls the same `dispatch` entry point, and
   `test_pickups.py:293` pins the λ = 0 identity rather than relying on it.

## 2. The gap: A9's late-file exception

e2e-1 §7 row A9 (`docs/e2e/e2e-1-pickups-to-hub.md:94`):

> | A9 | Upload file arrives *after* the bag (exception) | Envelopes processed
> in the slower order; `expected_ready_at` later than a same-time bag with a
> file; flagged late-ready |

Its authority is §5.1.1 (`docs/vrp-problem-definition.md:140`):

> Where the file is late or missing, the bag is processed in the slower order
> (open, key in, then geocode) and its envelopes are flagged as late-ready.

Three things it needs, none of which exist:

1. **No input.** `readiness.schedule(envelopes, arrival_of, …)` takes bag
   arrival times only. Nothing anywhere records that a bag's file was late.
2. **No slower-order path.** `schedule` runs reconcile → assemble → sort, with
   **no geocode stage at all** — deliberately, because §5.2.2
   (`docs/vrp-problem-definition.md:199`) puts geocoding before arrival in the
   usual case and so off the critical path. The exception is precisely the case
   where it is *on* it.
3. **No flag.** §9.1's envelope table (`docs/vrp-problem-definition.md:397`)
   has neither a file-arrival field nor a late-ready flag.

**And `test_a9` covers none of this.** `tests/e2e/test_slice_1_pickups_to_hub.py:309`
asserts that an assembly envelope is ready later than a finished one and that
`requires_assembly` separates them. That is §5.2.3's clean-room rule — true,
tested elsewhere, and a different rule from A9's. The row has been reading as
covered while covering nothing. Replacing it is a change of the test's subject,
not of an expected value, and the reason goes in the commit message per
CLAUDE.md.

## 3. The reading this plan takes

**How lateness reaches `schedule`: a separate argument, not a richer
`arrival_of`.**

`late_files: Container[str]` (bag ids), defaulting empty. The alternative —
widening `arrival_of`'s value to carry both the bag's and the file's arrival —
is truer to the idea that a file *has* a time, but §5.1.1 says "late **or
missing**", and a missing file has no timestamp to carry. A boolean per bag is
the shape the spec actually describes. It also leaves every existing caller
untouched, which makes the regression in Step 5 free rather than argued.

**Stage placement.** The slower order is "open, key in, then geocode", so the
geocode stage runs *after* reconcile and before assembly — a late-file bag
cannot be pre-sorted, so its assembly demand is not known until geocoding ends.

## 4. Steps

### Step 0 — spec proposal, then stop (the gate)

`docs/spec-proposals/v0.17-late-file.md`, covering:

- §9.1 mailbags: `file_received_at`, so a record can say the file was late or
  never came. §9.1 envelopes: the late-ready flag §5.1.1 names.
- §5.2.5: the late-file readiness formula. The existing one is not merely
  silent on this case — it is explicitly conditioned, "With the upload file in
  hand before arrival", so the exception has no formula at all. §5.2.2 gains
  one clause; §5.2.6's first-node annotation is corrected, adding no state and
  no edge.

Two v0.17 proposals are already pending against Draft v0.16
(`v0.17-per-envelope-reason.md` and the transfer-record one from `1ec4595`),
so this joins a queue; Luis may want to apply them together.

**Steps 2–5 do not start until this is approved.** CLAUDE.md: no field added,
renamed or dropped without updating the spec first.

### Step 1 — `GEOCODE_PER_HOUR` (not gated by Step 0)

`ddn/assumptions.py` beside `RECONCILE_PER_HOUR` / `ASSEMBLY_PER_HOUR` /
`SORT_PER_HOUR`, and the matching row in `docs/assumptions.md` with a
sensitivity note. `tests/test_assumptions.py` enforces the pairing.

**This is not a spec change and does not wait on the proposal.** §5.2.5 and
Open Question 4 already list geocoding among the four throughputs they ask
for, so this fills a declared `[TBD]` under CLAUDE.md's standing rule. It is
sequenced first because Step 2 needs it. `docs/assumptions.md:76-77` — "Geocoding
has no rate here" — is retired to the usual case in the same edit.

### Step 2 — the stage

`readiness.schedule` gains `late_files` and the fourth stage. `Readiness` gains
the late-ready flag. Envelopes not in `late_files` take exactly the path they
take today.

### Step 3 — `test_a9`, rewritten against A9

All three clauses pinned:

- slower order — a late-file bag's envelopes pass a stage a normal bag's do not;
- **`expected_ready_at` later than a same-time bag *with* a file** — the
  discriminating clause, and the one the current test only appears to make;
- the flag is set on the late bag and clear on the other.

### Step 4 — the e2e row

`docs/e2e/e2e-1-pickups-to-hub.md` §2.3 and the A9 row are already written;
confirm the implementation matches the wording and amend the doc if not.

### Step 5 — regression

`tests/test_peak_day_baseline.py` is the pin: no fixture supplies a late file,
so every number in it must be unchanged. The chaining test
(`tests/test_e2e_handoff.py`) must still equal `simulation.run_day`. Update
`tests/test_front_doors.py`, `CLAUDE.md` and `README.md` for the new suite size.

## 5. Mutation plan

One mutant, aimed at the clause that has already gone untested once:

- Make the geocode stage cost nothing (`GEOCODE_PER_HOUR` → effectively
  infinite, or the stage a no-op). **`test_a9` must go red.** A test that only
  checks the flag exists would stay green against a stage that adds zero
  seconds — which is how today's `test_a9` came to assert the wrong rule.

## 6. What this does not do

- Does not move λ from `dispatch.py` to `admission.py`. §2 note 1.
- Does not implement geocoding. `processing/` computes expected ready times;
  CLAUDE.md scopes it to that. The stage is a duration, not a geocoder.
- Does not touch the pending v0.17 proposals.

## 7. Questions

1. Approve the Step 0 proposal as its own commit, or batch it with the two
   already pending against v0.16?
2. Should a late-ready envelope be *visible* beyond `processing/` — reported in
   `DayReport`, or counted in `Tally`? A9 asks only that it be flagged. Adding
   a count is a §11 change and is out of this plan unless you want it in.
