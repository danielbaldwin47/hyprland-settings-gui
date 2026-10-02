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
directory, runs the app non-unique as `tools/sandbox.py` does, and runs the probe in its own
process with the worktree's `src` importable.

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
from collections.abc import Callable, Mapping
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


def _run_until(done: Callable[[], bool], seconds: float) -> bool:
    """Run GTK's main loop until `done()` holds or `seconds` pass; say whether it held.

    It sleeps between events (`iteration(True)`), woken by a timeout at the deadline, so
    waiting costs no CPU: a busy `iteration(False)` loop pinned a core per probe.
    """
    from gi.repository import GLib

    expired = False

    def expire() -> bool:
        nonlocal expired
        expired = True
        return GLib.SOURCE_REMOVE

    context = GLib.MainContext.default()
    timer = GLib.timeout_add(max(1, round(seconds * 1000)), expire)
    while not (held := done()) and not expired:
        context.iteration(True)
    if not expired:
        GLib.source_remove(timer)
    return held


def settle(seconds: float = 0.5) -> None:
    """Run GTK's main loop for `seconds`: layout, frame clock ticks, short animations."""
    _run_until(lambda: False, seconds)


def shoot(widget: Any, path: str | os.PathLike[str]) -> tuple[int, int]:
    """Write a PNG of `widget` as drawn on its surface, cropped to its bounds.

    The widget must already be in a window (`window.set_child(...)`) or in a popover
    whose button is (`popover.popup()` first). This presents the window, waits for the
    widget to be mapped and laid out, then renders the surface the widget is drawn on (the
    window, or the popover: a popover is a surface of its own) and keeps the widget's
    rectangle, background included. Returns the PNG's size.
    """
    from gi.repository import Graphene, Gtk

    window, native = widget.get_root(), widget.get_native()
    if window is None or native is None:
        raise ValueError("shoot: the widget is in no window; set it as a window's child first")
    window.present()
    if not _run_until(lambda: widget.get_mapped() and widget.get_width() > 0, 5):
        hint = "; call popover.popup() first" if native is not window else ""
        raise RuntimeError(f"shoot: the widget was not mapped within 5 s{hint}")
    settle()

    found, bounds = widget.compute_bounds(native)
    if not found:
        raise RuntimeError("shoot: the widget has no bounds on its surface")
    snapshot = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(native).snapshot(snapshot, native.get_width(), native.get_height())
    width, height = round(bounds.get_width()), round(bounds.get_height())
    viewport = Graphene.Rect().init(bounds.get_x(), bounds.get_y(), width, height)
    texture = native.get_renderer().render_texture(snapshot.to_node(), viewport)
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
        # An app the probe runs must not claim the app id on the session bus, or it hands
        # its launch to the owner's open window (`hyprtweaker.application.NON_UNIQUE_ENV`).
        os.environ["HYPRTWEAKER_NON_UNIQUE"] = "1"
        runpy.run_path(str(probe), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
else:
    refuse_unless_routed()
