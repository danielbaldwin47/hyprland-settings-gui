"""The navigate-and-flash pulse, shared by everything that jumps to a Row.

Two features navigate the user somewhere they did not scroll to: the conflict jump on the
Binds page (ADR-0007) and a Search hit (ADR-0017). Both owe the reader the same thing --
a moment's mark on the Row that just arrived, because scrolling something into view is
invisible to someone whose eyes were on the popover or the search entry they just typed
into. One pulse, defined once: two copies would drift in duration and colour, and "the
flash looks different depending on how you got here" is a bug nobody would think to file.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gdk, GLib, Gtk  # noqa: E402

FLASH_CLASS = "hyprtweaker-jump-flash"
FLASH_MS = 1200

_installed = False


def install_flash_css() -> None:
    """The pulse's one CSS rule, installed once per process.

    Lazy rather than at import: the UI smoke tier constructs Pages against whatever display
    the harness has, and a missing display must degrade to no flash rather than to a crash
    -- which is also what makes this importable from a module a headless test touches.
    """
    global _installed
    if _installed:
        return
    display = Gdk.Display.get_default()
    if display is None:  # pragma: no cover - no-display environments only
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(
        f"row.{FLASH_CLASS} {{"
        " background-color: alpha(@warning_bg_color, 0.35);"
        " transition: background-color 0.6s ease;"
        " }"
    )
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _installed = True


def flash(widget: Gtk.Widget) -> None:
    """Pulse `widget`'s background briefly, then put it back.

    Deliberately not paired with focus or scrolling: the two callers reach their Row by
    different routes -- the Binds page grabs focus, a Search hit scrolls explicitly because
    an insensitive Row cannot be focused at all -- and folding either into the pulse would
    make this the navigation rather than the mark on its end.
    """
    install_flash_css()
    widget.add_css_class(FLASH_CLASS)

    def unflash(target: Gtk.Widget = widget) -> bool:
        target.remove_css_class(FLASH_CLASS)
        return False  # one-shot

    GLib.timeout_add(FLASH_MS, unflash)
