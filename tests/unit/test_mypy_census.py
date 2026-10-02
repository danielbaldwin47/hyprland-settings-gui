"""Every gi-free `ui/` module is type-checked: the mypy `files` list cannot drift.

`pyproject.toml` lists the UI modules that decide rather than draw, because ADR-0011 exempts
only the `gi`-dynamic layer from strict mypy. The list is kept by hand, and it drifted once:
#137 found `ui/pages/tasks.py` and `ui/rows/gesture.py` missing. This test is that lesson as
structure. A module is gi-free when its import closure, followed through `hyprtweaker.*`
imports by reading their source, never reaches `gi`. Imports inside functions count, since
mypy reads them too. A package `__init__.py` that holds only a docstring is a marker, not
code, and is not counted.
"""

from __future__ import annotations

import ast
import tomllib
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
UI = SRC / "hyprtweaker" / "ui"


def module_path(name: str) -> Path | None:
    """The source file of a `hyprtweaker.*` module or package, if there is one."""
    base = SRC.joinpath(*name.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported(path: Path) -> set[str]:
    """Every module name `path` imports, relative imports resolved, submodules included."""
    package = module_name(path)
    if path.name != "__init__.py":
        package = package.rpartition(".")[0]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".")
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, node.module] if node.module else anchor)
            else:
                base = node.module or ""
            names.add(base)
            # `from pkg import mod` imports a module when `mod` is one.
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


@cache
def reaches_gi(name: str) -> bool:
    return _reaches_gi(name, frozenset())


def _reaches_gi(name: str, seen: frozenset[str]) -> bool:
    path = module_path(name)
    if path is None:
        return False
    seen = seen | {name}
    for target in imported(path):
        if target == "gi" or target.startswith("gi."):
            return True
        if (
            target.startswith("hyprtweaker")
            and target not in seen
            and module_path(target) is not None
            and _reaches_gi(target, seen)
        ):
            return True
    return False


def is_marker(path: Path) -> bool:
    """A package `__init__.py` whose body is only a docstring."""
    body = ast.parse(path.read_text(encoding="utf-8")).body
    return path.name == "__init__.py" and all(
        isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) for node in body
    )


def checked_files() -> list[Path]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [ROOT / entry for entry in config["tool"]["mypy"]["files"]]


def test_every_gi_free_ui_module_is_in_the_mypy_files_list() -> None:
    checked = checked_files()
    gi_free = [
        path
        for path in sorted(UI.rglob("*.py"))
        if not is_marker(path) and not reaches_gi(module_name(path))
    ]
    assert gi_free, "the census found no gi-free ui/ module: its import walk is broken"

    missing = [
        str(path.relative_to(ROOT))
        for path in gi_free
        if not any(path == entry or entry in path.parents for entry in checked)
    ]
    assert missing == [], (
        "gi-free ui/ modules missing from [tool.mypy] files in pyproject.toml "
        "(and ADR-0011's list): " + ", ".join(missing)
    )


def test_a_module_that_draws_is_not_counted_gi_free() -> None:
    """The walk follows imports: `config.py` imports `gi` itself, `binds.py` through it."""
    assert reaches_gi("hyprtweaker.ui.pages.config")
    assert reaches_gi("hyprtweaker.ui.shell.window")
    assert not reaches_gi("hyprtweaker.ui.pages.plan")
