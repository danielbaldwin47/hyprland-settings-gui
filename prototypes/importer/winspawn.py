#!/usr/bin/env python3
"""PROTOTYPE — throwaway. A deterministic test window.

Solid colour, no text, no cursor, no client-side decoration — so every pixel in a
screenshot comes from Hyprland's own rendering (border, rounding, gaps, shadow,
blur behind the translucent one) rather than from the client.

    winspawn.py <app-id> <r,g,b,a>
"""
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402


def main():
    app_id = sys.argv[1]
    r, g, b, a = (float(x) for x in sys.argv[2].split(","))
    app = Gtk.Application(application_id=app_id)

    def on_activate(a_):
        win = Gtk.ApplicationWindow(application=a_)
        win.set_decorated(False)
        win.set_default_size(600, 400)
        win.set_title(app_id)
        box = Gtk.Box()
        css = Gtk.CssProvider()
        css.load_from_data(
            f"box {{ background-color: rgba({int(r*255)},{int(g*255)},"
            f"{int(b*255)},{a}); }}".encode())
        box.get_style_context().add_provider(css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win.set_child(box)
        if a < 1.0:
            win.add_css_class("transparent")
            wcss = Gtk.CssProvider()
            wcss.load_from_data(b"window { background-color: transparent; }")
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(), wcss,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        win.present()
        GLib.timeout_add_seconds(600, lambda: a_.quit())

    app.connect("activate", on_activate)
    app.run([sys.argv[0]])


if __name__ == "__main__":
    main()
