"""UI smoke tier: the app boots and puts up its window.

Deliberately shallow — no pixel assertions, no widget-state assertions (spec #48,
"Testing Decisions"). This tier answers one question: does the shell still come
up? Everything with real logic lives in the engine and is tested headless there.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib  # noqa: E402

from hyprtweaker.application import APP_ID, HyprtweakerApplication  # noqa: E402
from hyprtweaker.ui.shell.window import MainWindow  # noqa: E402


def test_window_constructs() -> None:
    Adw.init()
    app = Adw.Application(application_id=APP_ID)
    window = MainWindow(application=app)

    assert isinstance(window, Adw.ApplicationWindow)
    assert window.get_title()
    assert window.get_content() is not None


def test_application_boots_and_presents_a_window() -> None:
    app = HyprtweakerApplication()
    seen: dict[str, object] = {}

    def inspect_then_quit(application: HyprtweakerApplication) -> bool:
        # Runs on the first idle turn after activation, so do_activate has
        # already built and presented the window by now.
        seen["window"] = application.props.active_window
        application.quit()
        return GLib.SOURCE_REMOVE

    app.connect("activate", lambda a: GLib.idle_add(inspect_then_quit, a))

    status = app.run([])

    assert status == 0
    assert isinstance(seen.get("window"), MainWindow)
