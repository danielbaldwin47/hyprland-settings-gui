"""hyprtweaker -- a GUI settings app for Hyprland's Lua config.

Two packages sit under here and the boundary between them is load-bearing
(ADR-0011): :mod:`hyprtweaker.engine` is headless and never imports ``gi``,
:mod:`hyprtweaker.ui` is the GTK4 / libadwaita shell. This module stays free of
both so that importing the package costs nothing and drags in no toolkit.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
