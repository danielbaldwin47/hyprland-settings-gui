"""The UI tier draws on a display of its own, never on the developer's desktop session.

The first four run `_display_probe.py` in a child pytest with the host session it
describes; the last two hold the private display to agent numbers, never the session's.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
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


STARTER = """
import sys
sys.path.insert(0, sys.argv[1])
from private_display import start_xvfb
print(start_xvfb(sys.argv[2]), flush=True)
sys.stdin.read()  # hold the display until the test closes stdin
"""


def test_concurrent_starts_take_distinct_high_displays_never_the_sessions() -> None:
    # Twenty xdist workers start an Xvfb apiece at once. An X server walking up from
    # display 0 replaced the desktop's own X socket at /tmp/.X11-unix/X0 (2026-10-02).
    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        pytest.skip("Xvfb is not installed")
    starters = [
        subprocess.Popen(
            [sys.executable, "-c", STARTER, str(ROOT / "tests" / "ui"), xvfb],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            # A session whose display number is the first agent number: it is skipped.
            env={**os.environ, "DISPLAY": ":200.0"},
        )
        for _ in range(8)
    ]
    try:
        displays = [starter.stdout.readline().strip() for starter in starters]
    finally:
        for starter in starters:
            starter.stdin.close()
            starter.wait(timeout=10)

    assert len(set(displays)) == 8, displays
    assert all(
        display.startswith(":") and 200 <= int(display[1:]) <= 999 for display in displays
    ), displays
    assert ":200" not in displays


def test_the_tier_refuses_a_display_that_is_the_sessions(
    pytestconfig: pytest.Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    ui_conftest = next(
        plugin
        for plugin in pytestconfig.pluginmanager.get_plugins()
        if getattr(plugin, "__file__", None) == str(ROOT / "tests" / "ui" / "conftest.py")
    )
    monkeypatch.delenv("HYPRTWEAKER_UI_HOST_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":4242.0")
    monkeypatch.setattr(ui_conftest.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(ui_conftest, "start_xvfb", lambda xvfb: ":4242")

    assert ui_conftest.ui_unavailable() == (
        "refusing display :4242: it is the desktop session's own DISPLAY (:4242.0), where an "
        "X server replaces the session's X socket and GTK draws on the desktop; agent X "
        "servers take displays 200-999 only (docs/agents/local-checks.md § Private X displays)"
    )
