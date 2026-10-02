"""What the running compositor is, read once and blocking, before the model exists.

`Session.__init__` builds its model from a Schema, and which Schema depends on which
Hyprland is running (ADR-0012 §Pinning). That has to be known before the async startup
(`Session._go_live`) has a loop to run on, so this one read is a plain blocking socket
rather than a `CommandClient` request. It is the only blocking IPC in the app, and it is
bounded: one deadline covers both requests, so a compositor that accepts and never
answers costs startup at most `LIVE_READ_TIMEOUT_SECONDS`, not that per request.

Every failure answers `None` -- no compositor, a socket that hangs, a reply the app cannot
read, a version string that is not a release number. The caller's answer to `None` is the
newest shipped Schema, which is what the app did before it asked at all.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import NoInstance
from .instance import Instance

LIVE_READ_TIMEOUT_SECONDS = 1.0
"""The whole read's budget, both requests together. It runs on the GTK main thread before
the window exists, so it is the longest a hung socket can delay the first frame."""

_RELEASE_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LiveHyprland:
    """The running compositor: its release number and every option it describes.

    Exists only for a compositor whose version parsed, so `version` is always a dotted
    release number (`"0.56.2"`) that `version_key` orders.

    `descriptions` are the raw `j/descriptions` records, kept whole because their readers
    want different parts: option names for "Not in this Hyprland" (#181), the full records
    to supplement options a newer Hyprland added (#177). `names` is built from them once.
    """

    version: str
    descriptions: tuple[dict[str, Any], ...]
    names: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        names = frozenset(str(record["name"]) for record in self.descriptions)
        object.__setattr__(self, "names", names)


def parse_release_version(text: str) -> str | None:
    """`"0.56.2"` from a `version` field, or `None` for anything that is not a release.

    Anything else (a packager's `unknown`, a `0.56.2-12-gabcdef`) answers `None` rather
    than a guess: degrading onto the wrong Schema offers options the compositor rejects,
    and the newest Schema is the app's long-standing answer to "version not known". A git
    build is not such a case: Hyprland puts its distance from the tag in `tag` and
    `commits`, and `version` stays the bare number of the release it builds on, so it
    reads as that release (from the field layout of a captured 0.56.2 reply; no git build
    has been probed).
    """
    match = _RELEASE_VERSION.fullmatch(text.strip())
    return match.group(1) if match is not None else None


def read_live_hyprland(
    connect: Callable[[], Instance], *, timeout: float = LIVE_READ_TIMEOUT_SECONDS
) -> LiveHyprland | None:
    """Ask the compositor `connect` names for `j/version` and `j/descriptions`.

    `None` when there is no compositor, it does not answer both within `timeout`, or either
    reply is not the shape a release Hyprland sends.
    """
    try:
        instance = connect()
    except NoInstance:
        return None

    deadline = time.monotonic() + timeout
    try:
        version_reply = json.loads(_request(instance, "j/version", deadline))
        descriptions_reply = json.loads(_request(instance, "j/descriptions", deadline))
    except (OSError, ValueError) as error:
        # TimeoutError is an OSError. A JSON reply Hyprland failed to escape is a ValueError.
        _log.info("could not read the running Hyprland's version: %s", error)
        return None

    version = _version_of(version_reply)
    descriptions = _descriptions_of(descriptions_reply)
    if version is None or descriptions is None:
        _log.info("the running Hyprland's version or descriptions did not parse")
        return None
    return LiveHyprland(version=version, descriptions=descriptions)


def _version_of(reply: Any) -> str | None:
    if not isinstance(reply, dict) or not isinstance(reply.get("version"), str):
        return None
    return parse_release_version(reply["version"])


def _descriptions_of(reply: Any) -> tuple[dict[str, Any], ...] | None:
    """The records, or `None` when any is malformed or there are none.

    All or nothing: a partial list would read as options the compositor lacks, and every
    one of those would be badged "Not in this Hyprland".
    """
    if not isinstance(reply, list) or not reply:
        return None
    if not all(
        isinstance(record, dict) and isinstance(record.get("name"), str) for record in reply
    ):
        return None
    return tuple(reply)


def _request(instance: Instance, request: str, deadline: float) -> str:
    """One blocking round trip; Hyprland answers and hangs up, so EOF ends the reply."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(_remaining(deadline))
        connection.connect(str(instance.command_socket))
        connection.sendall(request.encode("utf-8"))
        chunks: list[bytes] = []
        while True:
            connection.settimeout(_remaining(deadline))
            chunk = connection.recv(65536)
            if not chunk:
                return b"".join(chunks).decode("utf-8", errors="replace")
            chunks.append(chunk)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("the live read's deadline passed")
    return remaining
