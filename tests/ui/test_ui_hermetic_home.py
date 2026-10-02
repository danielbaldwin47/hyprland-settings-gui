"""No UI test reaches the owner's home or runs a real tool, with no setup of its own (#233).

The UI tier differs from the unit tier in one way that matters here: its process opened
GTK in `pytest_configure`, before any test, and GLib caches each user directory the first
time anything asks. So the process itself must already be fenced by then.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

import pytest

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.tools import ToolRefused, find_tool, run_tool


def test_home_and_the_xdg_homes_are_under_this_tests_tmp_path(tmp_path: Path) -> None:
    paths = ConfigPaths.default()
    home = tmp_path / "fence" / "home"

    assert Path.home() == home
    assert paths.hypr_dir == home / ".config" / "hypr"
    assert paths.state_dir == home / ".local" / "state" / "hyprtweaker"
    assert os.environ["XDG_DATA_HOME"] == str(home / ".local" / "share")
    assert os.environ["XDG_CACHE_HOME"] == str(home / ".cache")


def test_no_tool_is_found_or_run_by_default() -> None:
    assert find_tool("sh") is None
    with pytest.raises(ToolRefused):
        run_tool(["/bin/true"], timeout=1)


def test_gtk_never_saw_the_owners_home() -> None:
    """GLib caches each user directory the first time it is asked, in whichever test or
    hook that was; none of them may be the owner's."""
    from gi.repository import GLib, Gtk

    assert Gtk.Window.get_toplevels() is not None  # GTK is up in this process
    owner = Path(pwd.getpwuid(os.getuid()).pw_dir)
    seen = [
        GLib.get_home_dir(),
        GLib.get_user_config_dir(),
        GLib.get_user_data_dir(),
        GLib.get_user_cache_dir(),
        GLib.get_user_state_dir(),
    ]

    assert [path for path in seen if Path(path).is_relative_to(owner)] == []
    assert all("fence" in path for path in seen), seen
