"""UI smoke tier: the app boots and puts up its window.

Deliberately shallow -- no pixel assertions, no widget-state assertions (spec
#48, "Testing Decisions"). This tier answers one question: does the shell still
come up? Everything with real logic lives in the Engine and is tested headless
there.

Two things shape the odd import layout below. The gate has to run before any
`gi` import, because a missing PyGObject or typelib otherwise raises during
*collection*, which pytest reports as an error rather than a skip -- and this
tier must stay skippable so the engine tiers run on a bare machine. But the
tests still have to be *collected*, since a module-level skip collects nothing
and pytest exits 5 ("no tests ran"), which `meson test` reads as a failure. So
the gate is a `skipif` marker and the toolkit imports live inside the tests.

Set ``HYPRTWEAKER_REQUIRE_UI=1`` to turn the skip into a hard failure. CI sets
it on the job that installs GTK, so a broken install surfaces as a red build
instead of a green one that quietly skipped everything.
"""

from __future__ import annotations

import os

import pytest

REQUIRE_UI = os.environ.get("HYPRTWEAKER_REQUIRE_UI") == "1"


def _unavailable() -> str | None:
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

    from gi.repository import Gdk, Gtk

    # Gtk.init_check() alone is not enough: on GTK4 it can report success while
    # no display was ever opened, and the failure only surfaces later as a
    # RuntimeError from the first widget. The opened display is the real signal.
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        return "no usable display"

    return None


_reason = _unavailable()

if _reason is not None and REQUIRE_UI:
    raise RuntimeError(f"HYPRTWEAKER_REQUIRE_UI=1 but the UI smoke tier cannot run: {_reason}")

pytestmark = pytest.mark.skipif(_reason is not None, reason=f"UI smoke tier: {_reason}")


def test_window_constructs() -> None:
    from gi.repository import Adw

    from hyprtweaker.application import APP_ID
    from hyprtweaker.ui.shell.window import MainWindow

    Adw.init()
    app = Adw.Application(application_id=APP_ID)
    window = MainWindow(application=app)

    assert isinstance(window, Adw.ApplicationWindow)
    assert window.get_title()
    assert window.get_content() is not None


def test_application_boots_and_presents_a_window() -> None:
    from gi.repository import Adw, Gio, GLib

    from hyprtweaker.application import HyprtweakerApplication
    from hyprtweaker.ui.shell.window import MainWindow

    Adw.init()
    app = HyprtweakerApplication()
    # The app is single-instance in production, which would make this test
    # activate an already-running copy on a dev box and find no window of its
    # own. Booting a private instance is what we actually want to assert.
    app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)

    seen: dict[str, object] = {}

    def inspect_then_quit(application: HyprtweakerApplication) -> bool:
        # Runs on the first idle turn after activation, so do_activate has
        # already built and presented the window by now.
        seen["window"] = application.props.active_window
        application.quit()
        return GLib.SOURCE_REMOVE

    app.connect("activate", lambda a: GLib.idle_add(inspect_then_quit, a))

    status = app.run(["hyprtweaker"])

    assert status == 0
    assert isinstance(seen.get("window"), MainWindow)
