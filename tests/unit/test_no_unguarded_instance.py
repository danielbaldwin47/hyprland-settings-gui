"""No test reaches a live Hyprland except through the desktop guard (#201), enforced.

The owner's rule is that nothing an agent runs touches their desktop session, and an
agent's shell names that session in `HYPRLAND_INSTANCE_SIGNATURE`. Two routes inherit it:
`Instance.current()`, and a `hyprctl` spawned with the inherited environment (including
anything that runs `hyprctl` itself, such as `tools/gen_schema.py`). So every module under
`tests/` is scanned, the Harness tier too, though it sits outside `testpaths`:

- any use of `Instance.current` fails;
- a spawn naming `hyprctl` or `gen_schema` fails unless it passes `env=<x>.env`: the
  environment of a `GuardedInstance` (`tests/integration/harness/guard.py`) or of a
  `NestedHyprland`. `env=os.environ`, `env=dict(os.environ)` and no `env=` all fail.

What the scan cannot see is where an `.env` came from; the guard and `NestedHyprland` are the
only things in `tests/` that build one. Call sites the scan cannot clear are listed in
`MAY_SPAWN_HYPRCTL` with a reason each, by function, never by file.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]

#: Third-party rice fixtures: data, not test code (and excluded from lint for the same reason).
EXCLUDED = (TESTS / "corpus",)

MAY_SPAWN_HYPRCTL = {
    "integration/harness/nested.py::live_instances": (
        "`hyprctl instances -j` reads the `hyprland.lock` files under "
        "$XDG_RUNTIME_DIR/hypr and connects to no compositor: hyprctl answers it before "
        "it looks at any signature (hyprctl/src/main.cpp at v0.56.2, 'instances is "
        "HIS-independent'). Its env is the launch environment, signature already dropped"
    ),
}
"""Spawn sites cleared by reading them rather than by the scan, and why."""

TARGETS = ("hyprctl", "gen_schema")
SPAWN_MODULES = frozenset({"os", "asyncio", "subprocess"})
SPAWN_PREFIXES = ("exec", "spawn", "posix_spawn", "popen", "create_subprocess")
SUBPROCESS_CALLS = frozenset(
    {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
)


def _is_spawn(func: ast.expr, imported: set[str]) -> bool:
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        module, name = func.value.id, func.attr
        return module in SPAWN_MODULES and (
            name in SUBPROCESS_CALLS or name == "system" or name.startswith(SPAWN_PREFIXES)
        )
    return isinstance(func, ast.Name) and func.id in imported


def _names_target(call: ast.Call) -> bool:
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(target in node.value for target in TARGETS)
        for argument in call.args
        for node in ast.walk(argument)
    )


def _guarded_env(call: ast.Call) -> bool:
    return any(
        keyword.arg == "env"
        and isinstance(keyword.value, ast.Attribute)
        and keyword.value.attr == "env"
        for keyword in call.keywords
    )


class _Scan(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.imported: set[str] = set()
        self.offences: list[tuple[str, str]] = []

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _enter

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in SPAWN_MODULES:
            self.imported |= {alias.asname or alias.name for alias in node.names}

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            node.attr == "current"
            and isinstance(node.value, ast.Name)
            and node.value.id == "Instance"
        ):
            self.offences.append((self._where(), "Instance.current"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        spawn = _is_spawn(node.func, self.imported) and _names_target(node)
        if spawn and not _guarded_env(node):
            self.offences.append((self._where(), "hyprctl spawn without a guarded env"))
        self.generic_visit(node)

    def _where(self) -> str:
        return ".".join(self.scope) or "<module>"


def offences_in(source: str) -> list[tuple[str, str]]:
    """(enclosing function, what it does) for every unguarded reach in `source`."""
    scan = _Scan()
    scan.visit(ast.parse(source))
    return scan.offences


def scanned_modules() -> list[Path]:
    modules = [
        path
        for path in sorted(TESTS.rglob("*.py"))
        if not any(excluded in path.parents for excluded in EXCLUDED)
    ]
    assert any(path.parent.name == "integration" for path in modules), "scan lost a tier"
    return modules


def relative(module: Path) -> str:
    return module.relative_to(TESTS).as_posix()


@pytest.mark.parametrize("module", scanned_modules(), ids=relative)
def test_no_test_module_reaches_a_compositor_around_the_guard(module: Path) -> None:
    offences = [
        (where, what)
        for where, what in offences_in(module.read_text(encoding="utf-8"))
        if f"{relative(module)}::{where}" not in MAY_SPAWN_HYPRCTL
    ]
    assert not offences, (
        f"{relative(module)} reaches a live Hyprland around the desktop guard: {offences}. "
        "Take the instance from the `guarded_hyprland` fixture (tests/integration/conftest.py) "
        "or `harness.guarded(...)`, and spawn hyprctl with its `.env`."
    )


@pytest.mark.parametrize(
    "source",
    [
        "from hyprtweaker.engine.ipc import Instance\nInstance.current()",
        "import subprocess\nsubprocess.run(['hyprctl', 'version'])",
        "import os, subprocess\nsubprocess.run(['hyprctl', 'reload'], env=dict(os.environ))",
        "import os, subprocess\nsubprocess.run(['hyprctl'], env=os.environ)",
        "from subprocess import check_output\ncheck_output(['hyprctl', '-j', 'monitors'])",
        "import sys, subprocess\nsubprocess.run([sys.executable, 'tools/gen_schema.py'])",
        "Session(spawn=s, connect=Instance.current)",
    ],
    ids=[
        "instance-current",
        "bare-hyprctl",
        "dict-environ",
        "os-environ",
        "imported-spawner",
        "gen-schema",
        "current-as-default",
    ],
)
def test_the_scan_catches_each_way_around_the_guard(source: str) -> None:
    """Guards the guard: a detector with a hole reads exactly like a clean tree."""
    assert offences_in(source), f"the scan missed {source!r}"


def test_the_scan_clears_a_spawn_aimed_by_a_guarded_env() -> None:
    clean = "import subprocess\nsubprocess.run(['hyprctl', 'version'], env=guarded.env)"
    bare = "import subprocess\nsubprocess.run(['hyprctl', 'version'])"
    assert offences_in(clean) == []
    assert offences_in(bare) == [("<module>", "hyprctl spawn without a guarded env")]


def test_every_exemption_names_a_function_that_exists() -> None:
    """An exemption for a function that has moved is a hole nobody can see."""
    for entry in MAY_SPAWN_HYPRCTL:
        path, _, function = entry.partition("::")
        tree = ast.parse((TESTS / path).read_text(encoding="utf-8"))
        names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        assert function in names, entry
