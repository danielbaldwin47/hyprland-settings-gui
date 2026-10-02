"""Gating for the UI smoke tier.

This tier runs only where GTK4 + libadwaita and a usable display are present
(ADR-0011 testing tiers; spec #48 seam 5). Everywhere else it skips rather than
fails, so the engine tiers stay runnable on a bare machine.

Two constraints shape how the gate is applied, and they pull against each other:

* It must not import ``gi`` at collection time, or a machine without PyGObject
  gets a collection *error* instead of a skip. That is why the test modules keep
  their toolkit imports inside the test functions.
* The tests must still be collected. A module-level skip collects nothing, and
  pytest exits 5 ("no tests ran") for an empty run, which ``meson test`` reads as
  a failure.

So the gate marks collected items as skipped. Living in conftest means each new
``tests/ui`` module inherits it instead of repeating it.

Set ``HYPRTWEAKER_REQUIRE_UI=1`` to turn the skip into a hard failure. CI sets it
on the job that installs GTK, so a broken install surfaces as a red build rather
than a green one that quietly skipped everything.

The tier draws on an Xvfb display and a session bus of its own, one pair per pytest
process, never on the desktop session it was started from: its windows would map there,
and Hyprland would show its "Application Not Responding" dialog over the developer's work
(#146). The bus is a ``dbus-daemon`` with no service directory, so the desktop's
settings portal is not on it and nothing the owner runs can be activated (#212).
``private_display.py`` starts both and pins GTK to them, for this tier and for the
widget probe route (``tools/widget_probe.py``) alike.
``HYPRTWEAKER_UI_HOST_DISPLAY=1`` puts it on the host display and the host bus on
purpose, for example to watch it. The display opens in ``pytest_configure``, before
collection: importing ``Gtk`` initialises GTK, and some ``tests/unit`` modules import UI
pages at collection time.
"""

from __future__ import annotations

import atexit
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import main_loop
import pytest
from private_display import (
    PINNED,
    pin_environment,
    session_display_clash,
    start_bus,
    start_xvfb,
)

UI_TESTS_DIR = Path(__file__).parent


@pytest.fixture(autouse=True)
def sandboxed_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every UI test at a throwaway config dir and away from any live compositor.

    Autouse and unconditional, because the machine most likely to run this tier is a
    developer's own Hyprland box: `HyprtweakerApplication` builds a `Session` over
    `ConfigPaths.default()` and `Instance.current()`, and a test that boots the app would
    otherwise attach to the user's running session and their real `~/.config/hypr`.
    Read-only today, but "the test suite cannot reach your config" should be a property of
    the tier rather than of what the code currently happens to do.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)


@pytest.fixture(autouse=True)
def released_windows() -> Iterator[None]:
    """Destroy and release every window a test opened, when the test ends (#219).

    GTK keeps a toplevel until it is destroyed, and a destroyed `MainWindow` still holds
    itself through its handlers until `release` cuts them. A dozen `build_window` helpers
    left both undone, so a serial run of this tier held 150 windows and aborted. Windows
    open before the test began, such as a module-scoped one, are their fixture's to close.

    The main loop runs first and last, as the app's would around a close: first for the
    idles the test left queued, which may present a dialog on the window, and last for the
    idle that releases a dialog the test closed.
    """
    from gi.repository import Gtk

    before = set(Gtk.Window.get_toplevels())
    yield
    if not any(window not in before for window in Gtk.Window.get_toplevels()):
        return
    from hyprtweaker.ui.release import release

    main_loop.settle("the test's queued idles, before its windows close")
    for window in [each for each in Gtk.Window.get_toplevels() if each not in before]:
        window.destroy()
        release(window)
    main_loop.settle("the closed windows' and dialogs' release")


HOST_DISPLAY_OPT_IN = "HYPRTWEAKER_UI_HOST_DISPLAY"


def ui_unavailable() -> str | None:
    """Return why this tier cannot run here, or None when it can."""
    try:
        import gi
    except ImportError:
        return "PyGObject (gi) is not installed"

    try:
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        gi.require_version("Adw", "1")
    except ValueError as exc:
        return f"GTK4 / libadwaita typelibs unavailable: {exc}"

    if os.environ.get(HOST_DISPLAY_OPT_IN) == "1":
        return open_display()

    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        return f"Xvfb is not installed; set {HOST_DISPLAY_OPT_IN}=1 to use the host display"
    dbus_daemon = shutil.which("dbus-daemon")
    if dbus_daemon is None:
        return (
            "dbus-daemon is not installed, so there is no private session bus; "
            f"set {HOST_DISPLAY_OPT_IN}=1 to use the host's"
        )
    display = start_xvfb(xvfb)
    if display is None:
        return "Xvfb did not open a display within 10 s"
    if clash := session_display_clash(display, os.environ.get("DISPLAY")):
        return clash
    bus = start_bus(dbus_daemon)
    if bus is None:
        return "dbus-daemon did not open a private session bus within 10 s"
    # It dies with this process whatever happens (PR_SET_PDEATHSIG); this ends it and
    # removes its directory on a normal exit, an xdist worker's included.
    atexit.register(bus.stop)

    # GDK reads these only while it opens its display, and the Harness tier reads the host
    # session from them at test time when both tiers share a process, so restore them.
    # DISPLAY stays on the Xvfb: the NVIDIA EGL driver opens `$DISPLAY` again when a
    # window first realizes, so a restored one sends it to the session's X server, and
    # it crashes when nothing serves that display. Not GTK_A11Y: GTK reads it at the
    # first widget, after this returns, and restored it put every test widget on the
    # desktop's accessibility bus. Nor the bus and the GSettings backend: GIO reads them
    # at the first portal, settings or GApplication call, long after the display opens,
    # and a restored bus address sent those to the owner's session bus.
    kept = (
        "DISPLAY",
        "GTK_A11Y",
        "DBUS_SESSION_BUS_ADDRESS",
        "GSETTINGS_BACKEND",
        "ADW_DISABLE_PORTAL",
    )
    saved = {name: os.environ.get(name) for name in PINNED if name not in kept}
    pin_environment(os.environ, display, bus.address)
    try:
        return open_display()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def open_display() -> str | None:
    """Open GTK's default display from the current environment; say why not if it fails."""
    from gi.repository import Gdk, Gtk

    # Gtk.init_check() alone is not enough: on GTK4 it can report success while
    # no display was ever opened, and the failure only surfaces later as a
    # RuntimeError from the first widget. The opened display is the real signal.
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        return "no usable display"

    return None


_UI_SKIP_REASON = pytest.StashKey[str | None]()


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_UI_SKIP_REASON] = ui_unavailable()


# trylast: pytest applies -k/-m deselection in its own copy of this hook, so
# running after it means `items` is the final selection. Otherwise `-k` picking
# only engine tests would still see UI items here and trip the gate below.
@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    # This hook is global: even though it lives in tests/ui/conftest.py, pytest
    # hands it every collected item in the run. Skipping the lot would silently
    # disable the engine tiers, so match on path first. `item.path` is typed
    # optional, and a pathless item from some future plugin should not become
    # the INTERNALERROR this gate exists to avoid.
    ui_items = [
        item for item in items if item.path is not None and UI_TESTS_DIR in item.path.parents
    ]

    # Selecting no UI test at all is not a failure, whatever REQUIRE_UI says.
    if not ui_items:
        return

    reason = config.stash[_UI_SKIP_REASON]
    if reason is None:
        return

    if os.environ.get("HYPRTWEAKER_REQUIRE_UI") == "1":
        # pytest.exit rather than raise: a bare exception here surfaces as an
        # INTERNALERROR traceback, which buries the one line explaining why.
        pytest.exit(
            f"HYPRTWEAKER_REQUIRE_UI=1 but the UI smoke tier cannot run: {reason}",
            returncode=1,
        )

    skip = pytest.mark.skip(reason=f"UI smoke tier: {reason}")
    for item in ui_items:
        item.add_marker(skip)
