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
import subprocess
import sys
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
from pathlib import Path

import pytest

children = []
for stat in Path("/proc").glob("[0-9]*/stat"):
    try:
        name, rest = stat.read_text().split(" (", 1)[1].rsplit(") ", 1)
    except (OSError, ValueError):
        continue
    if int(rest.split()[1]) == os.getpid():
        children.append(name)
display = os.environ.get("DISPLAY") or ""
print(json.dumps({
    "environ": {name: os.environ.get(name) for name in (
        "DISPLAY", "GDK_BACKEND", "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE"
    )},
    "served": Path(f"/tmp/.X11-unix/X{display[1:]}").is_socket(),
    "children": children,
}))
"""


def test_the_route_gives_the_probe_a_private_xvfb_and_no_desktop_session(
    tmp_path: Path,
) -> None:
    # This probe never imports GTK, so a regressed route cannot map anything here.
    probe = tmp_path / "probe.py"
    probe.write_text(ENVIRONMENT_PROBE)

    result = run([*ROUTE, str(probe)], tmp_path, **DESKTOP_SESSION)

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    display = seen["environ"]["DISPLAY"]
    assert seen["environ"] == {
        "DISPLAY": display,
        "GDK_BACKEND": "x11",
        "WAYLAND_DISPLAY": None,
        "HYPRLAND_INSTANCE_SIGNATURE": None,
    }
    assert display != ":0"
    assert display.startswith(":")
    # An X server answers on it, and the only process the route started is that Xvfb.
    assert seen["served"] is True
    assert seen["children"] == ["Xvfb"]


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
print(box.width, box.height, *size, window.get_width(), window.get_height())
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
    row_width, row_height, *returned, window_width, window_height = map(
        int, result.stdout.split()
    )
    assert png_size(shot) == (row_width, row_height) == tuple(returned)
    assert 0 < row_height < window_height
    assert 0 < row_width <= window_width
