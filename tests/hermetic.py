"""The fence between the test suite and the owner's home and tools (#233).

`tests/conftest.py` applies it; this module holds what that conftest and the tests that
prove it share. Spec #153's code writes another tool's config under `~/.config` and runs
theming tools and wallpaper daemons. In a test, `~` must be the test's own directory and
no real tool may run: a real matugen or wallust runs its `post_hook`, which reloads
whichever compositor it finds, and on the owner's machine that is their desktop.

Two layers, so neither depends on what is installed:

- **Per process**, before anything is collected: `HOME`, the four `XDG_*` homes and the tool
  search path point into a throwaway directory, and a directory of refusing stand-ins, one
  per name in `REFUSED_TOOLS`, goes first on `PATH`. GLib caches the user directories the
  first time GTK asks, which is before any test, so this is what GTK sees.
- **Per test** (`hermetic_home`): the same variables point under the test's `tmp_path`.

A stand-in that runs logs itself to `HYPRTWEAKER_FENCE_LOG`, and the test fails at
teardown. Only `hyprtweaker.engine.tools` finds and runs a tool, on the tool search path,
which is an empty directory a test may put stubs in (the `stub_tool` fixture).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

TOOL_PATH_ENV = "HYPRTWEAKER_TOOL_PATH"
"""`hyprtweaker.engine.tools.TOOL_PATH_ENV`, spelled out: conftest imports no product code."""

FENCE_LOG_ENV = "HYPRTWEAKER_FENCE_LOG"

REFUSED_TOOLS = (
    "matugen",
    "wallust",
    "noctalia",
    "noctalia-shell",
    "qs",
    "quickshell",
    "dms",
    "swww",
    "swww-daemon",
    "awww",
    "awww-daemon",
    "hyprpaper",
    "dbus-update-activation-environment",
)
"""Theming tools and wallpaper daemons, by the program names their docs launch them with, and
the one program a rice's autostart uses to rewrite the session's activation environment."""

FIX = (
    "Find and run a tool through hyprtweaker.engine.tools (find_tool, run_tool), and in a "
    "test put a stub on the tool search path with the stub_tool fixture."
)


def fenced_environment(root: Path) -> dict[str, str]:
    """`HOME`, the `XDG_*` homes, the tool search path and the refusal log, all under `root`."""
    home = root / "home"
    return {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_STATE_HOME": str(home / ".local" / "state"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        TOOL_PATH_ENV: str(root / "tools"),
        FENCE_LOG_ENV: str(root / "refused.log"),
    }


def make_fence(root: Path) -> dict[str, str]:
    """Create the directories `fenced_environment(root)` names, and return it."""
    environment = fenced_environment(root)
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        Path(environment[name]).mkdir(parents=True, exist_ok=True)
    Path(environment["XDG_CACHE_HOME"]).mkdir(exist_ok=True)
    Path(environment[TOOL_PATH_ENV]).mkdir(exist_ok=True)
    return environment


def install_refusals(directory: Path, names: Iterable[str] = REFUSED_TOOLS) -> None:
    """Write a refusing stand-in for every one of `names` into `directory`."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        stand_in = directory / name
        stand_in.write_text(
            "#!/bin/sh\n"
            f"# A stand-in from tests/hermetic.py: no test runs a real {name}.\n"
            f'printf \'%s\\n\' "{name} $*" >> "${{{FENCE_LOG_ENV}:-/dev/null}}"\n'
            f"echo 'refused: tests never run a real {name}. {FIX}' >&2\n"
            "exit 126\n",
            encoding="utf-8",
        )
        stand_in.chmod(0o755)


def refusals(log: Path) -> str | None:
    """Why the test that owns `log` fails, or None when no stand-in ran during it."""
    try:
        ran = log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    return f"this test tried to run a real theming tool or wallpaper daemon: {ran}. {FIX}"


def write_stub(directory: Path, name: str, script: str) -> Path:
    """An executable `#!/bin/sh` script called `name` in `directory`, running `script`."""
    path = directory / name
    path.write_text(f"#!/bin/sh\n{script}\n", encoding="utf-8")
    path.chmod(0o755)
    return path
