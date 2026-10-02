"""`engine/tools.py` is the only route by which the app runs a theming tool or daemon (#233).

`test_no_hyprctl_spawn.py` lists the engine modules that may start a process at all. This
closes the two ways around that list: a module outside `engine/`, which that scan never
reads, and a listed spawner that runs a theming tool or a wallpaper daemon itself instead
of through `find_tool` and `run_tool`, the two functions the test fence and the routes'
empty tool search path govern.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from _support import SRC
from test_no_hyprctl_spawn import MAY_SPAWN, docstring_nodes, spawners_in

from hermetic import REFUSED_TOOLS

PACKAGE = SRC / "hyprtweaker"
ENGINE = PACKAGE / "engine"
SEAM = "engine/tools.py"

GLIB_SPAWNERS = frozenset(
    {
        "Subprocess",
        "SubprocessLauncher",
        "spawn_async",
        "spawn_async_with_pipes",
        "spawn_async_with_fds",
        "spawn_sync",
        "spawn_command_line_async",
        "spawn_command_line_sync",
        "create_from_commandline",
    }
)
"""GLib and Gio's ways to start a program, which the engine scan's `os`/`subprocess` list
does not cover and a UI module would reach for first."""

PATH_LOOKUPS = frozenset({"which", "get_exec_path", "find_program_in_path"})


def _called_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
    return names


def starts_a_process(source: str) -> list[str]:
    """Every way `source` could start a program, the engine scan's and GLib's."""
    return sorted({*spawners_in(source), *(_called_names(source) & GLIB_SPAWNERS)})


def searches_path(source: str) -> list[str]:
    """Every way `source` looks a program up on a search path."""
    return sorted(_called_names(source) & PATH_LOOKUPS)


TOOL_WORD = re.compile(
    r"(?<![\w.-])(" + "|".join(map(re.escape, REFUSED_TOOLS)) + r")(?![\w-])"
)


def tool_names_in_code(source: str) -> list[str]:
    """Theming tool and daemon names in `source`'s runtime strings (prose is skipped)."""
    tree = ast.parse(source)
    prose = docstring_nodes(tree)
    return sorted(
        {
            match
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in prose
            for match in TOOL_WORD.findall(node.value)
        }
    )


def relative(module: Path) -> str:
    return module.relative_to(PACKAGE).as_posix()


OUTSIDE_ENGINE = sorted(
    module for module in PACKAGE.rglob("*.py") if ENGINE not in module.parents
)
SPAWNERS = {f"engine/{name}" for name in MAY_SPAWN}


@pytest.mark.parametrize("module", OUTSIDE_ENGINE, ids=relative)
def test_no_module_outside_the_engine_starts_or_finds_a_program(module: Path) -> None:
    source = module.read_text(encoding="utf-8")

    assert (starts_a_process(source), searches_path(source)) == ([], []), (
        f"{relative(module)} starts or looks up a program. A theming tool or wallpaper "
        "daemon is found and run only through hyprtweaker.engine.tools (find_tool, "
        "run_tool), injected into the code that needs it."
    )


@pytest.mark.parametrize("module", sorted(ENGINE.rglob("*.py")), ids=relative)
def test_only_listed_spawners_look_a_program_up(module: Path) -> None:
    """A path lookup outside a spawner exists only to hand a program to someone who runs it."""
    if relative(module) in SPAWNERS:
        return
    assert searches_path(module.read_text(encoding="utf-8")) == [], (
        f"{relative(module)} looks a program up on a search path; find a tool with "
        "hyprtweaker.engine.tools.find_tool."
    )


@pytest.mark.parametrize("module", sorted(SPAWNERS - {SEAM}))
def test_no_spawner_but_the_seam_names_a_theming_tool_or_daemon(module: str) -> None:
    assert tool_names_in_code((PACKAGE / module).read_text(encoding="utf-8")) == [], (
        f"{module} may start a process and names a theming tool or wallpaper daemon. Run "
        "those through hyprtweaker.engine.tools, which the test fence governs."
    )


def test_the_seam_is_a_listed_spawner() -> None:
    assert SEAM in SPAWNERS
    assert (PACKAGE / SEAM).is_file()


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("from gi.repository import Gio\nGio.Subprocess.new(['swww'], 0)", ["Subprocess"]),
        ("from gi.repository import GLib\nGLib.spawn_async(['swww'])", ["spawn_async"]),
        (
            "from gi.repository import GLib\nGLib.spawn_command_line_async('x')",
            ["spawn_command_line_async"],
        ),
        (
            "from gi.repository import Gio\nGio.AppInfo.create_from_commandline('x', None, 0)",
            ["create_from_commandline"],
        ),
        ("import subprocess\nsubprocess.run(['matugen'])", ["subprocess"]),
    ],
)
def test_the_process_scan_catches_every_spelling_it_claims_to(
    source: str, found: list[str]
) -> None:
    assert starts_a_process(source) == found


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("import shutil\nshutil.which('matugen')", ["which"]),
        ("from shutil import which\nwhich('swww')", ["which"]),
        ("import os\nos.get_exec_path()", ["get_exec_path"]),
        (
            "from gi.repository import GLib\nGLib.find_program_in_path('awww')",
            ["find_program_in_path"],
        ),
    ],
)
def test_the_lookup_scan_catches_every_spelling_it_claims_to(
    source: str, found: list[str]
) -> None:
    assert searches_path(source) == found


def test_the_name_scan_finds_a_tool_in_a_command_and_skips_prose() -> None:
    source = (
        '"""Runs `matugen image` for the wallpaper."""\n'
        "COMMAND = ['swww-daemon', '--format', 'xrgb']\n"
        "LINE = 'exec-once = hyprpaper'\n"
        "OTHER = 'ffmpeg -qscale x'\n"
    )

    assert tool_names_in_code(source) == ["hyprpaper", "swww-daemon"]
