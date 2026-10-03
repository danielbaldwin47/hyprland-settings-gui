"""The app finds no path from the user's home but through `ConfigPaths` (#233).

Spec #153's code writes another tool's config (`~/.config/matugen/...`) and keeps copies
under `~/.local/state`. Derived from `ConfigPaths.config_home` and `.state_home`, those
paths follow `XDG_CONFIG_HOME`, `XDG_STATE_HOME` and `HOME`, which every test, the widget
probe and the sandbox point at a directory of their own. Derived from `Path.home()` or
`~` directly, they would still follow `HOME` today, and stop the day someone resolves the
home another way. So the rule is a scan: no module under `src/hyprtweaker/` looks the home
up itself, outside the functions listed here.

Prose is not scanned: docstrings cite `~/.config/hypr` and `Path.home()` all over.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _support import SRC
from test_no_hyprctl_spawn import docstring_nodes

PACKAGE = SRC / "hyprtweaker"

MAY_LOOK_UP_HOME = {
    ("engine/paths.py", "_xdg_dir"): (
        "the XDG fallback (`~/.config`, `~/.local/state`) that every `ConfigPaths` path, "
        "and so every path outside the App dir, derives from"
    ),
    ("engine/tools.py", "_search_dirs"): (
        "a `~/...` PATH entry is read against the HOME of the environment mapping the lookup "
        "is given -- the one the tool then runs with, fenced in every test -- never the "
        "process's own (ruling A8 of the #148 review)"
    ),
}
"""(module, function) pairs allowed to look the home up, and why."""

HOME_DIR_CALLS = frozenset(
    {
        "expanduser",
        "getpwuid",
        "getpwnam",
        "get_home_dir",
        "get_user_config_dir",
        "get_user_data_dir",
        "get_user_state_dir",
        "get_user_cache_dir",
    }
)
"""Calls that answer with the home, or a directory under it, whatever they are given."""


def _is_home(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "HOME"


def _names_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Name) and node.id == "environ") or (
        isinstance(node, ast.Attribute) and node.attr == "environ"
    )


def _lookup(node: ast.AST) -> str | None:
    """How `node` looks the home up, or None when it does not."""
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if (
            name == "home"
            and isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "Path"
        ):
            return "Path.home()"
        if name in HOME_DIR_CALLS:
            return f"{name}()"
        if name == "getenv" and node.args and _is_home(node.args[0]):
            return 'getenv("HOME")'
        if (
            name == "get"
            and isinstance(func, ast.Attribute)
            and _names_environ(func.value)
            and node.args
            and _is_home(node.args[0])
        ):
            return 'environ.get("HOME")'
    if isinstance(node, ast.Subscript) and _names_environ(node.value) and _is_home(node.slice):
        return 'environ["HOME"]'
    return None


def home_lookups(source: str) -> list[tuple[str, int, str]]:
    """Every home lookup in `source`, as (enclosing function, line, how)."""
    tree = ast.parse(source)
    prose = docstring_nodes(tree)
    found: list[tuple[str, int, str]] = []

    def visit(node: ast.AST, function: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            function = node.name
        if id(node) not in prose and (how := _lookup(node)) is not None:
            found.append((function, getattr(node, "lineno", 0), how))
        for child in ast.iter_child_nodes(node):
            visit(child, function)

    visit(tree, "<module>")
    return found


def package_modules() -> list[Path]:
    modules = sorted(PACKAGE.rglob("*.py"))
    assert modules, f"no modules found under {PACKAGE} -- the scan is broken"
    return modules


def relative(module: Path) -> str:
    return module.relative_to(PACKAGE).as_posix()


@pytest.mark.parametrize("module", package_modules(), ids=relative)
def test_no_module_looks_up_the_home_outside_configpaths(module: Path) -> None:
    lookups = [
        (function, line, how)
        for function, line, how in home_lookups(module.read_text(encoding="utf-8"))
        if (relative(module), function) not in MAY_LOOK_UP_HOME
    ]
    assert not lookups, (
        f"{relative(module)} looks up the home itself: {lookups}. Derive the path from "
        "ConfigPaths.config_home or .state_home (or take it as a parameter), so a test, the "
        "widget probe and the sandbox keep it off the owner's real home."
    )


@pytest.mark.parametrize(
    ("source", "how"),
    [
        ("from pathlib import Path\nTARGET = Path.home() / '.config'", "Path.home()"),
        ("import os\nos.path.expanduser('x')", "expanduser()"),
        ("from pathlib import Path\nPath('x').expanduser()", "expanduser()"),
        ("from os.path import expanduser\nexpanduser(name)", "expanduser()"),
        ("import os\nos.getenv('HOME')", 'getenv("HOME")'),
        ("import os\nos.environ['HOME']", 'environ["HOME"]'),
        ("import os\nos.environ.get('HOME', '/')", 'environ.get("HOME")'),
        ("import pwd, os\npwd.getpwuid(os.getuid()).pw_dir", "getpwuid()"),
        ("from gi.repository import GLib\nGLib.get_user_config_dir()", "get_user_config_dir()"),
    ],
)
def test_the_scan_catches_every_spelling_it_claims_to(source: str, how: str) -> None:
    """Guards the guard: a planted lookup in a scratch module is found, line and all."""
    assert [found[2] for found in home_lookups(source)] == [how]


def test_the_scan_names_the_function_a_lookup_is_in() -> None:
    source = 'def config_dir():\n    """Under `Path.home()`."""\n    return Path.home()\n'

    assert home_lookups(source) == [("config_dir", 3, "Path.home()")]


def test_the_scan_leaves_prose_and_other_environments_alone() -> None:
    """Docstrings cite `~`, and a parse environment's own `HOME` is data, not ours."""
    source = (
        '"""Reads `~/.config/hypr` via `Path.home()`."""\n'
        "def expand(self, path):\n"
        '    """`~` is the parse environment\'s HOME."""\n'
        '    return self.environment.get("HOME") + path[1:]\n'
    )

    assert home_lookups(source) == []


def test_every_exemption_names_a_function_that_still_looks_the_home_up() -> None:
    """An exemption for code that moved is a hole nobody can see."""
    for module, function in MAY_LOOK_UP_HOME:
        source = (PACKAGE / module).read_text(encoding="utf-8")
        assert function in {found[0] for found in home_lookups(source)}, (module, function)
