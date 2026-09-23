# Plan: §5.3.2's transfer-raising rule

**Status:** proposal. No code written. Requires approval per CLAUDE.md's
multi-module rule — this touches `simulation/`, `model/`, `linehaul/` and the
e2e slices.
**Date:** 23 September 2026
**Found by:** the 17-task completion audit (Task 7's bullets).

---

## 1. What §5.3.2 says, and what the code does

§5.3.2 names **three triggers** and one rule per trigger. Measured against the
tree at `c6ca0bd`:

| | Trigger | State |
|---|---|---|
| 1 | Address correction after a postponement changes the nearest facility | **built** — `simulation.day._raise_transfers` |
| 2 | Zip-centroid pre-sort was wrong, discovered after line-haul (§3.2, §12 Q14) | **absent**; `TransferReason.MISASSIGNMENT` is defined and never emitted |
| 3 | Operational rebalancing | `linehaul.rebalancing` exists, returns `Proposal`, and **has no caller** |

And three gaps in the rule itself:

**A. The "otherwise" half is missing.** §5.3.2: "the envelope is transferred if
it can reach the correct depot before its SLA date; **otherwise it is returned
to the customer via the hub**." `_raise_transfers` reaches that case and
`continue`s — the envelope stays in tomorrow's pool at the wrong depot, with a
corrected address nobody acts on, until §6.1's clock eventually sweeps it. The
document says it goes back *now*.

**B. A declined request is dropped.** §5.3.2: "Requests arising after
departures wait for the next day's plan." `DayReport.transfers_declined`
counts them and `State` has no transfer queue, so they evaporate. Measured: one
van against the 28-transfer fixture declines all 28 with `NO_VAN_LEG`, and
nothing carries one into tomorrow. The envelope stays put — correct — but the
*request* is gone, so the correction has to be rediscovered by being postponed
again for the same reason.

**C. §5.2.6's transfer statuses are never set.** `TRANSFER_REQUESTED` and
`IN_TRANSFER` are in `TRANSITIONS` and nothing writes them, so an envelope
mid-transfer is indistinguishable from one sitting at its depot — and §7.1's
"an envelope in transfer is not routable until it arrives" is enforced only by
`_handover` moving the record, not by a status any check can read.

## 2. What is buildable now, and what is not

**A and B are buildable now.** Both are rules the document states outright,
over data the code already has.

**Trigger 2 is not, and the reason is the same one that stopped `presort`.**
It fires when "address-level geocode places the envelope nearer another depot,
discovered after line-haul" — which needs (i) a late geocode the simulator has
no source for, and (ii) §3.3's road ranking over envelope addresses, which
this repository cannot compute: `tests/matrices.py` holds 94 recorded points
and refuses the rest (see `ddn/processing/sorting.py`'s docstring). §12 Q14 is
open on the rule itself. **Recommendation: leave it out and record why**, the
same way `presort` is recorded.

**Trigger 3 needs a cost basis nobody has supplied.** §5.3.2 makes it "a cost
comparison made by the allocation step" between moving envelopes and moving
motorbikes. `docs/assumptions.md` carries no cost for relocating a bike, §4.2
says only that "relocation takes time and the rider is unavailable during it",
and §8's objective weights are on the never-fill list. **Recommendation: wire
`rebalancing`'s existing `Proposal` output to a caller that records the
proposals, and stop short of the comparison** — which is what e2e-2 §3 asks for
anyway ("proposals go to allocation; this slice does not decide them alone").

## 3. Steps

### Step 1 — §5.3.2's "otherwise": a refused transfer goes back

`ddn/simulation/day.py`. `_raise_transfers` currently returns one list; it
returns two: the requests raised, and the envelopes whose correction cannot
make its deadline. The second list joins `_doorstep`'s `going_back` so §5.5
carries them — at a depot that means the next van leg, at the hub tonight's
run, which is the split e2e-3 §2.5 already draws and `outcomes.record` already
implements.

Stamped with a §6 outcome, not invented: the envelope was postponed, its
address was corrected, and the correct depot is unreachable in time. That is
an SLA failure in substance, so `sla_expired=True` is the honest flag and
`returns.goes_back` already recognises it. **This is the one judgement in the
step and it is called out rather than buried.**

### Step 2 — §5.3.2's "wait for the next day's plan"

`State` gains `transfers: tuple[TransferRequest, ...] = ()`. `run_day` seeds
tonight's plan from `state.transfers + _raise_transfers(...)`, and hands
forward the ones `linehaul.plan` declined for `NO_VAN_LEG` or `OVER_CAPACITY`.

**Not `MISSES_DEADLINE`.** A transfer that cannot arrive in time tonight will
not arrive in time tomorrow either — §9.1's deadline is fixed at raising — so
holding it would be a queue that never empties. Those go back by Step 1's
path instead, which is the same sentence of §5.3.2 answering both.

`_handover` already keeps a declined envelope where it is; it needs no change.

### Step 3 — §5.2.6's statuses, so §7.1 can be checked

`model/outcomes.py` gains the two edges' application:
`Postponed → Transfer requested` when a request is raised, and
`Transfer requested → In transfer` when a leg picks it up. `_handover` sets
`Ready` at the new depot, which it already does positionally.

This is what lets `postcheck` answer §7.1's "an envelope in transfer is not
routable" from a status rather than from an absence, and it is why Step 3 is
in scope rather than left for later: without it Steps 1 and 2 move envelopes
that no check can see moving.

### Step 4 — a caller for `rebalancing`

`ddn/e2e/hub_to_depots/run.py` calls it and puts the proposals on
`PositionedPool`. No cost comparison, no unilateral application — e2e-2 §5
row 5's output row, which is currently produced by nothing.

## 4. Commits

1. `feat(simulation): §5.3.2's other half — a transfer that cannot arrive goes back`
2. `feat(simulation): a declined transfer waits for tomorrow, as §5.3.2 says`
3. `feat(model): §5.2.6's transfer statuses, so §7.1 can read one`
4. `feat(e2e): slice 2 emits the rebalancing proposals it already computes`

## 5. Mutation plan

| | Mutation | Caught by |
|---|---|---|
| T1 | the refused-transfer branch `continue`s again | the envelope is in neither tomorrow's pool nor the return load |
| T2 | `MISSES_DEADLINE` declines are queued too | the queue grows every day on a fixed fixture |
| T3 | `State.transfers` is not seeded into tonight's plan | a declined transfer is never retried |
| T4 | `Transfer requested` is set and never cleared | an envelope is routable while in transfer; `postcheck` reports it |
| T5 | proposals are applied rather than offered | the pool moves without allocation being asked |

Each must fail. T5 is the one that matters most: §5.3.2 gives the decision to
§4.2, and a planner that moved envelopes on its own authority would be making
an allocation decision from inside §5.3.

## 6. What this does not do

- **Trigger 2 (misassignment)** — needs envelope-level road travel this
  repository does not have, and §12 Q14 is open. Recorded, not built.
- **Trigger 3's cost comparison** — needs a relocation cost nobody has
  supplied. Proposals are emitted; the choice is not made.
- **The API** — `POST /transfers` exists; nothing here changes it. The
  separate defect that `runner.linehaul_plan` had no `transit` was fixed in
  `1b621d2`.

## 7. Questions

1. **Step 1's flag.** A transfer refused for its deadline marks the envelope
   `sla_expired=True` so §5.5 carries it. It is an SLA failure in substance —
   the envelope cannot reach a depot that can deliver it in time — but §6 has
   no "transfer refused" outcome and I would rather not invent one. Accept, or
   should this be a v0.16 spec item alongside the depot-capacity one?
2. **Step 2's scope.** Queue `NO_VAN_LEG` and `OVER_CAPACITY`, drop
   `MISSES_DEADLINE` to Step 1's path — or queue all three and let the
   deadline check refuse it again each night? The first is my recommendation;
   the second is closer to a literal reading of "wait for the next day's plan".
3. **Step 3's necessity.** It is the largest of the four and the only one not
   named in Task 7's bullets. It is in scope because Steps 1 and 2 otherwise
   move envelopes no check can observe. Say if you would rather it waited.
