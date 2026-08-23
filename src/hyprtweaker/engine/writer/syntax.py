"""The `luac -p` syntax gate (ADR-0010, step 3).

Every reload starts by syntax-checking the main file, and a syntax error aborts the whole
reload -- not just the offending Module, the *whole config*. So the writer proves its own
output parses before a single byte reaches disk. A gate failure is a writer bug by
construction: the user never types Lua here.

`Hyprland --verify-config` is the heavier gate and stays where it belongs -- migration time
(ADR-0009) and the static test tier -- because it *executes* the config with live bindings,
which is far too much to do on every slider tick.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_CANDIDATES = ("luac5.5", "luac5.4", "luac5.3", "luac5.2", "luac", "luac5.1")
"""Newest first. The emitted subset is plain table constructors, so any of them will do;
preferring the newest just keeps the gate closest to the interpreter Hyprland embeds."""

_TIMEOUT_SECONDS = 10


class LuaSyntaxError(Exception):
    """Rendered output that does not parse. Always a bug in the writer, never in a config."""

    def __init__(self, name: str, detail: str) -> None:
        super().__init__(f"generated {name} is not valid Lua: {detail}")
        self.name = name
        self.detail = detail


@dataclass(frozen=True, slots=True)
class GateResult:
    """The outcome of one check, so callers can tell "passed" from "could not run"."""

    ok: bool
    available: bool
    detail: str = ""


@cache
def luac_command() -> str | None:
    """The `luac` on this machine, or `None` when the gate cannot run here."""
    for candidate in _CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def gate_available() -> bool:
    """Whether a syntax check can run at all.

    The gate degrades to a no-op rather than blocking a write on a missing dev tool: `luac`
    is not a runtime dependency of the app, and a user without it still needs their settings
    to save. CI and the test tier have it, which is where a writer bug gets caught.
    """
    return luac_command() is not None


def check(text: str, name: str = "module") -> GateResult:
    """Parse `text` with `luac -p`, reporting rather than raising."""
    command = luac_command()
    if command is None:
        return GateResult(ok=True, available=False)

    with tempfile.TemporaryDirectory(prefix="hyprtweaker-gate-") as directory:
        path = Path(directory) / "module.lua"
        path.write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [command, "-p", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )

    if completed.returncode == 0:
        return GateResult(ok=True, available=True)

    # luac prefixes every message with the temp path, which means nothing to a reader.
    detail = (completed.stderr or completed.stdout).strip().replace(str(path), name)
    return GateResult(ok=False, available=True, detail=detail)


def gate(text: str, name: str = "module") -> None:
    """Check `text` and raise `LuaSyntaxError` if it does not parse."""
    result = check(text, name)
    if not result.ok:
        raise LuaSyntaxError(name, result.detail)
