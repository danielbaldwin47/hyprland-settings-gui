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
    # DISPLAY stays on the tier's display: the NVIDIA EGL driver reopens `$DISPLAY`.
    assert os.environ.get("DISPLAY") == display.get_name()


def test_the_tier_keeps_the_hosts_scale_and_screen_reader_off_its_widgets() -> None:
    from gi.repository import Gdk, GObject, Gtk

    # The parent runs this with GDK_SCALE=2, the owner's HiDPI setting, and no GTK_A11Y:
    # GDK reads the one when it opens the display, GTK the other at the first widget.
    monitor = Gdk.Display.get_default().get_monitors().get_item(0)
    geometry = monitor.get_geometry()
    context = Gtk.Label().get_at_context()

    assert (monitor.get_scale_factor(), geometry.width, geometry.height) == (1, 1280, 1024)
    # GTK_A11Y=none gives no context on current GTK and a test context on older ones; with
    # it restored too early this was a GtkAtSpiContext, on the desktop's bus.
    assert (GObject.type_name(context.__gtype__) if context else None) in (
        None,
        "GtkTestATContext",
    )
