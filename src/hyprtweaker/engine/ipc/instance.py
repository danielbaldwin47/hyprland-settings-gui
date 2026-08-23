"""Which Hyprland this app is talking to, and where its two sockets live.

One object rather than path joins in both clients, because "the session" is a single fact
with two files hanging off it: `.socket.sock` takes commands and answers, `.socket2.sock`
pushes events (research #5 §7). Both sit in
`$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/`; the pre-0.40 `/tmp/hypr` location is
deliberately not searched, since the app requires Hyprland >= 0.56 anyway.

It is also the seam the tests use: an `Instance` is nothing but a directory, so a scripted
fake pair of sockets in a temp dir is a first-class instance and needs no monkeypatching.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import NoInstance

SIGNATURE_VARIABLE = "HYPRLAND_INSTANCE_SIGNATURE"
COMMAND_SOCKET_NAME = ".socket.sock"
EVENT_SOCKET_NAME = ".socket2.sock"


def _runtime_dir() -> Path:
    """`$XDG_RUNTIME_DIR`, with the systemd-standard fallback Hyprland itself assumes."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else Path(f"/run/user/{os.getuid()}")


@dataclass(frozen=True, slots=True)
class Instance:
    """One Hyprland session, identified by the directory holding its sockets."""

    directory: Path

    @classmethod
    def current(cls) -> Instance:
        """The session this process is running under.

        Raises `NoInstance` rather than returning something unusable: every caller of this
        would otherwise have to re-discover, at connect time, that there was never a
        compositor here -- and would report it as a connection failure, which is a
        different and much more alarming thing to tell a user.
        """
        signature = os.environ.get(SIGNATURE_VARIABLE)
        if not signature:
            raise NoInstance(f"{SIGNATURE_VARIABLE} is unset -- not running under Hyprland")

        instance = cls(_runtime_dir() / "hypr" / signature)
        if not instance.command_socket.is_socket():
            raise NoInstance(f"no Hyprland command socket at {instance.command_socket}")
        return instance

    @property
    def command_socket(self) -> Path:
        """The request/reply socket: one connection per command, closed by Hyprland."""
        return self.directory / COMMAND_SOCKET_NAME

    @property
    def event_socket(self) -> Path:
        """The push-only event socket: one long-lived connection for the app's lifetime."""
        return self.directory / EVENT_SOCKET_NAME
