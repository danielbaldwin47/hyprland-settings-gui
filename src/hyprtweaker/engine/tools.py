"""The one route by which the app finds and runs another program (#233).

A theming tool (matugen, wallust, noctalia, DMS) or a wallpaper daemon (swww, awww,
hyprpaper) is found with `find_tool` and run with `run_tool`, and nowhere else. Callers
take both as injected callables that default to these, so a test hands in a recording
fake; `tests/unit/test_no_hyprctl_spawn.py` keeps every other engine module from starting
a process, and `tests/unit/test_tool_seam.py` keeps the rest of the app from doing it.

Programs are looked up on the **tool search path**: `HYPRTWEAKER_TOOL_PATH` when it is set,
else `PATH`. The test suite, the widget probe route and `tools/sandbox.py` set it to an
empty directory of their own, so a real tool is never found there, installed or not: a
real matugen or wallust runs its `post_hook`, which reloads whichever compositor it
finds, and on a developer's machine that is their own desktop.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

TOOL_PATH_ENV = "HYPRTWEAKER_TOOL_PATH"


@dataclass(frozen=True, slots=True)
class ToolRun:
    """What one finished run of a tool printed and returned."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class ToolRefused(Exception):
    """`run_tool` was asked to start something that is not a found tool; nothing started."""


class ToolTimedOut(Exception):
    """A tool ran past its timeout and was killed."""

    def __init__(self, argv: tuple[str, ...], timeout: float) -> None:
        super().__init__(f"{Path(argv[0]).name} did not finish within {timeout:g} s")
        self.argv = argv
        self.timeout = timeout


def _search_dirs(environ: Mapping[str, str]) -> list[str]:
    raw = environ[TOOL_PATH_ENV] if TOOL_PATH_ENV in environ else environ.get("PATH", "")
    # An empty or relative entry is read against the working directory, which is no
    # place anyone put a tool on purpose.
    return [entry for entry in raw.split(os.pathsep) if os.path.isabs(entry)]


def tool_search_path(environ: Mapping[str, str]) -> str:
    """Where tools are looked up: `HYPRTWEAKER_TOOL_PATH` when set (even empty), else `PATH`.

    Only absolute entries are kept.
    """
    return os.pathsep.join(_search_dirs(environ))


def find_tool(name: str, environ: Mapping[str, str] = os.environ) -> Path | None:
    """The tool called `name` on the tool search path, or None when it is not there."""
    if os.sep in name:
        raise ValueError(f"find_tool takes a program's name, not a path: {name!r}")
    search = tool_search_path(environ)
    if not search:
        return None
    found = shutil.which(name, path=search)
    return None if found is None else Path(found)


def run_tool(
    argv: Sequence[str], *, timeout: float, environ: Mapping[str, str] = os.environ
) -> ToolRun:
    """Run a found tool to completion, with no shell, and capture what it printed.

    `argv[0]` must be an absolute path directly inside a directory of the tool search path,
    as `find_tool` returns it; anything else raises `ToolRefused` before a process starts.
    The environment is passed through unchanged. Raises `ToolTimedOut` after killing a tool
    that overruns `timeout` seconds, and `OSError` when the program cannot be executed.
    """
    command = tuple(argv)
    if not command:
        raise ToolRefused("run_tool was given an empty command")
    program = command[0]
    allowed = {os.path.normpath(entry) for entry in _search_dirs(environ)}
    if not os.path.isabs(program) or os.path.dirname(os.path.normpath(program)) not in allowed:
        search = tool_search_path(environ) or "(empty)"
        raise ToolRefused(
            f"refused to run {program}: it is not in a directory of the tool search path "
            f"({TOOL_PATH_ENV} or PATH: {search}). Find the program with find_tool and run "
            "the path it returns."
        )
    try:
        completed = subprocess.run(
            command,
            env=dict(environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ToolTimedOut(command, timeout) from None
    return ToolRun(
        argv=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
