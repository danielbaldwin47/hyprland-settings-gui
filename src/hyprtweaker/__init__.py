"""hyprtweaker -- a GUI settings app for Hyprland's Lua config.

Two packages sit under here and the boundary between them is load-bearing
(ADR-0011): :mod:`hyprtweaker.engine` is headless and never imports ``gi``,
:mod:`hyprtweaker.ui` is the GTK4 / libadwaita shell. This module stays free of
both so that importing the package costs nothing and drags in no toolkit.

App identity lives here rather than beside the ``Adw.Application`` for that
reason: the desktop entry, the icon theme name and any future Flatpak id all key
off ``APP_ID`` (ADR-0019, "so the desktop file, icon theme name, and any future
Flatpak id never churn"), and nothing should have to import GTK to read it.
"""

from __future__ import annotations

__version__ = "0.1.0"

APP_ID = "io.github.danielbaldwin47.Hyprtweaker"

__all__ = ["APP_ID", "__version__"]
