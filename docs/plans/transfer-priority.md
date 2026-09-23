# Plan: §5.3.2's priority rule under shortage (T8)

**Status:** proposal. No code written. Requires approval — this touches
`linehaul/`, `model/` and §9.1, and one part needs a spec change first.
**Date:** 23 September 2026
**Found by:** the 17-task completion audit (Task 8's bullets).

---

## 1. What §5.3.2 says

> **Priority.** When van capacity or van-hours are short, hub-origin loads and
> transfers compete. Both are ranked by the same priority score as delivery
> (§8.1); a transfer for an SLA-today envelope is a hard inclusion, like an
> SLA-today delivery.

Three claims: they **compete**, they are **ranked together by §8.1**, and an
SLA-today transfer is a **hard inclusion**. Measured against `b188dcf`, none of
the three is implemented.

## 2. What the code does

**Transfers are taken in the order the caller listed them.**
`circuit.choose` (`circuit.py:146`) loops `for transfer in transfers` and
declines `OVER_CAPACITY` once the 500 kg is gone. So which transfers ride is
decided by list position — and `simulation.day` now passes
`[*state.transfers, *raised]`, which makes it "yesterday's first", a rule
nobody chose and §5.3.2 does not state.

**Nothing is ranked by priority anywhere in the package.** The only `priority`
in `linehaul/` is inside `rebalancing`, which I added for e2e-2 §3.

**Hub-origin loads never yield.** They board wholesale
(`plan.py:288`, everything Ready by departure) and their weight is counted
first; transfers then get the remainder. So they do not compete — hub-origin
wins by construction, whatever the scores.

**A facility with no van rolls whole** (`plan.py:236`), which is right for
`NO_VAN` — nothing departs, so nothing is carried. But e2e-2 row B4 says
"Dropped loads are the lowest-priority", which implies a partial drop when
van-hours are short, and that case does not exist.

**There is no SLA-today test on a transfer at all.** `TransferRequest.makes`
checks arrival against the deadline; §7.1's hard inclusion is a different
rule and has no code.

## 3. The blocker: §9.1's transfer table carries neither field

§5.3.2 says rank transfers "by the same priority score as delivery (§8.1)",
and §7.1 makes an SLA-today transfer a hard inclusion. §9.1's transfer-request
table (`docs/vrp-problem-definition.md:437-443`) lists eight fields and
**neither `priority` nor `sla_date` is among them**, so a planner given a
`TransferRequest` cannot apply either rule.

Three ways out, and this is the decision the rest depends on:

**(a) Add the two fields to §9.1.** A spec proposal, same shape as
`Facility.unload_min` in `docs/spec-proposals/v0.15.md` — a datum the code
needs and the table does not declare. `deadline` is already documented as
`min(receiving depot's release, SLA date)`, so the SLA date is *in* the record
in compressed form and cannot be recovered from it: a deadline equal to the
release tells you nothing about the SLA.

**(b) Pass a lookup.** `plan(..., priority_of: Callable[[str], float])`. No
spec change, and it keeps `linehaul` importing nothing — but it puts a
side-table beside a record that §9 calls "the only schema definition", and
every caller has to remember it or silently get no ranking.

**(c) Rank on the envelope records the planner already has.** Works for
hub-origin loads, which are §9.1 envelope dicts carrying `priority` and
`sla_date`. Does nothing for transfers, which is half the rule.

**Recommendation: (a), proposed as v0.16 alongside the depot-capacity item, and
nothing in §5.3.2's Priority paragraph built until it lands.** (b) is the
tempting shortcut and it is how `Tally.disputed` came to be read and never
written: a field the code needs, supplied out of band, unnoticed when absent.

## 4. What can be built without the spec change

One thing, and it is worth having on its own:

**Determinism.** `choose` declining in caller order means the plan depends on
list position. Until the ranking exists, the order should at least be *stated*
— sorted by `(deadline, transfer_id)`, so two runs of one night decline the
same transfers and `simulation.day`'s `[*state.transfers, *raised]` stops
silently meaning "yesterday's first". That is a property this repository
already insists on elsewhere (`select` sorts `(-priority, package_id)` for
exactly this reason) and it needs no new datum.

## 5. Steps, once §9.1 carries the fields

1. **`model/records.py`** — `TransferRequest` gains `priority: float` and
   `sla_date: date | None`, both required at construction. Absent is refused,
   not defaulted: a transfer that ranks as zero is one that never rides.
2. **`linehaul/circuit.py`** — `choose` partitions before it loops: SLA-today
   transfers first as hard inclusions, then the rest by `(-priority,
   transfer_id)`. `OVER_CAPACITY` then means "the circuit was full when this
   one's turn came", which is what §9.2's reason should mean.
3. **`linehaul/plan.py`** — hub-origin loads and transfers ranked *together*
   against the 500 kg, which is the "compete" in §5.3.2. This is the largest
   piece and the only one that changes what hub-origin loads do today.
4. **`e2e` row B4** — currently asserts only that the reason is `no van`. It
   gains the row's first clause, "dropped loads are the lowest-priority", and
   its second, "every SLA-today transfer and envelope is carried".

## 6. Mutation plan

| | Mutation | Caught by | Killed by |
|---|---|---|---|
| P1 | `choose` reverts to caller order | two runs with the list reversed decline different transfers | `test_transfers.py::test_the_order_of_the_caller_s_list_no_longer_decides` (+2) |
| P2 | the SLA-today partition is dropped | an SLA-today transfer is declined while a lower-priority one rides | `test_transfers.py::test_a_transfer_due_at_the_next_release_rides_whatever_it_scores`; also `e2e/test_slice_2_hub_to_depots.py::test_b4` since step 4 |
| P3 | hub-origin loads board before ranking | a high-priority transfer is declined behind a low-priority envelope | `test_transfers.py::test_a_higher_scoring_transfer_takes_the_seat_from_a_hub_origin_envelope`; `e2e/…::test_b4` |
| P4 | `TransferRequest` defaults `priority` to 0 | a transfer with no score ranks last instead of being refused | `test_transfers.py::test_a_transfer_without_a_score_is_refused_not_ranked_last` |
| P3b | envelopes ranked worst-first among themselves | the dropped set is not the lowest-scoring one | `e2e/…::test_b4`; `test_transfers.py::test_a_transfer_that_would_overload_the_van_is_declined` |

P3b is not one this plan predicted. It was added while writing step 4, because
P3 alone does not prove B4's *first* clause discriminates: P3 moves the boundary
between the two kinds of load, and that clause is about the order within one
kind. Every row above was run and observed to fail the named tests; none is
recorded on the strength of the code reading as if it would.

## 7. What this does not do

- **Van-hours.** §5.3.2's "or van-hours are short" is the other half of the
  shortage, and §8.3 computes van-hours over a whole day rather than a night.
  Ranking under *weight* is what this plan covers; ranking under *hours* needs
  the night-level figure e2e-2 §5 row 7 also asks for and nothing produces.
- **Rebalancing's cost comparison.** Still blocked on a relocation cost
  nobody has supplied (see `docs/plans/transfer-raising.md` §2).

## 8. Questions

1. **(a), (b) or (c)?** My recommendation is (a) with a v0.16 proposal, and
   nothing built until it lands — but that means T8 stays open until you have
   time to rule on the spec change, and (b) would let it close this week at
   the cost of a side-table.
2. **Is §4 worth doing now?** Sorting `choose` by `(deadline, transfer_id)` is
   half a day's work, needs no decision, and removes a silent dependence on
   list order. It is not §5.3.2's rule and should not be described as it.
3. **Step 3's blast radius.** Making hub-origin loads yield to transfers
   changes what the simulator carries on §10's day, so the pinned figures in
   `tests/test_simulation.py` move. That is expected and correct — but say if
   you would rather see the measurement before approving, in which case I will
   build it behind a flag and report the delta first.
