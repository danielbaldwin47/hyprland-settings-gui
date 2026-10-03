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

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..files import write_atomic
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

    Named before the switch moves the original there (#268), so a reader checks that it
    exists before trusting it. `None` on the `.conf` path, where rollback is deleting the
    Entrypoint -- `hyprland.conf` was never touched, so there is nothing to put back and
    Hyprland picks it up again on its own (which is what keeps "delete `hyprland.lua`" a
    complete rollback).
    """

    restore_app_dir: str | None = None
    """The App dir the switch moved aside (`hyprtweaker.bak`), moved back on rollback.

    `None` when there was no App dir before the switch. Additive, like `bridge_tools`: a
    sentinel without the key comes from a switch that wrote over the App dir in place.
    """

    bridge_tools: tuple[str, ...] = ()
    """Theming tools this switch wires (#187): Roll back unwires each.

    Written before the first tool file changes, so a crash part-way through wiring still
    names the tool. Additive, without a `FORMAT_VERSION` bump: a sentinel without the key
    wired nothing, and `unwire` of a tool that was never wired is a no-op.
    """

    original_sha256: str | None = None
    """The `sha256` of the Entrypoint the switch found, `None` when there was none (#268).

    How Roll back tells, by bytes, whether the original is still in place (the switch
    stopped before moving it) or a hand edit is about to be replaced. Additive."""

    generated_sha256: str | None = None
    """The `sha256` of the Entrypoint the switch wrote, recorded once the tree is written.

    `None` while the switch is still writing: Roll back then copies any Entrypoint that is
    not the original before replacing it, rather than guess it is the app's own. Additive."""

    version: int = FORMAT_VERSION

    @property
    def known(self) -> bool:
        """Whether this marker could be read. One that could not still means a switch was
        under way, but names nothing Roll back can trust (`read`)."""
        return bool(self.kind)

    def as_json(self) -> dict[str, object]:
        return dict(asdict(self))


def write(
    paths: ConfigPaths,
    *,
    kind: str,
    source: Path | None = None,
    backup: Path | None = None,
    restore: Path | None = None,
    restore_app_dir: Path | None = None,
    bridge_tools: tuple[str, ...] = (),
    original_sha256: str | None = None,
    generated_sha256: str | None = None,
    now: datetime | None = None,
) -> Sentinel:
    """Record that a switch is under way. Call this before moving or writing anything.

    Through `write_atomic` (#268): the marker is whole or absent, never a truncated file
    that names half of what Roll back needs, and its bytes reach the disk before it returns,
    since the whole point is to survive a process that stops existing a moment later.
    Raises `OSError`; a switch that cannot record itself must not start.
    """
    marker = Sentinel(
        started=(now or datetime.now(UTC)).isoformat(timespec="seconds"),
        kind=kind,
        source=str(source) if source else None,
        backup=str(backup) if backup else None,
        restore=str(restore) if restore else None,
        restore_app_dir=str(restore_app_dir) if restore_app_dir else None,
        bridge_tools=bridge_tools,
        original_sha256=original_sha256,
        generated_sha256=generated_sha256,
    )
    write_atomic(paths.sentinel, json.dumps(marker.as_json(), indent=2) + "\n")
    return marker


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
    tools = data.get("bridge_tools")
    return Sentinel(
        started=text("started") or "",
        kind=text("kind") or "",
        source=text("source"),
        backup=text("backup"),
        restore=text("restore"),
        restore_app_dir=text("restore_app_dir"),
        original_sha256=text("original_sha256"),
        generated_sha256=text("generated_sha256"),
        bridge_tools=tuple(
            each for each in (tools if isinstance(tools, list) else []) if isinstance(each, str)
        ),
        version=version if isinstance(version, int) else FORMAT_VERSION,
    )


def clear(paths: ConfigPaths) -> bool:
    """Answer for the switch. `True` if a sentinel was there to clear."""
    try:
        paths.sentinel.unlink()
    except OSError:
        return False
    return True
