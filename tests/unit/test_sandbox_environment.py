"""`tools/sandbox.py` hands the app an empty tool search path in the sandbox home (#233).

The sandbox app finds theming tools and wallpaper daemons only through `engine/tools.py`,
which looks on `HYPRTWEAKER_TOOL_PATH`. Pointed at `<home>/bin`, created empty, it finds
none of the owner's, installed or not; a stub written there shows a detected one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import sandbox  # noqa: E402

from hyprtweaker.engine.tools import find_tool  # noqa: E402


def test_the_app_runs_with_a_tool_path_inside_the_sandbox_home(tmp_path: Path) -> None:
    home = tmp_path / "sandbox-home"
    nested = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "HYPRLAND_INSTANCE_SIGNATURE": "nested-sig",
        "WAYLAND_DISPLAY": "wayland-9",
        "PATH": "/usr/bin:/bin",
    }

    environment = sandbox.app_environment(nested, home)

    assert environment == {
        **nested,
        "PYTHONPATH": str(ROOT / "src"),
        "HYPRTWEAKER_NON_UNIQUE": "1",
        "HYPRTWEAKER_TOOL_PATH": str(home / "bin"),
    }


def test_the_sandbox_home_has_an_empty_tool_path_and_finds_no_tool(tmp_path: Path) -> None:
    home = tmp_path / "sandbox-home"
    sandbox.prepare_home(home, None)
    environment = sandbox.app_environment({**os.environ, "HOME": str(home)}, home)

    assert os.listdir(home / "bin") == []
    assert find_tool("sh", environment) is None


def test_a_reused_home_gets_the_refusing_stand_ins_first_on_path(tmp_path: Path) -> None:
    """Finding 9 of the #153 review: a copied rice's autostart, or a key binding, that names
    a wallpaper daemon ran the owner's real one from the host's `PATH`."""
    import subprocess

    home = tmp_path / "sandbox-home"
    sandbox.prepare_home(home, None)
    first = sandbox.fence_environment(home, "/usr/bin:/bin")
    again = sandbox.fence_environment(home, "/usr/bin:/bin")

    assert first == again
    assert first["PATH"] == f"{home / 'refused-bin'}:/usr/bin:/bin"
    ran = subprocess.run(
        ["swww-daemon", "--format", "xrgb"],
        env={**os.environ, **first},
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 126
    assert (home / "refused.log").read_text() == "swww-daemon --format xrgb\n"
    for name in ("dbus-update-activation-environment", "systemctl", "waybar"):
        assert (home / "refused-bin" / name).is_file()


def test_window_with_a_reused_home_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "sandbox-home"
    assert sandbox.argument_problem(window=True, home=home) is None
    sandbox.prepare_home(home, None)

    assert sandbox.argument_problem(window=True, home=home) == (
        "--window with a reused --home is refused: the home's own autostart would run with "
        "the host's session bus in reach. Use a fresh --home, or drop --window."
    )
    assert sandbox.argument_problem(window=False, home=home) is None
    assert sandbox.argument_problem(window=True, home=None) is None
