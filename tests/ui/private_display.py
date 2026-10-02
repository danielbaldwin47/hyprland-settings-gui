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
import select
import signal
import subprocess
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


def start_xvfb(xvfb: str) -> str | None:
    """Start a headless X server and return its display name, or None if it failed.

    It dies with this process (PR_SET_PDEATHSIG), including a `timeout` kill that runs no
    cleanup. Nothing stops it earlier on purpose: GTK keeps the connection until exit, and
    GDK exits the process when its X server goes away under it.
    """
    read_fd, write_fd = os.pipe()
    try:
        xvfb_process = subprocess.Popen(
            [
                xvfb,
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                "1280x1024x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(write_fd,),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: _libc.prctl(_PR_SET_PDEATHSIG, signal.SIGTERM),
        )
    except OSError:
        os.close(read_fd)
        return None
    finally:
        os.close(write_fd)
    # Xvfb writes the number once it accepts connections; EOF means it exited first. CI
    # jobs have no timeout of their own, so a wedged Xvfb must not hang the run.
    with os.fdopen(read_fd) as pipe:
        ready, _, _ = select.select([pipe], [], [], 10)
        number = pipe.readline().strip() if ready else ""
    if not number:
        xvfb_process.kill()
        return None
    return f":{number}"


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
