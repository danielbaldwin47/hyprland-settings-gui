"""Run only by `test_display.py`, in a child pytest whose host session it controls.

Not named `test_*.py`, so the suite never collects it; a child run names it explicitly.
The parent points `WAYLAND_DISPLAY` at a socket that does not exist and leaves `DISPLAY`
unset, so this test passes only on a display the UI tier started for itself.
"""

from __future__ import annotations

import os
import tempfile

ABSENT_WAYLAND_DISPLAY = "wayland-hyprtweaker-absent"
# The parent gives the child an owner-like session bus address with nothing behind it, as it
# gives a Wayland socket that does not exist; the tier must not be on it. Reading the
# worker's own inherited environment would prove nothing: a worker inherits its controller's.
FORGED_SESSION_BUS = "unix:path=/nonexistent/hyprtweaker-owner/session-bus"
TESTS_IN_THIS_FILE = 4


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


def test_the_tier_runs_on_a_session_bus_of_its_own_that_activates_nothing() -> None:
    from gi.repository import Gio

    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    # GIO connects to the session bus the way GTK and libadwaita do, by the environment: on
    # the forged address it would fail, and on the owner's it would list the desktop's portal.
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    activatable = bus.call_sync(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "ListActivatableNames",
        None,
        None,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
    ).unpack()[0]

    assert address is not None
    assert address != FORGED_SESSION_BUS
    assert address.startswith(f"unix:path={tempfile.gettempdir()}/hyprtweaker-bus-")
    assert activatable == ["org.freedesktop.DBus"]


def test_the_tier_keeps_settings_in_memory_and_off_the_desktops_portal() -> None:
    # GIO and libadwaita read both at the first settings call, after the display opened.
    assert os.environ.get("GSETTINGS_BACKEND") == "memory"
    assert os.environ.get("ADW_DISABLE_PORTAL") == "1"


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
