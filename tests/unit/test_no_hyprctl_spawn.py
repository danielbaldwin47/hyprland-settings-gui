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

MAY_NAME_HYPRCTL = {
    "importer/dispatchers.py": (
        "the fragments a user's own exec command is scanned for -- read as data about "
        "their config, never issued"
    ),
    "importer/loss.py": "the finding those fragments produce, in its human-readable text",
}
"""Engine modules allowed to name `hyprctl` in a runtime string, and why.

Naming is not spawning. The Importer has to *recognise* `hyprctl dispatch` inside a shell
command a user wrote, because that is the one thing the engine swap breaks which no syntax
check can see. Those modules remain barred from spawning anything -- `MAY_SPAWN` is
unchanged and is what the rule actually protects.
"""

SPAWN_PREFIXES = ("exec", "spawn", "posix_spawn", "popen", "fork", "create_subprocess")
"""Matched by prefix rather than by an explicit list of names.

`os` alone offers `execl/execle/execlp/execv/execve/execvp/execvpe`, seven `spawn*`, two
`posix_spawn*`, `fork`, `forkpty`, `popen` and `system` -- an enumeration is a list of the
holes someone will find. Prefixes are checked only against attributes of `os`, `asyncio`
and `subprocess`, so an unrelated method called `execute` is not swept up."""

SPAWN_NAMES = frozenset({"system", "startfile"})
"""The spawners whose names carry no family prefix."""

SPAWN_MODULES = frozenset({"os", "asyncio", "subprocess", "pty", "multiprocessing"})


def is_spawn(name: str) -> bool:
    return name in SPAWN_NAMES or name.startswith(SPAWN_PREFIXES)


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
    if relative(module) in MAY_NAME_HYPRCTL:
        pytest.skip(MAY_NAME_HYPRCTL[relative(module)])
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


def spawners_in(source: str) -> list[str]:
    """Every way `source` could start a process, by name."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names if alias.name == "subprocess"]
        elif isinstance(node, ast.ImportFrom):
            # `from subprocess import run` -- and `from os import execvp`, which the
            # attribute branch below would never see.
            if node.module == "subprocess":
                found.append("subprocess")
            elif node.module in SPAWN_MODULES:
                found += [alias.name for alias in node.names if is_spawn(alias.name)]
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in SPAWN_MODULES
            and is_spawn(node.attr)
        ):
            found.append(f"{node.value.id}.{node.attr}")
    return sorted(set(found))


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess",
        "from subprocess import run",
        "import os\nos.system('hyprctl reload')",
        "import os\nos.execlp('hyprctl', 'hyprctl', 'reload')",
        "import os\nos.posix_spawnp('hyprctl', [], {})",
        "import os\nos.forkpty()",
        "from os import execvp",
        "import asyncio\nasyncio.create_subprocess_exec('hyprctl')",
    ],
)
def test_the_scan_catches_every_spelling_it_claims_to(source: str) -> None:
    """Guards the guard: a detector with a hole reads exactly like a clean engine."""
    assert spawners_in(source), f"the spawn scan missed {source!r}"


def test_the_scan_leaves_innocent_names_alone() -> None:
    """A method called `execute` is not a process spawn, and a false positive here would
    push a future author to work around the test rather than trust it."""
    assert not spawners_in("class Query:\n    def execute(self): ...\nQuery().execute()")


def test_every_exemption_names_a_module_that_exists() -> None:
    """An exemption for a module that has moved is a hole nobody can see."""
    present = {relative(module) for module in engine_modules()}
    assert set(MAY_NAME_HYPRCTL) <= present, set(MAY_NAME_HYPRCTL) - present
    assert set(MAY_SPAWN) <= present, set(MAY_SPAWN) - present


def test_naming_hyprctl_never_grants_spawning_it() -> None:
    """The two lists are separate on purpose: the rule protects against spawning, and
    reading a string out of a user's config is not that."""
    assert not set(MAY_NAME_HYPRCTL) & set(MAY_SPAWN)


@pytest.mark.parametrize("module", engine_modules(), ids=relative)
def test_only_listed_engine_modules_start_a_process(module: Path) -> None:
    if relative(module) in MAY_SPAWN:
        return

    found = spawners_in(module.read_text(encoding="utf-8"))
    assert not found, (
        f"{relative(module)} spawns processes ({found}) but is not in MAY_SPAWN. "
        f"If it is not Hyprland it is talking to, list it there with a reason."
    )
