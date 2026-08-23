"""UI smoke tier: the app boots and puts up its window.

Deliberately shallow -- no pixel assertions, no widget-state assertions (spec
#48, "Testing Decisions"). This tier answers one question: does the shell still
come up? Everything with real logic lives in the Engine and is tested headless
there.

The tier gates itself at import time, because the gate has to run before the
`gi` imports below: a missing PyGObject or typelib raises during *collection*,
which pytest reports as an error, not a skip -- and this tier must stay skippable
so the engine tiers run on a bare machine.

Set ``HYPRTWEAKER_REQUIRE_UI=1`` to turn that skip into a hard failure. CI sets
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
if _reason is not None:
    if REQUIRE_UI:
        raise RuntimeError(
            f"HYPRTWEAKER_REQUIRE_UI=1 but the UI smoke tier cannot run: {_reason}"
        )
    pytest.skip(f"UI smoke tier: {_reason}", allow_module_level=True)


from gi.repository import Adw, Gio, GLib  # noqa: E402

from hyprtweaker.application import APP_ID, HyprtweakerApplication  # noqa: E402
from hyprtweaker.ui.shell.window import MainWindow  # noqa: E402

Adw.init()


def test_window_constructs() -> None:
    app = Adw.Application(application_id=APP_ID)
    window = MainWindow(application=app)

    assert isinstance(window, Adw.ApplicationWindow)
    assert window.get_title()
    assert window.get_content() is not None


def test_application_boots_and_presents_a_window() -> None:
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
