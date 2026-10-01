"""Run only by `test_display.py`, in a child pytest whose host session it controls.

Not named `test_*.py`, so the suite never collects it; a child run names it explicitly.
The parent points `WAYLAND_DISPLAY` at a socket that does not exist and leaves `DISPLAY`
unset, so this test passes only on a display the UI tier started for itself.
"""

from __future__ import annotations

import os

ABSENT_WAYLAND_DISPLAY = "wayland-hyprtweaker-absent"


def test_the_tier_draws_on_its_own_display_and_leaves_the_host_env_alone() -> None:
    from gi.repository import Gdk

    display = Gdk.Display.get_default()

    assert display is not None
    assert display.get_name().startswith(":")
    # The Harness tier reads the host session from the environment at test time, in the
    # same process when both tiers run together.
    assert os.environ.get("WAYLAND_DISPLAY") == ABSENT_WAYLAND_DISPLAY
    assert "DISPLAY" not in os.environ
