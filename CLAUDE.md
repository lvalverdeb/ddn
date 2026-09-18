# Document Delivery VRP Integration

## What this repo is

Integration between our document delivery operation and an existing VRP solver:
a consolidation hub and six secondary depots, a shared fleet of motorbikes and
vans, 2,500–5,000 document envelopes a day at ten minutes of service each, and a
daily decision about which packages cannot be served.

The operational problem is fully defined in
[`docs/vrp-problem-definition.md`](docs/vrp-problem-definition.md) (Draft v0.10).
**Read it before any substantive task.** Section numbers below refer to that
file; cite them the same way in docstrings, comments and commit messages.

## Source of truth

- **§2 Glossary** — use these terms in code, comments and tests. "Envelope" is
  the delivery unit; "mailbag" is the pickup unit. §2 glosses the entity as
  "Envelope (package)", so the two name the same concept in prose — but **not in
  identifiers**: `package_id` is the customer-supplied key for an envelope and
  nothing else. Do not rename it to `envelope_id`; §9.1 fixes the name.
- **§7.1 Hard constraints** — invariants. Every solver output must be validated
  against all of them before it is accepted. A violation is a failing test, never
  a warning. `ddn/solver_adapter/postcheck.py` is that validation, in two halves
  because §7.1 is: `check_route_constraints` answers the seven bullets one solve
  can see, `check_day_constraints` the four that span stages (one vehicle per
  *day*, arrival before dispatch, van unloaded before line-haul). It delegates to
  `vrp.verify`'s seventeen invariants rather than restating them.
- **§9 Data contract** — the only schema definition. Do not add, rename or drop
  fields without updating the spec first and saying so.
- **§5.2.6 Envelope lifecycle** — the state machine, from `Requested` through
  `Ready` to `{Delivered | Rejected | Returned | Postponed}`. Transitions not
  listed there are illegal. Only **Ready** envelopes are solver inputs for
  delivery routing.
- **§8 Objectives** — priority is a numeric score with tier offsets (§8.1).
  **Never apply an SLA weighting on top of it:** §6.1 makes priority the solver's
  sole ranking signal because it already incorporates SLA proximity, and SLA date
  = today is a hard constraint, not a weight.
- **§7.4's implication** — 35 envelopes × 10 minutes is 350 minutes against an
  8-hour shift, so **shift duration, not envelope count, binds**, and route
  duration must stay a hard constraint. Relaxing it changes the problem, not the
  solver.

## Working assumptions

Where the spec says `[TBD]`, the value is an open input. Two rules follow, and
they are not the same rule:

**Placeholders live in one place, labelled.** `docs/assumptions.md` carries every
stand-in value with the gap it fills and where it came from — *document* (stated
in §10, which the spec itself calls illustrative), *derived* (computed from
figures the spec does supply), or *invented* (chosen with no source, sensitivity
noted). `ddn/assumptions.py` mirrors that table and is what code reads;
`tests/test_assumptions.py` fails if the two drift apart. **Never hard-code a
`[TBD]` value in a module** — `returns.py:52`'s `SERVICE_SECONDS = 600` is the
example of what that costs: an invented service time, invisible for two days,
now documented as `RETURN_STOP_MIN` but not yet wired up.

**Three gaps get no placeholder at all**, because filling them would do damage a
label cannot undo:

- **§8's objective weights** — what a delivered envelope is worth against a
  kilometre ridden. Not tagged `[TBD]` anywhere, and the blocking unknown. A run
  made with a stand-in ratio is what produced the figure the next section exists
  to disown.
- **§11's eleven metric targets.** A target is a commitment, not an input;
  inventing "SLA compliance ≥ 98%" fabricates a customer promise.
- **§9.2's reason code for an unreachable address.** Needs a spec change, not a
  value. Currently invented at `lastmile.py:45`.

Still open and still on operations, none of them settleable in the solver:
**§3.1's real coordinates** (the placeholders are synthetic Costa Rican towns —
depot placement moves the delivery answer more than any solver setting does),
**§7.4's** service times, **§7.1's** van mailbag capacity, and **Open Questions
1, 7, 9 and 13**. `capacity-finding.md` §4 carries two more with suggested fixes.

Naming a value as a placeholder does not make a number measured on it a
measurement. The next section is the standing case.

## Read before quoting a number

**[`docs/capacity-finding.md`](docs/capacity-finding.md) first.** The headline
figure in this repository — 12.8 envelopes per deployed bike, against §7.4's
expected 20–30 — **is not a capacity measurement and must not be used as one.**
Nothing binds in that run; the solver declines on prices rather than time.
Arriving at the modules and finding the number without that framing leads exactly
the wrong way.

## Architecture (§5)

One module per stage, plus shared model and solver adapter. **The package layout
below is a target; the repository is currently flat.** Both columns are listed so
that ownership is unambiguous while the migration is incomplete — put new code
where the target says, and when you move a stage, move one boundary at a time.

| target | today | § | what it owns | is the *stage* a solver problem? |
|---|---|---|---|---|
| `model/` | `ddn/model/` | 9.1, 5.2.6, 3.3 | entities, lifecycle, nearest-facility | — |
| `solver_adapter/` | `ddn/solver_adapter/`, `ddn/contract.py` | 9.1, 7.1, 9.2 | operation records → `Problem`; priority as class plus score; §9.2 output; §7.1 post-checks | — (a mapping) |
| `allocation/` | `ddn/allocation/` | 4.2, 4.3 | bikes per facility, §7.2's relocation cost, the van pickup/line-haul split and its taper | no |
| `pickups/` | `ddn/pickups/` | 5.1 | admission, and the day on §5.1.5's cadence with §5.1.7's exceptions | no |
| `processing/` | `ddn/processing/` | 5.2 | expected-ready-time computation and §5.2.4's pre-sort **only** — not reconciliation or assembly themselves | no |
| `linehaul/` | `ddn/linehaul/` | 5.3 | van-to-depot assignment against morning release times | no |
| `lastmile/` | `ddn/lastmile/` | 5.4, 8 | per-facility delivery routing, and which envelopes are left when capacity is short | yes |
| `returns/` | `ddn/returns/` | 5.5 | end-of-day return run; static CVRP, stops aggregated by customer site | yes |
| `simulation/` | `ddn/simulation/` | 5.6, 10, 11 | the day end to end, its metrics and §8.3's checks | — |
| `api/` | `ddn/api/` | 13 | FastAPI service layer: thin handlers, job queue, event validation | — |

Every target package now exists. `ddn/contract.py` still holds §5.4's mapping
and `solver_adapter.last_mile` delegates to it rather than restating it, so
those two are one boundary until the move; `ddn/simulation/` is still
`ddn/simulation/on_road.py`.

That last column is about the *stage*, not the module: **no module here calls a
solver.** They build a `Problem` and the caller solves it. Two of the four
operational modules import no part of the routing library at all — `linehaul`
imports nothing from `vrp`, `pickups` one name from `ddn.contract`. §5.3 is an
assignment problem and routing it would invent a route where there is a single
leg. Do not add a solver call to make the stages look uniform.

**Stack:** Python 3.13; `vrp-platform[pyvrp]` pinned by git tag at
`osrm-microservice@v0.4.0` (the solver is PyVRP, reached through the platform's
adapter); FastAPI and Pydantic for §13; Arq on Redis for §13.1's job queue, with
`fakeredis` in tests so the suite still needs no external service; pytest; ruff
0.16.4.

## The API (§13)

`ddn/api/` depends on every module above it and **nothing depends on it.** §13
opens by saying what it is: "a thin layer: it accepts inputs, starts jobs,
reports status and returns results. Stage logic lives in the modules of §5 and
is never implemented inside request handlers." A handler that computes
something is a handler doing another module's job.

- **Solver work is a job, never a request.** §13.1: any endpoint that invokes
  the solver returns `202` with a job id. **Never FastAPI `BackgroundTasks`** —
  §13.1 rules them out by name, because a process that dies takes the work with
  it and nobody can ask what happened to it.
- **Status is never set directly.** Callers append events and §5.2.6 validates
  the transition; an illegal one is `409` with the current state.
- **`Idempotency-Key` is required** on ingest and event endpoints, and a replay
  returns the original response rather than acting twice.
- **Every routing result carries its §7.1 violation list**, empty on success.
- **Overrides and outcome events record actor and time** (§13.1, §8.2).
- The two §13.3 scheduled processes call the same internal functions as the
  handlers. They do not call the API over HTTP.

## Commands

```sh
make test                    # 587 tests; no gateway, no Redis, no routing data
make check                   # and ruff
make bootstrap               # §13's stack: redis, the API, an Arq worker
```

`Makefile` is the front door and `make` lists it. The suite runs on the host
with no services, which is a promise worth keeping: anyone who clones this
repository runs everything with one command and no infrastructure. Docker is
for the *service*, which needs a queue — `compose.yaml` runs Redis, the API and
a worker, and the worker is a separate container because §13.1 will not have
solver work inside a request.

Building the image needs `GH_TOKEN` (or a loaded ssh-agent): it clones the
second private repository, and `make bootstrap` says so by name rather than
letting the build fail with a git error that mentions neither. `DOCKER_HOST`
and `DOCKER_CONTEXT` select the daemon — often not the local one — and
`make config` prints every variable in force and which daemon it points at.
Configuration is environment variables rather than `make` arguments, so one
name reaches compose, the container and the host command; `.env.example` lists
them and compose reads `.env` by itself. Both failures are guarded and both
guards name the thing that is missing, which is the same courtesy
`simulation/on_road.py` extends about its two inputs.

`uv sync` needs read access to **two** private repositories: this one and
`lvalverdeb/osrm-microservice`. Without it you get a git authentication failure
that does not name the cause.

`ddn/simulation/on_road.py` (§5.4 across every facility, on real geography) is the only
thing here that needs routing data, and it asks for it by name:

```sh
DDN_PLATFORM_REPO=/path/to/osrm-microservice \
DDN_OSRM_GRAPH=/path/to/costa-rica-latest.osrm \
    uv run python -m ddn.simulation.on_road
```

## How to work here

- **Plan before code on anything touching more than one module**: propose the
  change, wait for approval. Note that `contract.py` is imported by most of the
  others, so this catches more changes than it looks like.
- **Every module ships with tests.** The worked example in §10 is the integration
  fixture: `tests/fixtures/peak_day.py` builds it deterministically from §10's
  own figures — keep them in sync, and derive fixture values from the document
  rather than from what the code emits. Tests must keep running without a
  gateway or routing data.
- **Prefer small, reviewable commits.** Do not refactor across modules in the
  same change as a feature.
- **When the spec is ambiguous, quote the section, state the interpretation you
  are taking, and continue.** Do not silently choose.
- **Never invent solver capabilities.** If a required feature is not confirmed in
  [`docs/solver-capabilities.md`](docs/solver-capabilities.md), implement the
  documented fallback from the spec and flag it. Answered there (Open Question
  5): dynamic stop insertion **yes**, native due-date handling **yes**, locked
  assignments **yes**, ready-time constraints on stops **yes** —
  **flexible vehicle-to-depot assignment no**, which settles §4.2 as a two-stage
  problem with allocation upstream of the solver.
- **DDN is downstream of the platform and the gateway; neither knows it exists.**
  Nothing here may require a change in `vrp-platform` or in the OSRM gateway.
  What lives here is what the platform deliberately does not model: the §6
  outcome lifecycle across a day boundary, the §9.1 mapping, the §5.2 cut-off
  partition, the §4.2 daily fleet distribution.
- **Delivery models live in `models/`, not upstream**, because they describe
  *this* operation. `contract.load_model` reads them **by path** — this
  repository ships the files and knows where they are. `VRP_MODEL_PATH` is how
  the platform's own tooling finds models it did not ship; a different problem.

## Disclosure

This repository is private. `osrm-microservice` is **public**, and findings from
this work get written up there — so a private repo is not sufficient care on its
own. That is not hypothetical: a sentence of the problem definition reached the
public repo verbatim and shipped in two tags before anyone checked visibility.

**What is established.** Luis confirmed on **17 September 2026** that the
customer has explicitly approved publication of work derived from the problem
definition. Consent is not an open question and does not need re-litigating.

**What is not established, and must not be invented.** Who granted that
approval, when, and where it is written down. Nobody has supplied those, so
nothing in this repository may cite them. If you need the detail, ask Luis; do
not reconstruct it from this paragraph. The line above records a confirmation
from him, which is weaker than the approval itself and should not be quoted as
if it were the approval.

> **To complete:** replace this block with the approver, the date, and where the
> approval is recorded, once Luis supplies them.

**The rule, which is narrower than the approval permits.** Before anything
derived from this work reaches the public repo, remove what fingerprints the
operation — facility counts, fleet counts, stop counts, the industry, and any
sentence quoted from the problem definition. Keep the technical finding: none of
them need the specifics to be true, and removing the provenance has so far
improved the prose rather than weakened it.

**Scrubbing one document does not scrub a term while a sibling still carries
it.** Doc 40 had the industry removed while doc 41 kept it in a sentence about
the same operation, and two "a consuming repository" mentions a month apart were
enough to rejoin them. Check the objective, not the diff: grep the public tree
for the term after editing, not just the lines you touched.

**Scrub forward; do not rewrite public history.** A force-push announces the
text far more loudly than leaving it does.

** never modify a test's expected values without stating the reason in the commit message 
** never mock the solver outside tests/
** always paste the full test runner output at the end of a task.