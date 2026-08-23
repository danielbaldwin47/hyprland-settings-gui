"""The ``Adw.Application`` subclass.

``APP_ID`` is re-exported from :mod:`hyprtweaker` so callers that already import
this module keep working; it is defined there, toolkit-free, because reading the
app id should not require GTK.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from hyprtweaker import APP_ID  # noqa: E402
from hyprtweaker.ui.shell.window import MainWindow  # noqa: E402

__all__ = ["APP_ID", "HyprtweakerApplication"]


class HyprtweakerApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = MainWindow(application=self)
        window.present()
