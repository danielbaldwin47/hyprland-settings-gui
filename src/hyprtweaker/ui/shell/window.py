"""The main window.

An empty shell for now: header bar plus a placeholder. The real furniture --
the ``Adw.NavigationSplitView`` with the Tasks and Config Views -- lands with
the shell tracer (#56); this ticket only proves the app boots.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.set_title("Hyprtweaker")
        self.set_default_size(1000, 700)

        placeholder = Adw.StatusPage(
            title="Hyprtweaker",
            description="The settings shell lands here.",
            icon_name="preferences-system-symbolic",
        )

        content = Adw.ToolbarView()
        content.add_top_bar(Adw.HeaderBar())
        content.set_content(placeholder)

        self.set_content(content)
