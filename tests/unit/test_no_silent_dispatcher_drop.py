"""No dispatcher grammar returns `None` without a Loss finding, enforced by reading the code.

`map_bind` drops the whole bind when its dispatcher will not translate. That is the right
call -- a bind with no action is worse than none -- and what makes it safe is the note:
without one, the user's line leaves the config and the Loss report has nothing to find it
by, which contradicts the Importer's own "nothing is lost" contract (#131).

`TestDispatcherRejections` in `test_importer_mapping.py` sweeps the same rule by *behaviour*,
driving every dispatcher with argument shapes chosen to make it refuse. That is the stronger
check where it reaches, because it proves the note survives the trip out through
`translate_dispatcher`. What it cannot prove is coverage: a rejection no probe happens to
reach passes it silently, and the probe list is a hand-written thing that rots.

So this test reads the module instead. Every `return None` inside a grammar has to be
accompanied by a note, and a new grammar that forgets one fails here on the day it is
written rather than on the day a user's config meets it. Same idea as
`test_no_hyprctl_spawn.py`: a rule nobody can quietly opt out of is a test, not a comment.
"""

from __future__ import annotations

import ast

import pytest
from _support import SRC

DISPATCHERS = SRC / "hyprtweaker" / "engine" / "importer" / "dispatchers.py"

#: Helpers that file the finding themselves, so a `return` of their result is accounted for.
REPORTING_CALLS = frozenset({"_reject", "note", "add"})

#: Shared parsers that note and hand back an empty result their caller turns into `None`.
#: `_resize_params` is the only one: its three grammars all read `if not fields: return
#: None`, and the note belongs where the argument was rejected rather than in each caller.
NOTING_PARSERS = frozenset({"_resize_params"})


@pytest.fixture(scope="module")
def module() -> ast.Module:
    return ast.parse(DISPATCHERS.read_text(encoding="utf-8"), filename=str(DISPATCHERS))


def _returns_optional_call(node: ast.FunctionDef) -> bool:
    """Whether this function is shaped like a Grammar: `-> DispatcherCall | None`."""
    return isinstance(node.returns, ast.BinOp) and "DispatcherCall" in ast.unparse(node.returns)


def _grammars(module: ast.Module) -> list[ast.FunctionDef]:
    """Only the grammars that *can* refuse: `-> DispatcherCall` without the `| None` half
    cannot drop a bind, and `_reject` is the machinery under test rather than a subject."""
    return [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and node.name != "_reject"
        and _returns_optional_call(node)
    ]


def _names_called(node: ast.AST) -> set[str]:
    """Every function or method name called anywhere under `node`."""
    names: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Name):
            names.add(function.id)
        elif isinstance(function, ast.Attribute):
            names.add(function.attr)
    return names


def _bare_none_returns(node: ast.FunctionDef) -> list[ast.Return]:
    """`return None` / `return` statements that are not `return _reject(...)`.

    Nested functions are included on purpose: the `_resize_active`/`_pixel` factories put
    the whole grammar inside a closure, and their returns are the ones under review.
    """
    returns: list[ast.Return] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Return):
            continue
        if child.value is None or (
            isinstance(child.value, ast.Constant) and child.value.value is None
        ):
            returns.append(child)
    return returns


def test_the_scan_actually_finds_the_grammars(module: ast.Module) -> None:
    """Guards every assertion below: a walk that matched nothing proves nothing."""
    found = _grammars(module)

    assert len(found) > 12, f"only {len(found)} grammars matched -- the AST shapes have moved"
    names = {node.name for node in found}
    assert {"_signal", "_move_cursor", "_set_prop", "_mouse"} <= names


def test_every_grammar_that_can_return_none_reports_why(module: ast.Module) -> None:
    """The rule itself: a grammar with a bare `return None` must also raise a finding.

    Deliberately whole-function rather than per-branch. Matching a note to the exact `if`
    it guards means reimplementing control flow in a test, and the false negative that
    admits -- a function that notes on one branch and not another -- is caught by the
    behavioural sweep, which drives each branch for real.
    """
    silent = []
    for grammar in _grammars(module):
        if not _bare_none_returns(grammar):
            continue
        called = _names_called(grammar)
        if called & REPORTING_CALLS or called & NOTING_PARSERS:
            continue
        silent.append(f"{grammar.name} (line {grammar.lineno})")

    assert not silent, (
        "these dispatcher grammars can return None without a Loss finding, so the bind is "
        "dropped and the report says nothing about it: " + ", ".join(silent)
    )
