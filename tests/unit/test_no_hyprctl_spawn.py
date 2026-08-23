"""The Engine never spawns `hyprctl` (ADR-0010), enforced rather than intended.

A process spawn is ~20 ms against a 0.4 ms socket round-trip. That gap is the difference
between a slider that tracks the pointer and one that does not, and it is the kind of thing
that creeps back in one convenient `subprocess.run(["hyprctl", ...])` at a time -- in the
importer, in a monitors page, anywhere someone needs one value in a hurry. So the rule is a
test: no engine module may name `hyprctl` in code, and only the modules listed here may
spawn a process at all.

Prose in docstrings and comments is not code and is not scanned -- the engine cites
`hyprctl -j descriptions` and `hyprctl -j getoption` all over, because those are the
protocol's names for things.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from _support import SRC

ENGINE = SRC / "hyprtweaker" / "engine"

MAY_SPAWN = {
    "writer/syntax.py": "the `luac -p` syntax gate (ADR-0010 step 3)",
}
"""Engine modules allowed to start a process, and why.

Adding to this is a real decision, not a formality: whatever goes here must be a tool that
is not Hyprland. Talking to the compositor has exactly one route, and it is the socket.
"""

SPAWN_NAMES = frozenset(
    {
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnv",
        "spawnvp",
        "posix_spawn",
        "create_subprocess_exec",
        "create_subprocess_shell",
    }
)


def engine_modules() -> list[Path]:
    modules = sorted(ENGINE.rglob("*.py"))
    assert modules, f"no engine modules found under {ENGINE} -- the scan is broken"
    return modules


def relative(module: Path) -> str:
    return module.relative_to(ENGINE).as_posix()


def docstring_nodes(tree: ast.AST) -> set[int]:
    """The ids of every string that is documentation rather than a value.

    Any bare string *statement* qualifies, not just the leading one: this codebase
    documents dataclass fields and module constants with the string-after-assignment form,
    and those are as much prose as a function's docstring. Neither is reachable at runtime.
    """
    return {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


@pytest.mark.parametrize("module", engine_modules(), ids=relative)
def test_no_engine_module_names_hyprctl_in_code(module: Path) -> None:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    skip = docstring_nodes(tree)

    offenders = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in skip
        and "hyprctl" in node.value.lower()
    ]
    assert not offenders, (
        f"{relative(module)} has 'hyprctl' in a runtime string: {offenders}. "
        "The Engine talks to Hyprland over the sockets in hyprtweaker.engine.ipc."
    )


@pytest.mark.parametrize("module", engine_modules(), ids=relative)
def test_only_listed_engine_modules_start_a_process(module: Path) -> None:
    if relative(module) in MAY_SPAWN:
        return

    tree = ast.parse(module.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name == "subprocess"]
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            found.append("subprocess")
        elif isinstance(node, ast.Attribute) and node.attr in SPAWN_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in SPAWN_NAMES:
            found.append(node.id)

    assert not found, (
        f"{relative(module)} spawns processes ({sorted(set(found))}) but is not in "
        f"MAY_SPAWN. If it is not Hyprland it is talking to, list it there with a reason."
    )
