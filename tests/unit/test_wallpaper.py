"""The wallpaper daemon, found and called through injected seams (#170, ADR-0014 §Wallpaper).

No real daemon runs here: `find` and `run` are fakes that record what they were asked, and
the runtime directory is `tmp_path`. A fake daemon is "running" when its socket file is
there, as the real one is.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from hyprtweaker.engine.tools import ToolRun
from hyprtweaker.engine.wallpaper import Shown, WallpaperError, Wallpapers

TOOLS = Path("/opt/tools")


class FakeTools:
    """A tool path holding `installed`, and a `run` that answers each verb from `replies`."""

    def __init__(self, *installed: str, replies: dict[str, ToolRun] | None = None) -> None:
        self.installed = set(installed)
        self.replies = replies or {}
        self.runs: list[tuple[str, ...]] = []

    def find(self, name: str) -> Path | None:
        return TOOLS / name if name in self.installed else None

    def run(self, argv: Sequence[str], *, timeout: float) -> ToolRun:
        command = tuple(argv)
        self.runs.append(command)
        return self.replies.get(command[1], ToolRun(command, 0, "", ""))


def running(runtime: Path, *sockets: str) -> dict[str, str]:
    """An environment whose runtime directory holds `sockets`, on display wayland-1."""
    runtime.mkdir(exist_ok=True)
    for socket in sockets:
        (runtime / socket).touch()
    return {"XDG_RUNTIME_DIR": str(runtime), "WAYLAND_DISPLAY": "wayland-1"}


def seam(tools: FakeTools, environ: dict[str, str]) -> Wallpapers:
    return Wallpapers(find=tools.find, run=tools.run, environ=environ)


@pytest.mark.parametrize(
    ("name", "socket"),
    [
        ("awww", "wayland-1-awww-daemon.sock"),  # awww 0.12
        ("swww", "wayland-1-swww-daemon..sock"),  # swww 0.11, default namespace
        ("swww", "swww-wayland-1.socket"),  # swww 0.9
    ],
)
def test_a_running_daemon_sets_the_wallpaper_in_one_call(
    tmp_path: Path, name: str, socket: str
) -> None:
    tools = FakeTools(name)
    daemon = seam(tools, running(tmp_path / "run", socket)).detect()
    assert daemon is not None

    daemon.set(Path("/pictures/nord.png"))

    assert tools.runs == [(f"/opt/tools/{name}", "img", "/pictures/nord.png")]


def test_awww_is_used_over_swww_when_both_run(tmp_path: Path) -> None:
    tools = FakeTools("swww", "awww")
    environ = running(
        tmp_path / "run", "wayland-1-swww-daemon..sock", "wayland-1-awww-daemon.sock"
    )

    daemon = seam(tools, environ).detect()

    assert daemon is not None and daemon.name == "awww"


def test_a_daemon_for_another_display_is_not_this_sessions(tmp_path: Path) -> None:
    tools = FakeTools("swww")
    wallpapers = seam(tools, running(tmp_path / "run", "wayland-0-swww-daemon..sock"))

    assert wallpapers.detect() is None
    assert wallpapers.absent_reason() == (
        "swww is installed, but its daemon is not running. Start swww-daemon to change "
        "the wallpaper from this app."
    )
    assert tools.runs == []


def test_with_no_daemon_nothing_is_detected_and_nothing_runs(tmp_path: Path) -> None:
    tools = FakeTools()
    wallpapers = seam(tools, running(tmp_path / "run"))

    assert wallpapers.detect() is None
    assert wallpapers.absent_reason() == (
        "No wallpaper daemon is running. This app changes the wallpaper through awww or swww."
    )
    assert tools.runs == []


def test_hyprpaper_is_detected_and_named_but_never_called(tmp_path: Path) -> None:
    """hyprpaper 0.8 is driven over Hyprwire, a binary protocol (hyprpaper v0.8.4
    `src/ipc/IPC.cpp`), and its text commands only through `hyprctl`, which the engine
    may not run: the user is told rather than left with a wallpaper that never changes."""
    tools = FakeTools("hyprpaper")
    environ = running(tmp_path / "run")
    environ["HYPRLAND_INSTANCE_SIGNATURE"] = "abc"
    (tmp_path / "run" / "hypr" / "abc").mkdir(parents=True)
    (tmp_path / "run" / "hypr" / "abc" / ".hyprpaper.sock").touch()
    wallpapers = seam(tools, environ)

    assert wallpapers.detect() is None
    assert wallpapers.absent_reason() == (
        "hyprpaper is running, and this app cannot change its wallpaper yet. "
        "awww and swww are supported."
    )
    assert tools.runs == []


def test_the_shown_wallpaper_is_read_per_output(tmp_path: Path) -> None:
    query = ToolRun(
        ("/opt/tools/swww", "query"),
        0,
        ": DP-1: 2560x1440, scale: 1, currently displaying: image: /pictures/a b.png\n"
        ": HDMI-A-1: 1920x1080, scale: 1.5, currently displaying: image: /pictures/c.jpg\n"
        ": eDP-1: 1920x1200, scale: 1, currently displaying: color: 000000\n"
        ": DP-2: 1920x1080, scale: 1, currently displaying: image: -rf.png\n",
        "",
    )
    tools = FakeTools("swww", replies={"query": query})
    daemon = seam(tools, running(tmp_path / "run", "wayland-1-swww-daemon..sock")).detect()
    assert daemon is not None

    assert daemon.current() == (
        Shown("DP-1", Path("/pictures/a b.png")),
        Shown("HDMI-A-1", Path("/pictures/c.jpg")),
    )


def test_an_older_query_line_without_a_namespace_reads_the_same(tmp_path: Path) -> None:
    query = ToolRun(
        ("/opt/tools/swww", "query"),
        0,
        "DP-1: 2560x1440, scale: 1, currently displaying: image: /pictures/a.png\n",
        "",
    )
    tools = FakeTools("swww", replies={"query": query})
    daemon = seam(tools, running(tmp_path / "run", "swww-wayland-1.socket")).detect()
    assert daemon is not None

    assert daemon.current() == (Shown("DP-1", Path("/pictures/a.png")),)


def test_putting_back_two_outputs_sends_each_image_to_its_outputs(tmp_path: Path) -> None:
    tools = FakeTools("awww")
    daemon = seam(tools, running(tmp_path / "run", "wayland-1-awww-daemon.sock")).detect()
    assert daemon is not None

    daemon.show(
        (
            Shown("DP-1", Path("/pictures/a.png")),
            Shown("HDMI-A-1", Path("/pictures/c.jpg")),
            Shown("DP-2", Path("/pictures/a.png")),
        )
    )

    assert tools.runs == [
        ("/opt/tools/awww", "img", "--outputs", "DP-1,DP-2", "/pictures/a.png"),
        ("/opt/tools/awww", "img", "--outputs", "HDMI-A-1", "/pictures/c.jpg"),
    ]


def test_a_failing_daemon_says_what_it_said(tmp_path: Path) -> None:
    refused = ToolRun(
        ("/opt/tools/swww", "img"), 1, "", "Error: failed to connect to the socket\nmore\n"
    )
    tools = FakeTools("swww", replies={"img": refused})
    daemon = seam(tools, running(tmp_path / "run", "wayland-1-swww-daemon..sock")).detect()
    assert daemon is not None

    with pytest.raises(WallpaperError) as raised:
        daemon.set(Path("/pictures/nord.png"))

    assert str(raised.value) == "swww said: Error: failed to connect to the socket"


def test_a_relative_image_is_refused_before_anything_runs(tmp_path: Path) -> None:
    tools = FakeTools("swww")
    daemon = seam(tools, running(tmp_path / "run", "wayland-1-swww-daemon..sock")).detect()
    assert daemon is not None

    with pytest.raises(ValueError, match="absolute"):
        daemon.set(Path("nord.png"))

    assert tools.runs == []


def test_no_daemon_config_is_ever_written(tmp_path: Path) -> None:
    """ADR-0006 and ADR-0014: the app drives a running daemon and never configures one."""
    home = tmp_path / "home"
    for directory in ("swww", "awww", "hypr"):
        (home / ".config" / directory).mkdir(parents=True)
    (home / ".config" / "hypr" / "hyprpaper.conf").write_text("preload = /a.png\n")
    before = sorted((p, p.read_bytes() if p.is_file() else b"") for p in home.rglob("*"))
    tools = FakeTools("swww")
    environ = running(tmp_path / "run", "wayland-1-swww-daemon..sock")
    environ["HOME"] = str(home)
    environ["XDG_CONFIG_HOME"] = str(home / ".config")
    daemon = seam(tools, environ).detect()
    assert daemon is not None

    daemon.set(Path("/pictures/nord.png"))
    daemon.current()

    after = sorted((p, p.read_bytes() if p.is_file() else b"") for p in home.rglob("*"))
    assert after == before
    assert [run[1] for run in tools.runs] == ["img", "query"]
