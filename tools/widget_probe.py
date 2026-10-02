"""Run a widget probe on a private Xvfb: the only sanctioned way to start GTK outside pytest.

    .venv/bin/python tools/widget_probe.py <probe.py> [args...]

A widget probe builds part of the app in-process, drives it, and reads widget properties
(`docs/agents/local-checks.md` § Running the app). Its first line is `import widget_probe`,
before `gi` and before any `hyprtweaker.ui` import, since either initialises GTK:

    import widget_probe

    from gi.repository import Gtk
    ...
    widget_probe.shoot(page.page, "/path/to/page.png")  # a PNG cropped to that widget

This runner starts an Xvfb of its own (the UI tier's, `tests/ui/private_display.py`), sets
`DISPLAY` to it and `GDK_BACKEND=x11`, drops `WAYLAND_DISPLAY` and
`HYPRLAND_INSTANCE_SIGNATURE`, points `XDG_CONFIG_HOME` and `XDG_STATE_HOME` at a throwaway
directory, and runs the probe in its own process with the worktree's `src` importable.

The import is the fence: imported by a probe this runner did not start, `widget_probe`
exits at once with one line naming this command, before GTK can open a display. On
2026-10-01 probes run under bare `xvfb-run` mapped windows on the owner's desktop (#202):
the session exports `GDK_BACKEND=wayland,x11,*` and `WAYLAND_DISPLAY`, which `xvfb-run`
leaves alone, and unsetting `WAYLAND_DISPLAY` alone sends GTK to the session's `DISPLAY`,
XWayland on the desktop. A probe that skips the import is fenced by nothing but the docs.
"""

from __future__ import annotations

import os
import runpy
import shutil
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
COMMAND = ".venv/bin/python tools/widget_probe.py <probe.py> [args...]"

sys.path.insert(0, str(REPO_ROOT / "tests" / "ui"))
from private_display import pin_environment, start_xvfb  # noqa: E402

# The Xvfb this process started, set only by `main`. A probe that imports this module
# without the runner gets a fresh copy, where it is None, so the fence refuses.
_route_display: str | None = None


def fence_problems(environ: Mapping[str, str]) -> list[str]:
    """What in `environ` could let GTK reach a display other than this route's Xvfb."""
    problems = []
    display = environ.get("DISPLAY")
    if _route_display is None or display != _route_display:
        problems.append(
            f"DISPLAY={display} is not an Xvfb this route started"
            if display
            else "DISPLAY is unset"
        )
    if "WAYLAND_DISPLAY" in environ:
        problems.append(f"WAYLAND_DISPLAY={environ['WAYLAND_DISPLAY']} is set")
    if environ.get("GDK_BACKEND") != "x11":
        problems.append(f"GDK_BACKEND={environ.get('GDK_BACKEND', '')} is not x11")
    return problems


def refuse_unless_routed() -> None:
    """Exit with one line naming the route's command if GTK could leave its Xvfb."""
    if problems := fence_problems(os.environ):
        raise SystemExit(
            f"widget_probe: refusing to start GTK: {', '.join(problems)}, so this probe "
            f"could map on the desktop session; run it with {COMMAND}"
        )


def settle(seconds: float = 0.5) -> None:
    """Run GTK's main loop for `seconds`: layout, frame clock ticks, short animations."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = GLib.get_monotonic_time() + int(seconds * 1_000_000)
    while GLib.get_monotonic_time() < deadline:
        context.iteration(False)


def shoot(widget: Any, path: str | os.PathLike[str]) -> tuple[int, int]:
    """Write a PNG of `widget` as drawn in its window, cropped to its bounds.

    The widget must already be in a window (`window.set_child(...)`); this presents the
    window, waits for the widget to be mapped and laid out, then renders the window and
    keeps the widget's rectangle, background included. Returns the PNG's size.
    """
    from gi.repository import GLib, Graphene, Gtk

    window = widget.get_root()
    if window is None:
        raise ValueError("shoot: the widget is in no window; set it as a window's child first")
    window.present()
    deadline = GLib.get_monotonic_time() + 5_000_000
    while not (widget.get_mapped() and widget.get_width() > 0):
        if GLib.get_monotonic_time() > deadline:
            raise RuntimeError("shoot: the widget was not mapped within 5 s")
        GLib.MainContext.default().iteration(False)
    settle()

    found, bounds = widget.compute_bounds(window)
    if not found:
        raise RuntimeError("shoot: the widget has no bounds in its window")
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(window).snapshot(snapshot, window.get_width(), window.get_height())
    width, height = round(bounds.get_width()), round(bounds.get_height())
    viewport = Graphene.Rect().init(bounds.get_x(), bounds.get_y(), width, height)
    texture = window.get_renderer().render_texture(snapshot.to_node(), viewport)
    texture.save_to_png(os.fspath(path))
    return texture.get_width(), texture.get_height()


def main(argv: list[str]) -> int:
    global _route_display
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    probe = Path(argv[0]).resolve()
    if not probe.is_file():
        raise SystemExit(f"widget_probe: no probe script at {probe}")
    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        raise SystemExit("widget_probe: Xvfb is not installed (pacman -S xorg-server-xvfb)")
    display = start_xvfb(xvfb)
    if display is None:
        raise SystemExit("widget_probe: Xvfb did not open a display within 10 s")

    _route_display = display
    pin_environment(os.environ, display)
    # Versions only, which opens no display: the probe then imports Gtk and Adw as is.
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("Adw", "1")
    # The probe's `import widget_probe` must find this module, not a fresh copy of it.
    sys.modules["widget_probe"] = sys.modules[__name__]
    sys.path[0:0] = [str(probe.parent), str(REPO_ROOT / "src")]
    sys.argv = [str(probe), *argv[1:]]
    with tempfile.TemporaryDirectory(prefix="widget-probe-") as sandbox:
        os.environ["XDG_CONFIG_HOME"] = str(Path(sandbox) / "config")
        os.environ["XDG_STATE_HOME"] = str(Path(sandbox) / "state")
        runpy.run_path(str(probe), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
else:
    refuse_unless_routed()
