# DDN — the Document Delivery Network, planned on the `vrp` platform

A consolidation hub and six secondary depots, a shared fleet of motorbikes and
vans, 2,500–5,000 document envelopes a day at ten minutes of service each, and a
daily decision about which packages cannot be served. The operation is specified
in [`docs/vrp-problem-definition.md`](docs/vrp-problem-definition.md) (v0.16);
this repository is the attempt to express it.

Travel is road travel everywhere: §3.3 assigns by road distance, the stages
take a gateway-built matrix, and the suite replays a real OSRM table rather
than computing straight lines. That is not a detail — straight-line legs ran
about 40% short and reported §8.3's van check as passing when it breaches.

**Read [`docs/capacity-finding.md`](docs/capacity-finding.md) before drawing any
conclusion from a number produced here.** Its first line is that the headline
figure — 12.8 envelopes per deployed bike, against §7.4's expected 20–30 — **is
not a capacity measurement and must not be used as one.** Nothing binds in that
run; the solver declines on prices rather than time. Arriving at the modules and
finding the number without that framing leads exactly the wrong way.

## Getting it running

`ddn` pins the platform by git tag, from a public repository:

```toml
"vrp-platform[pyvrp] @ git+https://github.com/lvalverdeb/osrm-microservice@v0.4.0"
```

Nothing else is needed to clone it. This README said for a long time that you
needed read access to *two private repositories* — `osrm-microservice` is
public, as the Disclosure section of `CLAUDE.md` says in so many words, and the
two claims sat in the repository together until a CI run made someone check.

```sh
make            # what there is
make test       # 1071 tests, no xfail: every e2e row built, no services needed
make check      # lint and tests
```

`make test` installs what it needs and runs on the host: no Redis, no gateway,
no routing data. That is the whole of what it takes to work on the modules.

To run the §13 service, which does need a queue:

```sh
make bootstrap                          # redis, the API, an Arq worker
make bootstrap DOCKER_HOST=ssh://box    # or on another engine entirely
```

`bootstrap` waits for the API's healthcheck and prints the host the stack is
actually listening on — which is not `localhost` when the daemon is remote.

The token is the same second-repository problem as above, moved into the image
build. `make bootstrap` stops with that explanation rather than letting `uv
sync` fail inside a build layer with a git error that names neither repository.
An ssh-agent works instead, if you have one loaded.

Everything configurable is an environment variable, not a `make` argument.
Compose reads `.env` from the repository root by itself, and `make` picks the
same names up from your shell:

```sh
cp .env.example .env     # DDN_API_PORT, DDN_IMAGE, DOCKER_HOST…
make config              # what is in force, and which daemon it points at
```

`make bootstrap` waits for the API to report healthy before printing its URL.

The daemon is often not the local one, so `DOCKER_HOST` and `DOCKER_CONTEXT`
select it. Every target that needs one checks first and names the endpoint it
tried. `make test` needs none.

The tests need no gateway and no routing data. They cover the data contract, the
line-haul assignment, pickup admission and the return run.

## What is here

| module | §  | what it does | is the *stage* a solver problem? |
|---|---|---|---|
| `allocation/` | 4.2 | bikes per facility, van duty and its taper | no |
| `contract.py` | 9.1 | operation records → `Problem`; priority as class plus score | — (a mapping) |
| `pickups/`    | 5.1 | admission, and the day run on a re-optimisation cadence | no |
| `processing/` | 5.2 | expected ready times; pre-sort at file receipt | no |
| `linehaul/`   | 5.3 | circuits between facilities: hub loads, transfers, returns | no |
| `lastmile/`   | 5.4 | static per-facility batch, the one-day lag's gift | yes |
| `returns/`    | 5.5 | static CVRP, stops aggregated by customer site | yes |
| `solver_adapter/` | 9.2, 7.1 | §9.2 output; §7.1 post-checks; §5.1's problem | — |
| `simulation/` | 5.6, 11 | the D/D+1 cycle chained, §11's metrics, §8.3's checks | — |
| `api/`        | 13  | FastAPI over the modules above; jobs on Arq | — |

That last column is about the *stage*. No module here calls a solver itself —
`contract.py`, `lastmile/` and `returns/` build a `Problem` and the caller
solves it.

**Two of the four operational modules import no part of the routing library at
all.** `linehaul/` imports nothing from `vrp`; `pickups/` imports one name
from `ddn.contract`. §5.3 is an assignment problem and treating it as routing
would invent a route where there is a single leg; §5.1 is a dynamic VRP as a
*problem type*, but the half built so far is admission, which needs no solver
either. This matters for whoever integrates the stages and expects five uniform
routing calls.

Delivery models live in `models/` rather than upstream, because they describe
*this* operation. `contract.load_model` reads them **by path**, since this
repository ships the files and knows where they are; `VRP_MODEL_PATH` is how the
platform's own tooling finds models it did not ship.

## Worked examples

[`notebooks/`](notebooks/) has three: a complete day, the stages one at a time,
and the same work over §13's API. The suite executes all three, because a
notebook nobody runs is documentation that agrees with itself.

```sh
make notebooks     # open them
make nb-clean      # strip saved outputs before committing
```

## CI

`.github/workflows/check.yml` runs `make check` — ruff, then the suite — on
every push and pull request. No secrets: the suite needs no gateway, no Redis,
no routing data and no credentials.

## Where the answers are

- **[`docs/capacity-finding.md`](docs/capacity-finding.md)** — §8.3's three
  capacity checks. **The van check is answered: it binds, and harder than the
  document assumes**, measured from three stages, none of which needed a cost
  ratio. **The delivery check is not yet answerable** and the document explains
  why that is a question about inputs rather than about solvers.
- **[`docs/solver-capabilities.md`](docs/solver-capabilities.md)** — Open
  Question 5, answered by running it. Four yes, one no. The no —
  vehicle-to-depot is not a decision variable — settles §4.2 for two-stage,
  which is the confirmation the document asks for.

## What is open, and on whom

These are **not unfinished code**. They are figures the problem definition has
not supplied, and they cannot be settled in the solver:

- **§8's cost ratio** — what a delivered envelope is worth against a kilometre
  ridden. The blocking one.
- **§3.1's coordinates** — the real customer geography. Depot placement moves
  the delivery answer more than any solver setting does.
- **§7.4** has no service time for a return stop; **§9.2** has no reason code
  for an address with no road path; **§7.1**'s "within its shift" bullet has no
  shift that contains line-haul, which runs overnight by construction. All
  three are described in `capacity-finding.md` §4 with a suggested fix.
- **Open Questions 1, 7, 9 and 13** — mailbag capacity per van, the pickup
  taper, total van fleet, and whether the return run is van-only.

Anyone picking this up and trying to close these by tuning the solver will be
solving the wrong problem.

## The day-one run

`ddn/simulation/on_road.py` runs §5.4 across every facility on real geography. It spawns its
own `osrm-routed` and the platform's compiled gateway, so travel times are road
distances rather than a synthetic grid. It needs two things that live in neither
repository and in no git history — a built gateway with its corpus, and a built
OSRM graph — so it asks for them by name:

```sh
DDN_PLATFORM_REPO=/path/to/osrm-microservice \
DDN_OSRM_GRAPH=/path/to/costa-rica-latest.osrm \
    uv run python -m ddn.simulation.on_road
```

Unset or missing inputs are reported immediately, with the command that builds
each. Nothing else here needs either variable: the modules and the tests run
against the installed `vrp-platform` and no routing data at all.
