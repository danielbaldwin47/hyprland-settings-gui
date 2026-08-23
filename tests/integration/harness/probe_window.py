"""A deterministic test window: one solid colour, nothing else.

Run as a subprocess by `visual.py`, never imported by a test -- it needs GTK, and the engine
must stay importable without it (ADR-0011's seam).

Undecorated, untitled, no cursor, no client-side shadow: every pixel a screenshot catches
around this window comes from *Hyprland's* rendering -- border, rounding, gaps, shadow, and
the blur behind a translucent one -- rather than from the toolkit. A window with a title bar
would make each screenshot a picture of GTK's theme, and a GTK update would then read as a
compositor regression.

    probe_window.py <app-id> <r,g,b,a>
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

#: The window gives up on its own if a test dies without terminating it, so a crashed run
#: cannot leave a client holding a surface in a compositor nobody is watching.
LIFETIME_SECONDS = 600


def main(argv: list[str]) -> int:
    app_id = argv[1]
    red, green, blue, alpha = (float(part) for part in argv[2].split(","))
    application = Gtk.Application(application_id=app_id)

    def on_activate(app: Gtk.Application) -> None:
        window = Gtk.ApplicationWindow(application=app)
        window.set_decorated(False)
        window.set_default_size(600, 400)
        window.set_title(app_id)

        box = Gtk.Box()
        fill = Gtk.CssProvider()
        fill.load_from_data(
            f"box {{ background-color: rgba({int(red * 255)},{int(green * 255)},"
            f"{int(blue * 255)},{alpha}); }}".encode()
        )
        box.get_style_context().add_provider(fill, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window.set_child(box)

        if alpha < 1.0:
            # Without this the toolkit paints an opaque window background behind the
            # translucent box, and the compositor's blur -- the thing the translucent probe
            # exists to exercise -- never shows through.
            transparent = Gtk.CssProvider()
            transparent.load_from_data(b"window { background-color: transparent; }")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), transparent, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        window.present()
        from gi.repository import GLib

        GLib.timeout_add_seconds(LIFETIME_SECONDS, app.quit)

    application.connect("activate", on_activate)
    return application.run([argv[0]])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
