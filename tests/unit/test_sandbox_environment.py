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
