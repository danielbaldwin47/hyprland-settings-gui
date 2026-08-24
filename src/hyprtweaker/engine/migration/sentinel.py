"""The `migration-pending` marker that makes a half-finished switch survivable (ADR-0009).

Written to the state dir *before* the Entrypoint goes live and cleared by both answers --
Keep and roll back. So its presence at the next start means exactly one thing: a switch
began and nobody ever answered for it. The app died, the compositor died, or the session
did. Either way the switch is treated as failed and rollback is offered.

It carries the rollback instructions rather than just a flag, because the process that
would have known them is gone. A relaunched app has to be able to undo a switch it has no
memory of making: which file to restore, which backup it came from, and the TTY line to
print if it cannot do either.

State dir, not the hypr dir: a marker inside `~/.config/hypr` would land in the user's
dotfile repo, and a committed-and-pushed `migration-pending` would offer every machine that
checked it out a rollback of a migration that finished fine on one of them.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..paths import ConfigPaths

FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class Sentinel:
    """An unconfirmed switch, and everything needed to undo it without the wizard."""

    started: str
    """ISO-8601 UTC timestamp of the moment the Entrypoint was about to be written."""

    kind: str
    """The `ConfigKind` the switch came from -- which decides how rollback undoes it."""

    source: str | None
    """The config file that was imported, for the report and the rescue line."""

    backup: str | None
    """The full-tree backup taken before the switch, or `None` if there was none."""

    restore: str | None
    """A file to move back into place on rollback: the `.lua` path's `hyprland.lua.bak`.

    `None` on the `.conf` path, where rollback is deleting the Entrypoint -- `hyprland.conf`
    was never touched, so there is nothing to put back and Hyprland picks it up again on its
    own (which is what keeps "delete `hyprland.lua`" a complete rollback).
    """

    version: int = FORMAT_VERSION

    def as_json(self) -> dict[str, object]:
        return dict(asdict(self))


def write(
    paths: ConfigPaths,
    *,
    kind: str,
    source: Path | None = None,
    backup: Path | None = None,
    restore: Path | None = None,
    now: datetime | None = None,
) -> Sentinel:
    """Record that a switch is under way. Call this before writing the Entrypoint.

    Flushed to disk before it returns, not left to the OS: the whole point is to survive a
    process that stops existing a moment later, and a sentinel still sitting in a write
    buffer when the compositor takes the session down would have recorded nothing.
    """
    marker = Sentinel(
        started=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        kind=kind,
        source=str(source) if source else None,
        backup=str(backup) if backup else None,
        restore=str(restore) if restore else None,
    )
    paths.sentinel.parent.mkdir(parents=True, exist_ok=True)
    with paths.sentinel.open("w", encoding="utf-8") as handle:
        json.dump(marker.as_json(), handle, indent=2)
        handle.write("\n")
        handle.flush()
        _fsync(handle.fileno())
    return marker


def _fsync(fileno: int) -> None:
    """`os.fsync`, wrapped so a filesystem that refuses it cannot fail the migration.

    tmpfs and some network mounts reject `fsync` on a plain file. The sentinel is a
    best-effort durability measure; refusing to migrate because the state dir will not
    promise durability would trade a rare recovery path for a common outright failure.
    """
    with contextlib.suppress(OSError):
        os.fsync(fileno)


def read(paths: ConfigPaths) -> Sentinel | None:
    """The unconfirmed switch this state dir remembers, or `None` for a clean start.

    Never raises. A sentinel that cannot be parsed still means a switch was under way, so
    it comes back as one with unknown details rather than as "nothing happened" -- the
    conservative reading, since the alternative silently leaves a user on a config they
    never confirmed.
    """
    try:
        raw = paths.sentinel.read_text(encoding="utf-8")
    except OSError:
        return None

    data: dict[str, object] = {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        data = {str(key): value for key, value in parsed.items()}

    def text(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) else None

    version = data.get("version")
    return Sentinel(
        started=text("started") or "",
        kind=text("kind") or "",
        source=text("source"),
        backup=text("backup"),
        restore=text("restore"),
        version=version if isinstance(version, int) else FORMAT_VERSION,
    )


def clear(paths: ConfigPaths) -> bool:
    """Answer for the switch. `True` if a sentinel was there to clear."""
    try:
        paths.sentinel.unlink()
    except OSError:
        return False
    return True
