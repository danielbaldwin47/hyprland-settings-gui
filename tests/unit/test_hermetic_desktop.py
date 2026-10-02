"""No test process can reach the desktop session's display or bus (F11 of the #148 review).

PyGObject runs `Gtk.init_check()` on the first `Gtk` import, so a unit test importing a
page opened the owner's display and session bus. `tests/conftest.py` now strips both from
every test process (`hermetic.fence_desktop`); these hold that structure in place.
"""

from __future__ import annotations

import os
from pathlib import Path

from hermetic import NO_SESSION_BUS, SESSION_DISPLAY_ENV, fence_desktop


def test_the_fence_takes_the_session_display_and_bus_away() -> None:
    environ = {
        "WAYLAND_DISPLAY": "wayland-1",
        "DISPLAY": ":0",
        "GDK_BACKEND": "wayland,x11,*",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "AT_SPI_BUS_ADDRESS": "unix:path=/run/user/1000/at-spi/bus_0",
    }

    fence_desktop(environ)

    assert environ == {
        SESSION_DISPLAY_ENV: ":0",
        "GDK_BACKEND": "x11",
        "GTK_A11Y": "none",
        "DBUS_SESSION_BUS_ADDRESS": NO_SESSION_BUS,
    }


def test_the_owners_host_display_switch_keeps_the_session() -> None:
    environ = {
        "HYPRTWEAKER_UI_HOST_DISPLAY": "1",
        "WAYLAND_DISPLAY": "wayland-1",
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }

    fence_desktop(environ)

    assert environ == {
        "HYPRTWEAKER_UI_HOST_DISPLAY": "1",
        "WAYLAND_DISPLAY": "wayland-1",
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    }


def test_the_host_switch_without_a_bus_still_never_leaves_the_address_unset() -> None:
    """CI's meson job set the switch for every suite on a runner with no session bus, and
    the unset address would have let GIO fall back to `$XDG_RUNTIME_DIR/bus`."""
    environ = {"HYPRTWEAKER_UI_HOST_DISPLAY": "1"}

    fence_desktop(environ)

    assert environ == {
        "HYPRTWEAKER_UI_HOST_DISPLAY": "1",
        "DBUS_SESSION_BUS_ADDRESS": NO_SESSION_BUS,
    }


def test_this_test_process_has_no_desktop_to_reach() -> None:
    """Whatever ran before in this worker: a UI test leaves its own Xvfb, never the host's."""
    assert "WAYLAND_DISPLAY" not in os.environ
    display = os.environ.get("DISPLAY")
    assert display is None or 200 <= int(display.lstrip(":").split(".")[0]) <= 999, display
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    owners_bus = f"unix:path={Path(runtime) / 'bus'}"
    assert os.environ.get("DBUS_SESSION_BUS_ADDRESS") not in (None, owners_bus)
