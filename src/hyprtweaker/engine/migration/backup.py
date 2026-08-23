"""Full copies of the hypr dir, taken before a migration touches anything (ADR-0009).

A copy, never a move. The legacy tree stays exactly where it is and exactly as it was --
`hyprland.conf` is never written, moved or deleted -- so rolling back is deleting one
generated file, not reassembling a config from a backup. The backup is the belt to that
pair of braces: it covers the case where something *else* in the dir was disturbed.

`$XDG_STATE_HOME/hyprtweaker/backups/<timestamp>/`, not the mockup's
`~/.config/hypr.backup-*`: a full copy of a dotfile repo placed next to that repo is a
directory users end up committing, and one placed *inside* it is worse.

The timestamp directory name is the whole index. No metadata file, because the alternative
is a record that can disagree with the directory it describes, and because a backup has to
stay usable by a human with `cp -r` long after this app is uninstalled.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..paths import ConfigPaths

STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True, slots=True)
class Backup:
    """One stored copy of the hypr dir."""

    path: Path
    stamp: str

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    def count(self) -> int:
        """How many regular files it holds. Computed, never recorded -- see the module note."""
        if not self.path.is_dir():
            return 0
        return sum(1 for item in self.path.rglob("*") if item.is_file() or item.is_symlink())


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime(STAMP_FORMAT)


def create(paths: ConfigPaths, *, now: datetime | None = None) -> Backup:
    """Copy the whole hypr dir aside and return where it went.

    Symlinks are copied as symlinks. A dotfile repo that symlinks `hyprland.conf` in from
    elsewhere must come back as a symlink, not as a detached copy that silently stops
    tracking the file the user actually edits.

    A hypr dir that does not exist yet still produces a backup -- an empty one. The wizard's
    Back up step then has a real, restorable answer for the fresh-user case instead of a
    special case, and rollback does not have to know which kind it is undoing.
    """
    stamp = _stamp(now)
    destination = paths.backups_dir / stamp
    suffix = 1
    while destination.exists():
        # Two migrations inside one second, or a retried one. Never overwrite: the older
        # copy may be the only remaining record of the config being replaced.
        suffix += 1
        destination = paths.backups_dir / f"{stamp}-{suffix}"

    destination.parent.mkdir(parents=True, exist_ok=True)
    if paths.hypr_dir.is_dir():
        shutil.copytree(paths.hypr_dir, destination, symlinks=True, dirs_exist_ok=False)
    else:
        destination.mkdir()

    return Backup(path=destination, stamp=destination.name)


def stored(paths: ConfigPaths) -> list[Backup]:
    """Every backup, oldest first. The timestamp names sort chronologically as text."""
    if not paths.backups_dir.is_dir():
        return []
    return [
        Backup(path=item, stamp=item.name)
        for item in sorted(paths.backups_dir.iterdir())
        if item.is_dir()
    ]


def latest(paths: ConfigPaths) -> Backup | None:
    """The most recent backup, or `None`. What "Roll back" reaches for by default."""
    backups = stored(paths)
    return backups[-1] if backups else None


def restore(paths: ConfigPaths, backup: Backup) -> int:
    """Put a backup's contents back into the hypr dir. Returns the number of files written.

    Additive by design: files in the backup are copied over whatever is there now, and
    anything created since is left alone. A destructive restore -- wipe the dir, then
    copy -- would delete a `user.lua` the user wrote after the migration, and this app's
    whole position is that it never demolishes files it did not write. The one thing
    rollback *does* remove is the Entrypoint, and that is the caller's job, done by name.
    """
    if not backup.path.is_dir():
        raise FileNotFoundError(f"no such backup: {backup.path}")

    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for item in sorted(backup.path.rglob("*")):
        relative = item.relative_to(backup.path)
        target = paths.hypr_dir / relative
        if item.is_dir() and not item.is_symlink():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.exists():
            target.unlink()
        shutil.copy2(item, target, follow_symlinks=False)
        written += 1
    return written
