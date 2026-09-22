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

Three evasions are open, not closed, and are listed so nobody mistakes a green
run for a guarantee:

* logic moved into `scenario.py`, which needs no imports at all and is
  exempted below because it is a record of inputs;
* `sorted(xs, key=operator.itemgetter("priority"))` or
  `from builtins import sorted as rank` — the call target is an `Attribute` or
  an alias, so a name-based deny-list does not see it;
* `functools.reduce` and `itertools.starmap`, for the same reason.

What the allow-list really buys is that evading it requires writing something
that plainly does not look like a sequence of calls, which a reviewer notices.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SLICES = sorted(p for p in pathlib.Path("ddn/e2e").iterdir()
                if p.is_dir() and not p.name.startswith("_"))

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
    "ddn", "ddn.allocation", "ddn.lastmile", "ddn.linehaul", "ddn.model",
    "ddn.model.outcomes", "ddn.pickups", "ddn.processing", "ddn.returns",
    "ddn.solver_adapter", "ddn.e2e.handoff", "__future__",
}


def _runners():
    return [(slice_dir.name, module)
            for slice_dir in SLICES
            for module in sorted(slice_dir.glob("*.py"))
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
            if field in {"annotation", "returns"} and isinstance(value, ast.AST):
                skip.update(id(n) for n in ast.walk(value))
    return [n for n in ast.walk(node) if id(n) not in skip]


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
            assert (source in PUBLIC_SEAMS
                    or source.startswith("ddn.e2e.")), f"{module}: {source}"
            assert not any(a.name.startswith("_") for a in node.names), (
                f"{module}: imports a private name from {source}; promote it "
                "to its owning module instead")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in PUBLIC_SEAMS, f"{module}: {alias.name}"


@pytest.mark.parametrize("name, module", _runners(),
                         ids=lambda v: getattr(v, "name", v))
def test_no_runner_reaches_the_simulator(name, module):
    """`ddn.simulation` is the thing the chain compares against.

    A slice importing it would put the same code on both sides of that
    comparison, and the agreement would prove nothing at all.
    """
    for node in ast.walk(_tree(module)):
        sources = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                   else [a.name for a in node.names]
                   if isinstance(node, ast.Import) else [])
        assert not any(s == "ddn.simulation" or s.startswith("ddn.simulation.")
                       for s in sources), f"{module} imports the simulator"


def test_every_slice_has_a_runner_to_check():
    """A guard on the guard: three slices, and this file found all of them.

    Without it, renaming `run.py` would empty the parametrisation and every
    test above would pass by having nothing to look at.
    """
    checked = {name for name, _ in _runners()}

    assert checked == {"pickups_to_hub", "hub_to_depots", "depot_delivery"}
    assert len(_runners()) >= 6, "each slice ships __init__.py and run.py"
