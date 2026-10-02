"""The widget probe route (`tools/widget_probe.py`) puts GTK on a private Xvfb or nowhere.

On 2026-10-01 a probe run under `xvfb-run` mapped a window on the owner's desktop three
times (#202): the session exports `GDK_BACKEND=wayland,x11,*` and `WAYLAND_DISPLAY`, and
`xvfb-run` overrides neither. Each test runs a probe in a child process with such a
session in its environment. Where the probe imports GTK, every name in that session is
one nothing listens on, so a regressed fence falls through to no display, never to the
desktop's.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROUTE = [sys.executable, str(ROOT / "tools" / "widget_probe.py")]
ROUTE_COMMAND = ".venv/bin/python tools/widget_probe.py <probe.py> [args...]"

DESKTOP_SESSION = {
    "WAYLAND_DISPLAY": "wayland-1",
    "GDK_BACKEND": "wayland,x11,*",
    "DISPLAY": ":0",
    "HYPRLAND_INSTANCE_SIGNATURE": "desktop-session-signature",
    "GDK_SCALE": "2",
    "XDG_CONFIG_HOME": "/home/owner/.config",
    "XDG_STATE_HOME": "/home/owner/.local/state",
}
DEAD_DISPLAY = ":4095"


def dead_session(tmp_path: Path) -> dict[str, str | None]:
    """A desktop session's variables, naming a display and a socket that do not exist."""
    assert not Path(f"/tmp/.X11-unix/X{DEAD_DISPLAY[1:]}").exists()
    runtime = tmp_path / "run"
    runtime.mkdir()
    return {
        **DESKTOP_SESSION,
        "WAYLAND_DISPLAY": "wayland-absent",
        "DISPLAY": DEAD_DISPLAY,
        "XDG_RUNTIME_DIR": str(runtime),
        # Where the owner's session bus lives, named but with nothing behind it.
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }


def run(
    command: list[str], tmp_path: Path, **env: str | None
) -> subprocess.CompletedProcess[str]:
    child_env = {key: value for key, value in os.environ.items() if key not in env}
    child_env.update({key: value for key, value in env.items() if value is not None})
    return subprocess.run(
        command, cwd=tmp_path, env=child_env, capture_output=True, text=True, timeout=120
    )


ENVIRONMENT_PROBE = """\
import widget_probe

import json
import os
import sys
import time
from pathlib import Path

import pytest
from gi.repository import Gio, GLib  # Gio opens no display; the probe never imports Gtk

children = {}
for stat in Path("/proc").glob("[0-9]*/stat"):
    try:
        name, rest = stat.read_text().split(" (", 1)[1].rsplit(") ", 1)
    except (OSError, ValueError):
        continue
    if int(rest.split()[1]) == os.getpid():
        children[name] = int(stat.parent.name)
display = os.environ.get("DISPLAY") or ""
address = os.environ.get("DBUS_SESSION_BUS_ADDRESS") or ""

# Only the address this route gave the probe is ever connected to.
activatable = refusal = None
if "dbus-daemon" in children:
    connection = Gio.DBusConnection.new_for_address_sync(
        address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )

    def call(name, path, interface, method):
        return connection.call_sync(
            name, path, interface, method, None, None, Gio.DBusCallFlags.NONE, 5000, None
        )

    activatable = call(
        "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
        "ListActivatableNames",
    ).unpack()[0]
    try:
        call(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
            "org.freedesktop.DBus.Peer", "Ping",
        )
    except GLib.Error as error:
        refusal = error.message
    daemon_environ = Path(f"/proc/{children['dbus-daemon']}/environ").read_bytes()
    daemon_has_session_address = b"DBUS_SESSION_BUS_ADDRESS" in daemon_environ
socket_path = address.split("unix:path=")[-1].split(",")[0]
print(json.dumps({
    "environ": {name: os.environ.get(name) for name in (
        "DISPLAY", "GDK_BACKEND", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE",
        "GDK_SCALE", "GTK_A11Y", "HYPRTWEAKER_NON_UNIQUE", "DBUS_SESSION_BUS_ADDRESS",
        "GSETTINGS_BACKEND", "ADW_DISABLE_PORTAL",
    )},
    "config": os.environ["XDG_CONFIG_HOME"],
    "state": os.environ["XDG_STATE_HOME"],
    "homes": {name: os.environ.get(name) for name in (
        "HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "HYPRTWEAKER_TOOL_PATH",
    )},
    "tools": sorted(os.listdir(os.environ["HYPRTWEAKER_TOOL_PATH"])),
    "served": Path(f"/tmp/.X11-unix/X{display[1:]}").is_socket(),
    "children": children,
    "socket": socket_path,
    "socket_inode": os.stat(socket_path).st_ino,
    "activatable": activatable,
    "refusal": refusal,
    "daemon_has_session_address": daemon_has_session_address,
}), flush=True)
if sys.argv[1:] == ["hold"]:
    time.sleep(60)
"""


def test_the_route_gives_the_probe_a_private_xvfb_and_no_desktop_session(
    tmp_path: Path,
) -> None:
    # This probe never imports GTK, so a regressed route cannot map anything here.
    probe = tmp_path / "probe.py"
    probe.write_text(ENVIRONMENT_PROBE)

    session = dead_session(tmp_path)
    result = run([*ROUTE, str(probe)], tmp_path, **session, GTK_A11Y=None)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    display = seen["environ"]["DISPLAY"]
    bus = seen["environ"]["DBUS_SESSION_BUS_ADDRESS"]
    # GDK_SCALE halved the screen on the owner's HiDPI desktop; GTK_A11Y keeps the widgets
    # off the desktop's screen reader; non-unique keeps an app the probe runs from handing
    # its launch to the owner's open window over the session bus. The session bus is the
    # route's own, with GSettings in memory and no settings portal, so a render does not
    # depend on the owner's colour scheme.
    assert seen["environ"] == {
        "DISPLAY": display,
        "GDK_BACKEND": "x11",
        "WAYLAND_DISPLAY": None,
        "HYPRLAND_INSTANCE_SIGNATURE": None,
        "GDK_SCALE": None,
        "GTK_A11Y": "none",
        "HYPRTWEAKER_NON_UNIQUE": "1",
        "DBUS_SESSION_BUS_ADDRESS": bus,
        "GSETTINGS_BACKEND": "memory",
        "ADW_DISABLE_PORTAL": "1",
    }
    # The config and state dirs are a throwaway pair, not the owner's.
    config, state = Path(seen["config"]), Path(seen["state"])
    assert (config.name, state.name) == ("config", "state")
    assert config.parent == state.parent
    assert config.parent.name.startswith("widget-probe-")
    assert not config.parent.exists()  # removed when the probe ended
    # Home and the tool search path are in the same throwaway directory, and no tool is on
    # that path: an app the probe runs finds no theming tool or wallpaper daemon (#233).
    sandbox = config.parent
    assert seen["homes"] == {
        "HOME": str(sandbox),
        "XDG_DATA_HOME": str(sandbox / "data"),
        "XDG_CACHE_HOME": str(sandbox / "cache"),
        "HYPRTWEAKER_TOOL_PATH": str(sandbox / "bin"),
    }
    assert seen["tools"] == []
    assert display != ":0"
    assert display.startswith(":")
    # An X server answers on it, and the only processes the route started are that Xvfb and
    # the bus daemon.
    assert seen["served"] is True
    assert sorted(seen["children"]) == ["Xvfb", "dbus-daemon"]


def test_the_probe_runs_on_a_session_bus_that_is_not_the_owners_and_activates_nothing(
    tmp_path: Path,
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(ENVIRONMENT_PROBE)
    session = dead_session(tmp_path)

    result = run([*ROUTE, str(probe)], tmp_path, **session)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    bus = seen["environ"]["DBUS_SESSION_BUS_ADDRESS"]
    # Not the address the route was started with, and not the owner's socket: compared by
    # string and by inode, never by connecting to either.
    assert bus != session["DBUS_SESSION_BUS_ADDRESS"]
    owner_socket = Path(f"/run/user/{os.getuid()}/bus")
    assert seen["socket"] != str(owner_socket)
    if owner_socket.exists():
        assert seen["socket_inode"] != owner_socket.stat().st_ino
    # Nothing on it can be activated: no service directory, so the settings portal, dconf
    # and the accessibility bus answer ServiceUnknown instead of starting.
    assert seen["activatable"] == ["org.freedesktop.DBus"]
    assert "ServiceUnknown" in seen["refusal"] or "not activatable" in seen["refusal"]
    assert seen["daemon_has_session_address"] is False


def readable_environs_naming(needle: str) -> list[str]:
    """The pids of processes this user can read whose environment contains `needle`."""
    found = []
    for environ in Path("/proc").glob("[0-9]*/environ"):
        try:
            if needle.encode() in environ.read_bytes():
                found.append(environ.parent.name)
        except OSError:
            continue
    return found


def wait_gone(pids: list[int], seconds: float = 5) -> list[int]:
    """The pids still in /proc after `seconds`."""
    deadline = time.monotonic() + seconds
    alive = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
    while alive and time.monotonic() < deadline:
        time.sleep(0.02)
        alive = [pid for pid in alive if Path(f"/proc/{pid}").exists()]
    return alive


@pytest.mark.parametrize("ending", ["exits", "killed by timeout"])
def test_no_bus_daemon_or_activated_helper_outlives_the_route(
    tmp_path: Path, ending: str
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(ENVIRONMENT_PROBE)
    command = [*ROUTE, str(probe)]
    if ending != "exits":
        # A kill no cleanup runs in: the daemon and the Xvfb must go with their parent.
        command = ["timeout", "8", *command, "hold"]

    result = run(command, tmp_path, **dead_session(tmp_path))

    assert result.returncode == (0 if ending == "exits" else 124), result.stderr
    seen = json.loads(result.stdout)
    children = list(seen["children"].values())
    assert wait_gone(children) == []
    # Whatever a name request on the bus had started would carry its address in its
    # environment: nothing does, and the directory that held the socket is gone.
    directory = str(Path(seen["socket"]).parent)
    assert readable_environs_naming(directory) == []
    assert (not Path(directory).exists()) if ending == "exits" else True
    leftovers = list(Path(directory).glob("*")) if Path(directory).exists() else []
    shutil.rmtree(directory, ignore_errors=True)
    assert leftovers == []


GTK_PROBE = """\
import widget_probe

print("past the fence", flush=True)
from gi.repository import Gtk
"""


@pytest.mark.parametrize(
    ("session", "expected"),
    [
        pytest.param(
            {"WAYLAND_DISPLAY": "wayland-absent", "GDK_BACKEND": "wayland,x11,*"},
            "WAYLAND_DISPLAY=wayland-absent is set",
            id="wayland-display-set",
        ),
        pytest.param(
            {"WAYLAND_DISPLAY": None, "GDK_BACKEND": "x11"},
            "DISPLAY=:4095 is not an Xvfb this route started",
            id="foreign-display",
        ),
        pytest.param(
            {"WAYLAND_DISPLAY": None, "GDK_BACKEND": "x11"},
            "DBUS_SESSION_BUS_ADDRESS=unix:path=",
            id="the-sessions-bus",
        ),
        pytest.param(
            {"WAYLAND_DISPLAY": None, "GDK_BACKEND": "x11", "DBUS_SESSION_BUS_ADDRESS": None},
            "DBUS_SESSION_BUS_ADDRESS is unset, so GTK falls back to the desktop's bus",
            id="no-bus-address",
        ),
    ],
)
def test_a_probe_started_outside_the_route_stops_before_gtk_naming_the_route(
    tmp_path: Path, session: dict[str, str | None], expected: str
) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(GTK_PROBE)
    environment = {**dead_session(tmp_path), **session}

    result = run(
        [sys.executable, str(probe)],
        tmp_path,
        PYTHONPATH=f"{ROOT / 'tools'}:{ROOT / 'src'}",
        **environment,
    )

    assert result.returncode == 1
    assert "past the fence" not in result.stdout
    assert result.stderr.splitlines() == [result.stderr.strip()]  # one line, no traceback
    assert result.stderr.startswith("widget_probe: refusing to start GTK: ")
    assert expected in result.stderr
    assert result.stderr.strip().endswith(f"run it with {ROUTE_COMMAND}")


SCREENSHOT_PROBE = """\
import widget_probe

import dataclasses
import sys
import tempfile
from pathlib import Path

from gi.repository import Adw, Gtk

from hyprtweaker.engine.ipc import NoInstance
from hyprtweaker.engine.model.entities import Bind, DispatcherCall
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.session import Session
from hyprtweaker.ui.pages.binds import BindActions, BindsPage


def offline():
    raise NoInstance("widget probe")


Adw.init()
session = Session(
    spawn=lambda coro: coro.close(),
    paths=ConfigPaths.rooted_at(Path(tempfile.mkdtemp())),
    app_version="0",
    connect=offline,
)
kitty = DispatcherCall(path="exec_cmd", positional=("kitty",))
session.model.entities.binds.append(Bind(keys="SUPER + Return", dispatcher=kitty))
noop = lambda *_args: None
page = BindsPage(
    session, actions=BindActions(**{f.name: noop for f in dataclasses.fields(BindActions)})
)
page.refresh()
window = Gtk.Window(default_width=900, default_height=600)
window.set_child(page.page)
row = page.rows[0].widget
size = widget_probe.shoot(row, sys.argv[1])
box = row.get_allocation()  # the border box: what the user sees of the row
margined = widget_probe.shoot(row, sys.argv[1] + ".margin.png", margin=8)
print(box.width, box.height, *size, window.get_width(), window.get_height(), *margined)
"""


def png_size(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def test_the_route_writes_a_png_cropped_to_one_widget_of_a_real_page(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(SCREENSHOT_PROBE)
    shot = tmp_path / "row.png"

    result = run([*ROUTE, str(probe), str(shot)], tmp_path, **dead_session(tmp_path))

    assert result.returncode == 0, result.stderr
    numbers = [int(number) for number in result.stdout.split()]
    row_width, row_height, *returned, window_width, window_height = numbers[:6]
    margined = tuple(numbers[6:])
    assert png_size(shot) == (row_width, row_height) == tuple(returned)
    assert 0 < row_height < window_height
    assert 0 < row_width <= window_width
    # A margin takes that much of the surface around the widget, where a group title's
    # glyphs reach past its box; the row spans the page, so the sides stop at the window.
    assert png_size(Path(f"{shot}.margin.png")) == margined
    assert margined[1] == row_height + 16


POPOVER_PROBE = """\
import widget_probe

import sys

from gi.repository import Gdk, GdkPixbuf, Gtk

css = Gtk.CssProvider()
css.load_from_string(".probe-red { background: #ff0000; }")
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER
)
red = Gtk.Box(width_request=120, height_request=60, css_classes=["probe-red"])
popover = Gtk.Popover(child=red, autohide=False)
button = Gtk.MenuButton(label="Open", popover=popover, halign=Gtk.Align.START,
                        valign=Gtk.Align.START)
window = Gtk.Window(default_width=600, default_height=400)
window.set_child(button)
window.present()
popover.popup()
size = widget_probe.shoot(red, sys.argv[1])
pixbuf = GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
row = pixbuf.get_rowstride() * (pixbuf.get_height() // 2)
center = pixbuf.get_pixels()[row + pixbuf.get_n_channels() * (pixbuf.get_width() // 2):][:3]
print(*size, *center)
"""


def test_a_widget_in_a_popover_is_shot_from_the_popover_not_the_window(
    tmp_path: Path,
) -> None:
    # A popover is a surface of its own: cropping the window at the widget's bounds shows
    # whatever of the window lies under it, the #101 probe's wrong picture.
    probe = tmp_path / "probe.py"
    probe.write_text(POPOVER_PROBE)
    shot = tmp_path / "red.png"

    result = run([*ROUTE, str(probe), str(shot)], tmp_path, **dead_session(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["120", "60", "255", "0", "0"]
    assert png_size(shot) == (120, 60)


SCROLLED_PROBE = """\
import widget_probe

import sys

from gi.repository import Gtk

target = Gtk.Box(height_request=200)
column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
column.append(Gtk.Box(height_request=400))
column.append(target)
column.append(Gtk.Box(height_request=400))
scrolled = Gtk.ScrolledWindow(child=column)
window = Gtk.Window(default_width=400, default_height=300)
window.set_child(scrolled)
window.present()
widget_probe.settle()
scrolled.get_vadjustment().set_value(500)  # the target's top 100 px above the view
widget_probe.settle()
widget_probe.shoot(target, sys.argv[1])
"""


def test_a_widget_partly_scrolled_out_of_view_is_refused_not_shot(tmp_path: Path) -> None:
    # The #177 shot cut the group's title off: the window has other pixels where the
    # scrolled-away part would be, and nothing said so.
    probe = tmp_path / "probe.py"
    probe.write_text(SCROLLED_PROBE)
    shot = tmp_path / "target.png"

    result = run([*ROUTE, str(probe), str(shot)], tmp_path, **dead_session(tmp_path))

    assert result.returncode == 1
    assert not shot.exists()
    assert result.stderr.strip().splitlines()[-1] == (
        "RuntimeError: shoot: 100 of the widget's 200 px rows are scrolled out of view; "
        "scroll it into view or make the window larger first"
    )


SHADOWED_PROBE = """\
import widget_probe

import sys

from gi.repository import Adw, Gdk, GdkPixbuf, Gtk

Adw.init()
css = Gtk.CssProvider()
css.load_from_string(".probe-red { background: #ff0000; }")
Gtk.StyleContext.add_provider_for_display(
    Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_USER
)
red = Gtk.Box(width_request=120, height_request=60, css_classes=["probe-red"],
              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
window = Adw.Window(default_width=600, default_height=400, content=red)
widget_probe.shoot(red, sys.argv[1], margin=1)
pixbuf = GdkPixbuf.Pixbuf.new_from_file(sys.argv[1])
pixels, stride, step = pixbuf.get_pixels(), pixbuf.get_rowstride(), pixbuf.get_n_channels()
middle = stride * (pixbuf.get_height() // 2)
red = [tuple(pixels[middle + step * x :][:3]) == (255, 0, 0) for x in range(pixbuf.get_width())]
print(*red)
"""


def test_a_window_with_a_shadow_is_shot_at_one_to_one(tmp_path: Path) -> None:
    # The #183 swatch shots came out scaled by 0.98 x 0.95 and blurred: the window paintable
    # of a window with a shadow is larger than the window, and was squeezed into its size.
    probe = tmp_path / "probe.py"
    probe.write_text(SHADOWED_PROBE)
    shot = tmp_path / "red.png"

    result = run([*ROUTE, str(probe), str(shot)], tmp_path, **dead_session(tmp_path))

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", *["True"] * 120, "False"]
