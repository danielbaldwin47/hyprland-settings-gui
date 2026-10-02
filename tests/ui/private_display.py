"""A private Xvfb for GTK, and the environment that keeps GTK on it.

Shared by the UI tier (`conftest.py`) and the widget probe route (`tools/widget_probe.py`,
#202), so both fence GTK off the desktop session the same way. Starting an Xvfb is not
enough on its own: a desktop session exports `GDK_BACKEND=wayland,x11,*` and
`WAYLAND_DISPLAY`, so GTK picks Wayland first, and its x11 fallback finds the session's
`DISPLAY`, which is XWayland on the desktop. `pin_environment` overrides all of them.
"""

from __future__ import annotations

import ctypes
import os
import signal
import socket
import subprocess
import time
from collections.abc import MutableMapping

# Every variable `pin_environment` sets or removes, for a caller that restores them.
PINNED = (
    "DISPLAY",
    "GDK_BACKEND",
    "WAYLAND_DISPLAY",
    "HYPRLAND_INSTANCE_SIGNATURE",
    "GDK_SCALE",
    "GTK_A11Y",
)

_libc = ctypes.CDLL(None, use_errno=True)
_PR_SET_PDEATHSIG = 1


# Agent X servers take a display number from this range and nowhere else. An X server on
# a number the desktop session holds replaces its socket: xtrans unlinks a listening path
# like /tmp/.X11-unix/X0 before binding its own, and `-displayfd`, which walks up from 0,
# also turns off the lock-file check that would have stopped it. On 2026-10-02 that took
# the owner's display :0 from Hyprland's Xwayland.
PRIVATE_DISPLAYS = range(200, 1000)
_SOCKET = "/tmp/.X11-unix/X{}"
_LOCK = "/tmp/.X{}-lock"
# Xvfb writes its lock here first and links it into place; one left by a killed server
# makes the next Xvfb on that number sleep about 6 s before it gives up (xserver os/utils.c).
_TEMP_LOCK = "/tmp/.tX{}-lock"


def display_number(name: str | None) -> int | None:
    """The number in an X display name (`:0`, `:0.0`, `unix:0`), or None for no X display."""
    _, colon, rest = (name or "").rpartition(":")
    digits = rest.partition(".")[0]
    return int(digits) if colon and digits.isdigit() else None


def session_display_clash(display: str, session: str | None) -> str | None:
    """Why `display` must not be used, or None: it is the desktop session's own display."""
    number = display_number(display)
    if number is None or number != display_number(session):
        return None
    return (
        f"refusing display {display}: it is the desktop session's own DISPLAY ({session}), "
        "where an X server replaces the session's X socket and GTK draws on the desktop; "
        f"agent X servers take displays {PRIVATE_DISPLAYS[0]}-{PRIVATE_DISPLAYS[-1]} only "
        "(docs/agents/local-checks.md § Private X displays)"
    )


def start_xvfb(xvfb: str) -> str | None:
    """Start a headless X server on a free display in `PRIVATE_DISPLAYS`; None if none came up.

    A number whose lock file, Xvfb's temporary lock or socket exists is skipped and left
    alone, whether a live server or a crashed run's leftover holds it, and so is the
    session's own number. The
    rest is settled by Xvfb's own lock: started with an explicit number (never
    `-displayfd`), it takes `/tmp/.X<n>-lock` with an atomic link() before it creates any
    socket, so of several processes starting at once one wins each number and the others
    exit and try the next.

    It dies with this process (PR_SET_PDEATHSIG), including a `timeout` kill that runs no
    cleanup, and removes its own lock and socket as it goes. Nothing stops it earlier on
    purpose: GTK keeps the connection until exit, and GDK exits the process when its X
    server goes away under it.
    """
    session = display_number(os.environ.get("DISPLAY"))
    # CI jobs have no timeout of their own, so a wedged Xvfb must not hang the run.
    deadline = time.monotonic() + 10
    for number in PRIVATE_DISPLAYS:
        if time.monotonic() > deadline:
            return None
        if number == session or any(
            os.path.lexists(path.format(number)) for path in (_LOCK, _TEMP_LOCK, _SOCKET)
        ):
            continue
        try:
            xvfb_process = subprocess.Popen(
                [xvfb, f":{number}", "-screen", "0", "1280x1024x24", "-nolisten", "tcp"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=lambda: _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM),
            )
        except OSError:
            return None
        if _serving(xvfb_process, number, deadline):
            return f":{number}"
    return None


def _serving(xvfb_process: subprocess.Popen[bytes], number: int, deadline: float) -> bool:
    """Wait until `xvfb_process` holds display `number` and accepts connections.

    False when it exits first (another process took the number) or by the deadline, when
    it is asked to stop, so it removes its own lock and socket, and killed only if it has
    not stopped a second later; either way it is reaped.
    """
    while time.monotonic() < deadline:
        if xvfb_process.poll() is not None:
            return False
        if _lock_pid(number) == xvfb_process.pid:
            with socket.socket(socket.AF_UNIX) as probe:
                try:
                    probe.connect(_SOCKET.format(number))
                    return True
                except OSError:
                    pass
        time.sleep(0.02)
    xvfb_process.terminate()
    try:
        xvfb_process.wait(1)
    except subprocess.TimeoutExpired:
        xvfb_process.kill()
        xvfb_process.wait()
    return False


def _lock_pid(number: int) -> int | None:
    """The pid in display `number`'s lock file, or None while it has none."""
    try:
        with open(_LOCK.format(number)) as lock:
            return int(lock.read().strip() or 0)
    except (OSError, ValueError):
        return None


def pin_environment(environ: MutableMapping[str, str], display: str) -> None:
    """Point GTK at `display` over X11 only, with no way back to the desktop session.

    `HYPRLAND_INSTANCE_SIGNATURE` goes too: it is how the app finds a compositor to talk
    to. So do the session's `GDK_SCALE`, which shrank the 1280x1024 screen to 640x512 on
    the owner's HiDPI desktop, and the accessibility bus, which would register the widgets
    with the desktop's screen reader.
    """
    environ["DISPLAY"] = display
    environ["GDK_BACKEND"] = "x11"
    environ["GTK_A11Y"] = "none"
    for name in ("WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "GDK_SCALE"):
        environ.pop(name, None)
