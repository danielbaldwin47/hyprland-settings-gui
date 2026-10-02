"""No unit test reaches the owner's home or runs a real tool, with no setup of its own (#233).

`tests/conftest.py` fences every test; these prove it from inside one, by asking the
product where home is and what it may run. `tests/ui/test_ui_hermetic_home.py` asks the same
from the UI tier, whose process opened GTK before any test began.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from hermetic import FENCE_LOG_ENV, REFUSED_TOOLS, refusals
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.tools import ToolRefused, find_tool, run_tool


def test_home_and_the_xdg_homes_are_under_this_tests_tmp_path(tmp_path: Path) -> None:
    paths = ConfigPaths.default()
    home = tmp_path / "fence" / "home"

    assert Path.home() == home
    assert Path("~/.config/matugen").expanduser() == home / ".config" / "matugen"
    assert paths.hypr_dir == home / ".config" / "hypr"
    assert paths.state_dir == home / ".local" / "state" / "hyprtweaker"
    assert paths.config_home == home / ".config"
    assert paths.state_home == home / ".local" / "state"
    assert os.environ["XDG_DATA_HOME"] == str(home / ".local" / "share")
    assert os.environ["XDG_CACHE_HOME"] == str(home / ".cache")
    assert "HYPRLAND_INSTANCE_SIGNATURE" not in os.environ


def test_no_tool_is_found_or_run_by_default(tmp_path: Path) -> None:
    assert os.listdir(tmp_path / "fence" / "tools") == []
    assert find_tool("sh") is None
    with pytest.raises(ToolRefused):
        run_tool(["/bin/true"], timeout=1)


def test_a_stub_tool_is_found_and_runs_exactly_once(
    tmp_path: Path, stub_tool: Callable[[str, str], Path]
) -> None:
    counter = tmp_path / "count"
    stub_tool("matugen", f"echo ran >> '{counter}'")

    found = find_tool("matugen")
    assert found == tmp_path / "fence" / "tools" / "matugen"
    run_tool([str(found)], timeout=5)

    assert counter.read_text(encoding="utf-8") == "ran\n"


def test_a_theming_tool_run_by_name_from_path_is_refused_and_fails_the_test() -> None:
    """The route around the seam: `PATH` lookup, from a test or from code under test."""
    ran = subprocess.run(
        ["matugen", "image", "wall.png"], capture_output=True, text=True, check=False
    )
    log = Path(os.environ[FENCE_LOG_ENV])

    assert ran.returncode == 126
    assert "refused: tests never run a real matugen" in ran.stderr
    assert "stub_tool" in ran.stderr
    assert refusals(log) == (
        "this test tried to run a real theming tool or wallpaper daemon: "
        "['matugen image wall.png']. Find and run a tool through hyprtweaker.engine.tools "
        "(find_tool, run_tool), and in a test put a stub on the tool search path with the "
        "stub_tool fixture."
    )
    log.unlink()  # this test's own run, consumed, so its teardown passes


def test_every_refused_name_resolves_to_its_stand_in_on_path() -> None:
    found = {name: shutil.which(name) for name in REFUSED_TOOLS}

    assert None not in found.values(), found
    for name, path in found.items():
        assert f"no test runs a real {name}." in Path(str(path)).read_text(encoding="utf-8")
