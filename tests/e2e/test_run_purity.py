"""What a slice runner may contain, read as syntax.

The rule the slice documents are built on is that `ddn/e2e/*/run.py`
**orchestrates existing modules and computes nothing**: every decision belongs
to the §5 module that owns it, and a runner is a sequence of calls. A runner
that filtered or ranked would be a second implementation of a stage, and the
chain test would then be comparing two things that were never meant to differ.

**This check is weaker than it sounds, and saying so is part of it.** It is a
syntactic allow-list over each file's own source. It is not a proof and not an
import-graph check: `ddn/lastmile/plan.py` already reaches
`ddn.solver_adapter.output` for its reason strings, so a runner importing
`select` touches `solver_adapter` transitively and nothing here sees it.

An adversarial pass found four holes in the first version of this file, all of
which are now closed and are recorded because the *shape* of each recurs:

* `from ddn import simulation` passed all nineteen tests. `"ddn"` was a seam,
  and the simulator check read `node.module` but never the imported names. One
  ordinary-looking import line handed a runner the whole simulator, which is
  the single thing this file exists to prevent.
* every annotation position was an unchecked code slot, because the walk
  skipped the annotation subtree entirely and a module-level `AnnAssign`
  annotation executes without `from __future__ import annotations`.
* `sorted`, `filter`, `__contains__`, `__getitem__` and `dict.get` spell every
  forbidden construct with nodes the allow-list permits.
* logic moved into a sub-package or an `_`-prefixed directory was never parsed
  at all, because the glob was neither recursive nor inclusive.

**What remains open, and it is not small.** `exec` and `__import__` are denied
by name, but this is still a syntactic check on each file's own source: it is
not an import-graph check, and `ddn/lastmile/plan.py` already reaches
`ddn.solver_adapter.output`, so a runner importing `select` touches that
package transitively and nothing here sees it. `ddn/e2e/handoff.py` is checked
under a relaxed rule (it holds the `.of()` constructors) and it imports
`Tally` from `ddn.simulation.metrics`, so `ddn.e2e` does depend on the
simulator's package — just not in a way a runner can reach.

A green run here means "nobody has written the evasion yet", not "there is no
logic in the runners".
"""

from __future__ import annotations

import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SLICES = sorted(p for p in (ROOT / "ddn" / "e2e").iterdir() if p.is_dir()
                and p.name != "__pycache__")

#: Everything a sequence of calls needs, and nothing that decides anything.
#: The forbidden nodes are forbidden *by omission*, which is the point: a new
#: syntax form is refused until someone looks at it. Absent on purpose:
#: `ListComp`, `DictComp`, `SetComp`, `GeneratorExp` (filtering and ranking),
#: `Lambda` (a function smuggled in as an argument), `If`/`IfExp` (a branch is
#: a decision), `For`/`While` (iteration is a stage's job), `Compare` and
#: `BoolOp` (a test), `BinOp` (arithmetic — `allocation.capacity_for` exists
#: because of this rule), `Subscript` and `Attribute`-on-subscript (reaching
#: into a structure rather than asking it).
ALLOWED = {
    ast.Module, ast.Import, ast.ImportFrom, ast.alias,
    ast.FunctionDef, ast.arguments, ast.arg,
    ast.Expr, ast.Assign, ast.AnnAssign,
    ast.Name, ast.Store, ast.Load, ast.Return,
    ast.Call, ast.keyword, ast.Attribute, ast.Constant,
    ast.Tuple, ast.List, ast.Dict, ast.Starred,
}

#: A runner may reach these and nothing else. `ddn.simulation` is absent by
#: design: a slice that imported the simulator would be compared against
#: itself, which is the one thing `tests/e2e/test_chain.py` exists to rule out.
PUBLIC_SEAMS = {
    "ddn.allocation", "ddn.lastmile", "ddn.linehaul", "ddn.model",
    "ddn.model.outcomes", "ddn.pickups", "ddn.processing", "ddn.returns",
    "ddn.solver_adapter", "ddn.e2e.handoff", "__future__",
}

#: `from ddn import lastmile` is how this repository imports a stage, so bare
#: `ddn` cannot simply be banned — but `from ddn import simulation` is the same
#: syntax and once passed every test here. The rule is therefore on the
#: *names*: each one must spell a seam when joined to the package.
PACKAGE = "ddn"

#: Names that spell a forbidden construct out of permitted nodes.
#: `sorted(filter(...), key=...)` is a ranking; `xs.__contains__(y)` is a
#: `Compare`; `d.get(k, fallback)` is an `If`; `list.__getitem__(xs, slice())`
#: is a `Subscript`. `exec`, `eval` and `__import__` are arbitrary code.
DENIED_NAMES = {
    "sorted", "filter", "map", "min", "max", "sum", "any", "all", "zip",
    "reduce", "exec", "eval", "compile", "open", "__import__", "getattr",
    "setattr", "slice", "reversed", "enumerate",
}
DENIED_ATTRS = {
    "__contains__", "__getitem__", "__lt__", "__gt__", "__mul__", "__add__",
    "sort", "get", "pop", "setdefault",
}


def _runners():
    """Every module under every slice package, at any depth.

    `glob` rather than `rglob`, and skipping `_`-prefixed directories, meant a
    sub-package full of comprehensions was never opened. Both are fixed here,
    and `test_every_slice_has_a_runner_to_check` guards the result.

    `scenario.py` is exempt because it is a record of inputs — not because it
    imports nothing, which an earlier version of this docstring claimed and
    which is false of all three. That exemption is the largest hole left here:
    a filtering loop in a scenario would not be seen.
    """
    return [(slice_dir.name, module)
            for slice_dir in SLICES
            for module in sorted(slice_dir.rglob("*.py"))
            if module.name != "scenario.py"]


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _executable(node):
    """Every node that runs, with the type annotations left out.

    `tuple[PositionedPool, TransferOutcomes]` is a `Subscript` and so is
    `pool["D1"]`. The first is a return type and the second is reaching into a
    structure instead of asking it — so annotations are skipped here rather
    than `Subscript` being allowed everywhere, which would let the second in
    to make room for the first.
    """
    skip = set()
    for parent in ast.walk(node):
        for field, value in ast.iter_fields(parent):
            if (field in {"annotation", "returns"}
                    and isinstance(value, ast.AST)
                    and _is_type_expression(value)):
                skip.update(id(n) for n in ast.walk(value))
    return [n for n in ast.walk(node) if id(n) not in skip]


def _is_type_expression(node):
    """Whether this annotation is a type and not a place to hide code.

    Skipping the whole subtree made every annotation an unchecked slot — and
    without `from __future__ import annotations` a module-level one *runs*. So
    only the shapes a type actually takes are skipped: a name, an attribute
    chain, a string, `None`, and subscripts and tuples of those.
    """
    return all(isinstance(n, (ast.Name, ast.Attribute, ast.Subscript,
                              ast.Tuple, ast.Constant, ast.Load, ast.Index,
                              ast.BitOr, ast.BinOp))
               for n in ast.walk(node))


@pytest.mark.parametrize("name, module", _runners(),
                         ids=lambda v: getattr(v, "name", v))
def test_a_runner_is_a_sequence_of_calls_and_nothing_else(name, module):
    """No loop, no comprehension, no branch, no arithmetic, no lambda."""
    offending = sorted({type(node).__name__ for node in _executable(_tree(module))
                        if type(node) not in ALLOWED})

    assert not offending, (
        f"{module} contains {', '.join(offending)}. A runner calls; the "
        f"decision belongs in the §5 module that owns it, with tests there.")


@pytest.mark.parametrize("name, module", _runners(),
                         ids=lambda v: getattr(v, "name", v))
def test_a_runner_imports_only_a_public_seam(name, module):
    """And never an underscored name, which is what forces a seam to be promoted.

    Six seams were moved out of `ddn/simulation/day.py` for exactly this rule.
    Without it a runner could reach `_attempt` or `_position` directly, and the
    chain would be measuring argument threading rather than decomposition.
    """
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.ImportFrom):
            source = node.module or ""
            if source == PACKAGE:
                for alias in node.names:
                    assert f"{PACKAGE}.{alias.name}" in PUBLIC_SEAMS, (
                        f"{module}: `from ddn import {alias.name}` — not a "
                        "seam. `from ddn import simulation` is the reason this "
                        "reads the names and not just the module.")
            else:
                assert (source in PUBLIC_SEAMS
                        or source.startswith("ddn.e2e.")), f"{module}: {source}"
            assert not any(a.name.startswith("_") for a in node.names), (
                f"{module}: imports a private name from {source}; promote it "
                "to its owning module instead")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != PACKAGE, (
                    f"{module}: bare `import ddn` reaches every submodule")
                assert alias.name in PUBLIC_SEAMS, f"{module}: {alias.name}"


@pytest.mark.parametrize("name, module", _runners(),
                         ids=lambda v: getattr(v, "name", v))
def test_no_runner_reaches_the_simulator(name, module):
    """`ddn.simulation` is the thing the chain compares against.

    A slice importing it would put the same code on both sides of that
    comparison, and the agreement would prove nothing at all.
    """
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.ImportFrom):
            # Both halves: `from ddn.simulation import x` AND the name in
            # `from ddn import simulation`, which the first version missed.
            base = node.module or ""
            sources = [base] + [f"{base}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Import):
            sources = [a.name for a in node.names]
        else:
            continue
        assert not any(s == "ddn.simulation" or s.startswith("ddn.simulation.")
                       for s in sources), f"{module} reaches the simulator"


@pytest.mark.parametrize("name, module", _runners(),
                         ids=lambda v: getattr(v, "name", v))
def test_a_runner_does_not_spell_a_decision_with_a_builtin(name, module):
    """`sorted`, `filter`, `.get(k, fallback)` and the dunders are decisions.

    The node allow-list cannot see them: they are `Call` on `Name` or
    `Attribute`, which a sequence of calls needs. Measured on the first version
    of this file, a complete second §8 capacity cut — ranking, filtering and a
    per-facility branch — passed all nineteen tests using nothing else.
    """
    for node in ast.walk(_tree(module)):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Name):
                assert target.id not in DENIED_NAMES, (
                    f"{module}: `{target.id}(...)` ranks, filters or branches; "
                    "that belongs in the §5 module that owns the decision")
            elif isinstance(target, ast.Attribute):
                assert target.attr not in DENIED_ATTRS, (
                    f"{module}: `.{target.attr}(...)` spells a decision")


def test_every_slice_has_a_runner_to_check():
    """A guard on the guard: three slices, and this file found all of them.

    Without it, renaming `run.py` would empty the parametrisation and every
    test above would pass by having nothing to look at.
    """
    checked = {name for name, _ in _runners()}

    assert checked == {"pickups_to_hub", "hub_to_depots", "depot_delivery"}
    assert len(_runners()) >= 6, "each slice ships __init__.py and run.py"
