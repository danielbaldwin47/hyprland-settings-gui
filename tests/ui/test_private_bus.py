"""The private session bus GTK runs on (#212): it activates nothing and dies with its owner.

A `dbus-daemon --session` started with the default configuration reads
`/usr/share/dbus-1/services`, where a desktop keeps the settings portal, dconf and the
accessibility bus. A name request on such a bus starts the real services, and they can
outlive the daemon. The route's bus has a configuration of its own with no service
directory. Nothing here connects to the desktop's bus: only to an address this module
started or one a child printed.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from private_display import session_bus_config, start_bus

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "tests" / "ui"
FORGED = "unix:path=/nonexistent/owner/session-bus"


@pytest.fixture
def dbus_daemon() -> str:
    path = shutil.which("dbus-daemon")
    if path is None:
        pytest.skip("dbus-daemon is not installed")
    return path


def gone(pid: int, seconds: float = 5) -> bool:
    """Whether `/proc/<pid>` disappears within `seconds` (a killed child is reaped by init)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not Path(f"/proc/{pid}").exists():
            return True
        time.sleep(0.02)
    return False


def test_the_configuration_names_no_service_directory_and_no_include(tmp_path: Path) -> None:
    root = ElementTree.fromstring(session_bus_config(tmp_path))

    tags = {element.tag for element in root.iter()}

    assert root.tag == "busconfig"
    assert {"type", "listen", "auth", "policy", "allow"} <= tags
    assert tags.isdisjoint(
        {"servicedir", "standard_session_servicedirs", "include", "includedir", "servicehelper"}
    )
    assert root.findtext("type") == "session"
    assert root.findtext("auth") == "EXTERNAL"
    assert root.findtext("listen") == f"unix:path={tmp_path}/bus"


def test_a_started_bus_listens_on_its_own_address_and_activates_nothing(
    dbus_daemon: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gi.repository import Gio, GLib

    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", FORGED)
    bus = start_bus(dbus_daemon)
    assert bus is not None
    try:
        directory = Path(bus.directory)
        assert bus.address.startswith(f"unix:path={directory}/bus,")
        assert (directory / "bus").is_socket()
        # The daemon is not given an owner-like address to inherit, or fall back to.
        environ = Path(f"/proc/{bus.pid}/environ").read_bytes()
        assert b"DBUS_SESSION_BUS_ADDRESS" not in environ

        connection = Gio.DBusConnection.new_for_address_sync(
            bus.address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        activatable = connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListActivatableNames",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        ).unpack()[0]
        with pytest.raises(GLib.Error) as refused:
            connection.call_sync(
                "org.freedesktop.portal.Desktop",
                "/org/freedesktop/portal/desktop",
                "org.freedesktop.DBus.Peer",
                "Ping",
                None,
                None,
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
        connection.close_sync(None)
    finally:
        bus.stop()

    assert activatable == ["org.freedesktop.DBus"]
    assert (
        "ServiceUnknown" in refused.value.message or "not activatable" in refused.value.message
    )


def test_stopping_the_bus_removes_the_daemon_and_its_directory(dbus_daemon: str) -> None:
    bus = start_bus(dbus_daemon)
    assert bus is not None
    pid, directory = bus.pid, Path(bus.directory)

    bus.stop()

    assert not Path(f"/proc/{pid}").exists()
    assert not directory.exists()
    bus.stop()  # idempotent


HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from private_display import start_bus
bus = start_bus(sys.argv[2])
print(bus.pid, bus.directory, flush=True)
sys.stdin.read()
"""


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM], ids=["sigkill", "sigterm"])
def test_the_daemon_dies_with_its_owner_even_when_nothing_cleans_up(
    dbus_daemon: str, sig: signal.Signals
) -> None:
    # An oomd kill, or `timeout`, runs no cleanup in the owner (2026-10-01).
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(UI_DIR), dbus_daemon],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    pid, directory = holder.stdout.readline().split()
    assert Path(f"/proc/{pid}").exists()

    holder.send_signal(sig)
    holder.wait(timeout=10)

    assert gone(int(pid))
    # The daemon unlinked its socket as it went; at most the empty directory is left.
    leftovers = list(Path(directory).glob("*"))
    shutil.rmtree(directory, ignore_errors=True)
    assert leftovers == []


def test_a_missing_daemon_binary_gives_no_bus(tmp_path: Path) -> None:
    assert start_bus(str(tmp_path / "no-dbus-daemon")) is None
