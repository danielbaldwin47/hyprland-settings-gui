"""The ``Adw.Application`` subclass.

``APP_ID`` is re-exported from :mod:`hyprtweaker` so callers that already import
this module keep working; it is defined there, toolkit-free, because reading the
app id should not require GTK.

This is where the three halves of the app meet: the main loop's asyncio runner, the
`Session` that owns the engine, and the window that is a view of it. The wiring is one-way
-- the session knows nothing about widgets and reports through plain callbacks, which the
window turns into a Banner, a Toast, or a refresh (ADR-0011).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from hyprtweaker import APP_ID, __version__  # noqa: E402
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.shell.runtime import MainLoopRunner  # noqa: E402
from hyprtweaker.ui.shell.window import MainWindow  # noqa: E402

__all__ = ["APP_ID", "HyprtweakerApplication"]


class HyprtweakerApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._runner = MainLoopRunner()
        self._session: Session | None = None

    @property
    def session(self) -> Session | None:
        """The running session, once `do_activate` has built one."""
        return self._session

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = self._build_window()
        window.present()

    def _build_window(self) -> MainWindow:
        session = Session(spawn=self._runner.spawn, app_version=__version__)
        self._session = session

        window = MainWindow(session, application=self)
        session.on_state_changed = window.sync
        session.on_applied = window.show_result

        # Started only once the window is listening: the session's first act is a re-read,
        # and it reports the result through those two callbacks.
        if self._runner.available:
            session.start()
        else:
            session.set_read_only(self._runner.unavailable_reason or "cannot apply changes")

        return window
