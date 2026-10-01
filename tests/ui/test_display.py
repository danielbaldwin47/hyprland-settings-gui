"""The UI tier draws on a display of its own, never on the developer's desktop session.

Each test runs `_display_probe.py` in a child pytest with the host session it describes.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from _display_probe import ABSENT_WAYLAND_DISPLAY

ROOT = Path(__file__).resolve().parents[2]
PROBE_RUN = [sys.executable, "-m", "pytest", "tests/ui/_display_probe.py", "-q", "-rs"]


def run_probe(**env: str) -> subprocess.CompletedProcess[str]:
    """Run the probe as a desktop session whose Wayland socket does not exist."""
    child_env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DISPLAY", "GDK_BACKEND", "WAYLAND_DISPLAY")
        and not key.startswith("HYPRTWEAKER_")
    }
    child_env["WAYLAND_DISPLAY"] = ABSENT_WAYLAND_DISPLAY
    child_env.update(env)
    return subprocess.run(
        [*PROBE_RUN, "--color=no", "-p", "no:cacheprovider"],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_desktop_session_gets_its_own_display() -> None:
    result = run_probe()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_without_xvfb_the_tier_skips_naming_it(tmp_path: Path) -> None:
    result = run_probe(PATH=str(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "Xvfb" in result.stdout


def test_without_xvfb_require_ui_fails_the_run(tmp_path: Path) -> None:
    result = run_probe(PATH=str(tmp_path), HYPRTWEAKER_REQUIRE_UI="1")

    assert result.returncode == 1
    assert "Xvfb" in result.stdout + result.stderr


def test_the_host_display_opt_in_uses_the_host_session() -> None:
    # The host's Wayland socket does not exist, so on the host display the tier skips.
    result = run_probe(HYPRTWEAKER_UI_HOST_DISPLAY="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout
    assert "no usable display" in result.stdout
