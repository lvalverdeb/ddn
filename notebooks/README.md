# Notebooks

Three, in order. They run against the same modules the API and the schedulers
call — nothing here is a parallel implementation, and nothing is illustrative
pseudocode.

| | |
|---|---|
| `01-a-complete-day.ipynb` | §5.6's cycle end to end, then chained so §8.3's gap accumulates |
| `02-stages-in-detail.ipynb` | §4.2 through §5.5 one at a time: what each takes, decides and refuses |
| `03-through-the-api.ipynb` | the same work over §13's endpoints, in-process — no server, no Redis |

```sh
make notebooks     # jupyter lab, with the API extras installed
```

## They are executed by the suite

`tests/test_notebooks.py` runs every notebook in this directory. A notebook is
prose about code, and prose about code fails by agreeing with whatever it was
written against — a renamed argument or a moved module leaves it reading
perfectly and doing nothing. Nothing asserts what the outputs *say*; that would
duplicate the modules' own tests. What is caught is a notebook that no longer
runs, which is the failure that actually happens.

They ship **without stored outputs**, for the same reason: a saved result is a
second copy that goes stale, and it makes a diff unreadable. The suite produces
the outputs; git does not carry them.

## What the numbers are worth

Nothing in here measures the operation. The outcome rates are read off §10's
worked example, the processing throughputs are invented, and the geography is
synthetic — `docs/assumptions.md` says which is which, per row.

Notebook 01 ends on the sharpest case: with no solver in the loop, a chained
day can report more envelopes delivered than the fleet could carry, because
§6.1 will not let `select` trim an envelope that is due today. That figure is
an optimistic bound and the notebook says so. `docs/capacity-finding.md` is the
standing warning about reading a number produced by machinery that was not
measuring what it appears to measure.
