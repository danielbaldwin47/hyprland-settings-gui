"""The ``Adw.Application`` subclass.

Identity (app id, name) is fixed by ADR-0019 and must not churn: the desktop
entry, icon theme name and any future Flatpak id all key off ``APP_ID``.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from hyprtweaker.ui.shell.window import MainWindow  # noqa: E402

APP_ID = "io.github.danielbaldwin47.Hyprtweaker"
APP_NAME = "Hyprtweaker"


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
