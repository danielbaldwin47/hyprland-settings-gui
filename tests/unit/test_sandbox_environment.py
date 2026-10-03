"""`tools/sandbox.py` hands the app an empty tool search path in the sandbox home (#233).

The sandbox app finds theming tools and wallpaper daemons only through `engine/tools.py`,
which looks on `HYPRTWEAKER_TOOL_PATH`. Pointed at `<home>/bin`, created empty, it finds
none of the owner's, installed or not; a stub written there shows a detected one.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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

    environment = sandbox.app_environment(nested, home, "unix:path=/tmp/sandbox-bus")

    assert environment == {
        **nested,
        "PYTHONPATH": str(ROOT / "src"),
        "HYPRTWEAKER_NON_UNIQUE": "1",
        "HYPRTWEAKER_TOOL_PATH": str(home / "bin"),
        "GDK_BACKEND": "wayland",
        "GTK_A11Y": "none",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/sandbox-bus",
        "ADW_DISABLE_PORTAL": "1",
        "GSETTINGS_BACKEND": "memory",
        "GDK_DISABLE": "vulkan",
    }


def test_the_app_gets_nothing_of_the_desktop_session(tmp_path: Path) -> None:
    """F13 of the #148 review and hand-test 15: the session's `GDK_BACKEND=wayland,x11,*`
    and `DISPLAY=:0` let a failed nested connect fall back to the desktop's Xwayland, and
    the host's accessibility bus reached the app (a Gtk-CRITICAL in app.log)."""
    nested = {
        "HOME": str(tmp_path),
        "WAYLAND_DISPLAY": "wayland-9",
        "GDK_BACKEND": "wayland,x11,*",
        "DISPLAY": ":0",
        "AT_SPI_BUS_ADDRESS": "unix:path=/run/user/1000/at-spi/bus_0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }

    environment = sandbox.app_environment(nested, tmp_path, "unix:path=/tmp/sandbox-bus")

    assert "DISPLAY" not in environment
    assert "AT_SPI_BUS_ADDRESS" not in environment
    assert environment["GDK_BACKEND"] == "wayland"
    assert environment["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/sandbox-bus"


def test_the_sandbox_home_has_an_empty_tool_path_and_finds_no_tool(tmp_path: Path) -> None:
    home = tmp_path / "sandbox-home"
    sandbox.prepare_home(home, None)
    environment = sandbox.app_environment(
        {**os.environ, "HOME": str(home)}, home, "unix:path=/x"
    )

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


def test_an_abbreviated_window_option_is_an_error_not_the_window(capsys: Any) -> None:
    """#270: argparse read `--wi` as `--window`, which the desktop fence matches whole."""
    import pytest

    with pytest.raises(SystemExit) as stopped:
        sandbox.build_parser().parse_args(["--wi"])

    assert stopped.value.code == 2
    assert "unrecognized arguments: --wi" in capsys.readouterr().err
