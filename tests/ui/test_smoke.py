"""UI smoke tier: the app boots and puts up its window.

Deliberately shallow -- no pixel assertions, no widget-state assertions (spec
#48, "Testing Decisions"). This tier answers one question: does the shell still
come up? Everything with real logic lives in the Engine and is tested headless
there.

The toolkit imports sit inside the test functions on purpose: importing ``gi``
at module scope would raise during collection on a machine without PyGObject,
which pytest reports as an error rather than the skip this tier is supposed to
produce. `tests/ui/conftest.py` owns the gate itself.
"""

from __future__ import annotations


def test_window_constructs() -> None:
    from gi.repository import Adw

    from hyprtweaker import APP_ID
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
