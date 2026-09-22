# CLAUDE.md — Document Delivery VRP Integration

## What this repo is
Integration between our document delivery operation and an existing VRP solver.
The operational problem is fully defined in `docs/vrp-problem-definition.md`
(**Draft v0.15**; `tests/test_api_contract.py` holds the API's published
version to it).
Read it before any substantive task. Section numbers below refer to that file.

## Source of truth
- §2 Glossary: use these terms in code, comments, and tests. "Envelope" is the delivery unit; "mailbag" is the pickup unit. Do not use "package" and "envelope" interchangeably in identifiers — `package_id` is the customer-supplied key for an envelope, nothing else.
- §7.1 Hard constraints: these are invariants. Every solver output must be validated against all of them before it is accepted. A violation is a failing test, never a warning.
- §9 Data contract: the only schema definition. Do not add, rename, or drop fields without updating the spec first and saying so.
- §5.2.6 Envelope lifecycle: the state machine. Transitions not listed there are illegal.
- §8 Objectives: priority is a numeric score with tier offsets (§8.1). Never apply an SLA weighting on top of it; SLA date = today is a hard constraint, not a weight (§6.1).

## Working assumptions
Where the spec says `[TBD]`, use the values in `docs/assumptions.md` and reference them from a single config module — never hard-code them. If `docs/assumptions.md` does not exist yet, create it with clearly labelled placeholder values and stop to ask before proceeding.

## Architecture (from §5)
One module per stage, plus shared model and solver adapter:
- `model/` — entities and lifecycle from §9 and §5.2.6
- `allocation/` — daily fleet split across facilities and van duty (§4.2), upstream of the solver
- `pickups/` — dynamic mailbag pickup routing, van-only (§5.1)
- `processing/` — hub readiness: expected-ready-time computation only; we do not implement reconciliation/assembly themselves (§5.2)
- `linehaul/` — van circuits between facilities: hub → depot loads, inter-depot transfers, returns, against morning release times (§5.3)
- `lastmile/` — per-facility delivery routing (§5.4)
- `returns/` — end-of-day return run (§5.5)
- `solver_adapter/` — translation to/from the existing solver's format; constraint post-checks (§7.1)
- `simulation/` — day simulator and metrics (§5.6, §10, §11)
- `api/` — FastAPI service layer per §13: thin handlers, job queue, event validation; depends on all modules above, none depend on it

Stack: Python [version TBD], FastAPI, [queue library TBD: Arq / RQ / Celery / Dramatiq], [solver name and version TBD], pytest.

API rules (§13): solver-invoking endpoints are async jobs returning 202; never use FastAPI BackgroundTasks for solver work; lifecycle changes only via event endpoints; Idempotency-Key required on ingest and events; handlers contain no routing logic.

## Commands

```sh
make test       # 971 tests (9 xfail: e2e rows not built); no gateway, no Redis, no routing data
make check      # and ruff
make bootstrap  # §13's stack: redis, the API, an Arq worker
```

`tests/test_front_doors.py` holds this number to the suite that produces it, and
README.md to the same number, because it went stale three times in one sitting.

## How to work here
- Plan before code on anything touching more than one module: propose the change, wait for approval.
- Every module ships with tests. The worked example in §10 is the integration fixture; keep `tests/fixtures/peak_day.*` in sync with it.
- Prefer small, reviewable commits. Do not refactor across modules in the same change as a feature.
- When the spec is ambiguous, quote the section, state the interpretation you are taking, and continue. Do not silently choose.
- Never invent solver capabilities. If a required feature (dynamic insertion, ready-time constraints, locked assignments, flexible depot assignment) is not confirmed in `docs/solver-capabilities.md`, implement the documented fallback from the spec and flag it.
