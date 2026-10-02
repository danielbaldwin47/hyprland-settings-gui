"""The application a test's `MainWindow` belongs to (#228).

GTK takes a window into an application only after the application's `startup`, and logs a
Gtk-CRITICAL for one added before; the app itself builds its window in `activate`, so it
never does. Registering runs `startup`. One per pytest process, registered on the tier's
private session bus without claiming a name (`NON_UNIQUE`), so no two tests or workers
contend for one.
"""

from __future__ import annotations

from typing import Any

APP_ID = "io.github.danielbaldwin47.HyprtweakerTest"

_app: Any = None


def started_application() -> Any:
    """This process's `Adw.Application`, already through `startup`."""
    global _app
    if _app is None:
        from gi.repository import Adw, Gio

        Adw.init()
        app = Adw.Application(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        app.register(None)
        _app = app
    return _app


def presented(dialog: Any) -> Any:
    """`dialog`, presented on a window of its own, as the app presents every dialog.

    A dialog's Save and Cancel close it, and libadwaita logs an Adwaita-CRITICAL for a
    dialog closed that was never presented. The window is never shown: the dialog needs a
    host, not a surface. The tier's `released_windows` destroys it when the test ends.
    """
    from gi.repository import Adw

    dialog.present(Adw.Window())
    return dialog
