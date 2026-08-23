"""Snapshots and the Journal: what every write replaced, and which writes were good.

Two files in the state dir, and one idea each (ADR-0005, ADR-0010 §Rollback, ADR-0016):

* **Snapshots** (`snapshots/<sha256>`) -- the bytes of one Module version, stored under
  their own digest. Content-addressed rather than one directory per transaction, because
  the overwhelmingly common write changes one Module and leaves the rest of the App dir
  byte-identical: keying by content means the unchanged ones cost nothing and a value
  toggled back and forth reuses the file it had.
* **The Journal** (`journal.jsonl`) -- one line per Apply transaction: when, which Options
  it was accountable for, how it ended, whether it **confirmed**, and, per Module, the
  digest of the bytes it replaced and of the bytes it left. Append-only, so a write is one
  `open(..., "a")` and a crash mid-append costs the last line rather than the file.

**Two digests per Module, not one.** ADR-0010 wants the *pre-write* bytes, because
auto-revert restores "the state that was live and confirmed moments before". ADR-0016 wants
the newest bytes *whose transaction confirmed clean*, because that is Last known good. Those
are opposite ends of the same write, and a record carrying only one of them would answer one
ADR by guessing at the other -- so `ModuleChange` carries `before` and `after`, and the two
questions are two lookups rather than one inference.

**Pruning is bounded and pinned.** The Journal keeps the newest `MAX_ENTRIES` transactions,
*plus* the newest confirmed entry for every Module, whether or not it still fits. The pin is
ADR-0016's consequence stated as code: "pruning must never drop the newest confirmed Snapshot
of a Module", because dropping it silently retires that Module's Last known good and leaves
Restore-last-good with nothing to restore. Snapshot files are then garbage-collected against
exactly what the retained entries reference, so the store is bounded by the entry count and
never by how long the app has been running.

Nothing here raises on a damaged file. History is not the config: an unreadable Journal line
costs the app that transaction's history, and refusing to apply anything until the user
deletes a state file would turn a truncated write from an unrelated crash into a bricked app.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..paths import ENTRYPOINT_NAME, ConfigPaths
from .manifest import content_hash

_log = logging.getLogger(__name__)

FORMAT_VERSION = 1
"""Stamped on every line. A line from a future format is skipped rather than guessed at."""

MAX_ENTRIES = 200
"""How many transactions the Journal keeps before the oldest are dropped.

Two hundred is roughly a long session's worth of edits -- enough that "what did I change
this afternoon?" is answerable -- and at a few hundred bytes a line the file stays under
100 kB. The bound that actually matters is the Snapshot store's, and it follows from this
one: no entry, no reference, no blob.
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ModuleChange:
    """What one Apply transaction did to one app-owned file.

    `before` and `after` are Snapshot digests, or `None` for "the file was not there" --
    which is a real state at both ends: a Module is created when its Section gains its first
    set Option, and deleted when it loses its last.
    """

    module: str
    """App-dir-relative name, exactly as the Manifest spells it (`options/general.lua`)."""

    before: str | None
    after: str | None

    def as_json(self) -> dict[str, Any]:
        return {"module": self.module, "before": self.before, "after": self.after}

    @classmethod
    def from_json(cls, payload: Any) -> ModuleChange | None:
        if not isinstance(payload, dict) or not isinstance(payload.get("module"), str):
            return None
        before, after = payload.get("before"), payload.get("after")
        if not isinstance(before, str | None) or not isinstance(after, str | None):
            return None
        return cls(module=payload["module"], before=before, after=after)


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One Apply transaction, as history rather than as an outcome to branch on."""

    at: str
    """ISO-8601 UTC, to the second. For a human reading the file, not for ordering -- the
    Journal is append-only, so file order *is* chronological order and stays right across a
    clock change."""

    keys: tuple[str, ...]
    """The Options the transaction was accountable for confirming."""

    outcome: str
    """`ApplyOutcome`'s wire spelling, kept as text so the state layer never has to import
    the apply layer (the writer already depends on this package the other way round)."""

    confirmed: bool
    """ADR-0016's flag: empty `configerrors` *and* every non-restart key read back.

    The whole reason the Journal exists as more than a log. `after` bytes are only Last
    known good when this is true -- see `ApplyResult.confirmed` for why the stricter question
    is the one worth recording."""

    changes: tuple[ModuleChange, ...]

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(change.module for change in self.changes)

    def change(self, module: str) -> ModuleChange | None:
        for candidate in self.changes:
            if candidate.module == module:
                return candidate
        return None

    def as_json(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "at": self.at,
            "keys": list(self.keys),
            "outcome": self.outcome,
            "confirmed": self.confirmed,
            "changes": [change.as_json() for change in self.changes],
        }

    @classmethod
    def from_json(cls, payload: Any) -> JournalEntry | None:
        if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
            return None
        raw_changes = payload.get("changes")
        if not isinstance(raw_changes, list):
            return None
        changes = [ModuleChange.from_json(item) for item in raw_changes]
        if any(change is None for change in changes):
            return None
        raw_keys = payload.get("keys")
        return cls(
            at=str(payload.get("at", "")),
            keys=tuple(str(key) for key in raw_keys) if isinstance(raw_keys, list) else (),
            outcome=str(payload.get("outcome", "")),
            confirmed=bool(payload.get("confirmed", False)),
            changes=tuple(change for change in changes if change is not None),
        )


class Draft:
    """One transaction's pre-write bytes, held until the transaction has an outcome.

    Opened before the Writer runs and closed after Read-back, because those are the two
    moments the two halves of a `ModuleChange` exist at: the `before` bytes are gone the
    instant a rename lands, and `confirmed` is not knowable until the compositor has
    answered. Holding the `before` bytes in memory rather than writing them straight out is
    what keeps a no-op transaction from leaving litter -- most Apply transactions change one
    Module, and snapshotting the other twenty would be twenty files nobody will ever read.
    """

    def __init__(self, journal: Journal, before: dict[str, bytes | None]) -> None:
        self._journal = journal
        self._before = before

    def before_bytes(self, module: str) -> bytes | None:
        """What `module` held when this transaction started, or `None` if it was absent."""
        return self._before.get(module)

    def dirty(self) -> tuple[str, ...]:
        """Which candidates are not the bytes they were. The answer when nobody else has one.

        A write that raised part-way through leaves no `WriteResult` to read, and "the App
        dir may now be half-updated" is exactly the state worth having a Snapshot of. Asking
        the disk is the only way to find out what landed before the filesystem said no.
        """
        return tuple(
            sorted(
                module
                for module, before in self._before.items()
                if self._journal.read_module(module) != before
            )
        )

    def commit(
        self,
        *,
        keys: Sequence[str],
        outcome: str,
        confirmed: bool,
        changed: Iterable[str],
    ) -> JournalEntry | None:
        """Record what happened. `changed` names the files this write actually replaced.

        Returns `None` when nothing changed -- a transaction that laid no bytes down has no
        Snapshot to take and nothing to say that the Manifest does not already record. The
        `after` bytes are read back off disk rather than taken from what the Writer rendered:
        the claim being stored is "this is what the Module holds", and the only honest source
        for that is the file.
        """
        names = sorted(set(changed))
        if not names:
            return None

        changes: list[ModuleChange] = []
        for module in names:
            before = self._before.get(module)
            after = self._journal.read_module(module)
            changes.append(
                ModuleChange(
                    module=module,
                    before=self._journal.store(before),
                    after=self._journal.store(after),
                )
            )

        entry = JournalEntry(
            at=_now(),
            keys=tuple(keys),
            outcome=outcome,
            confirmed=confirmed,
            changes=tuple(changes),
        )
        self._journal.append(entry)
        return entry

    def discard(self) -> None:
        """Drop the held bytes without recording anything. Nothing reached disk."""
        self._before.clear()


class Journal:
    """The Snapshot store and the change log, as one object over one state dir."""

    def __init__(self, paths: ConfigPaths, *, max_entries: int = MAX_ENTRIES) -> None:
        self._paths = paths
        self._max_entries = max(1, max_entries)

    @property
    def paths(self) -> ConfigPaths:
        return self._paths

    # --- the transaction's two moments --------------------------------------------------

    def begin(self, modules: Iterable[str]) -> Draft:
        """Snapshot the current bytes of every file a write is about to consider.

        `modules` is a superset on purpose -- everything the app owns or is about to own --
        because which files a write actually replaces is only known once it has run, and by
        then their previous bytes are gone. A file that turns out not to change is simply
        never recorded (`Draft.commit`).

        Absent candidates are held as `None` rather than left out: "this Module did not exist
        before the write" is a real prior state, and the one a newly created Module's undo
        has to restore.
        """
        return Draft(self, {module: self.read_module(module) for module in modules})

    def read_module(self, module: str) -> bytes | None:
        """The bytes of one app-owned file right now, or `None` when it is not there."""
        try:
            return self._path_for(module).read_bytes()
        except OSError:
            return None

    # --- the Snapshot store -------------------------------------------------------------

    def store(self, data: bytes | None) -> str | None:
        """Put bytes in the Snapshot store and return their digest. `None` passes through.

        Idempotent by construction: the digest *is* the filename, so re-storing bytes the
        store already holds is one `is_file()` and no write at all. That is what makes
        snapshotting every write affordable -- a config where one Module churns and nineteen
        do not costs one new file per distinct version of that Module, forever.
        """
        if data is None:
            return None
        digest = content_hash(data)
        path = self._paths.snapshots_dir / digest
        if path.is_file():
            return digest
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{digest}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, path)
        except OSError as error:
            # History, not config. A state dir that cannot be written costs the user
            # auto-revert and Last known good, and the app says so in the log -- but
            # refusing the *edit* over it would be the tail wagging the dog.
            _log.warning("could not store a Snapshot: %s", error)
            return None
        return digest

    def snapshot(self, digest: str | None) -> bytes | None:
        """The stored bytes for a digest, or `None` when it was pruned or never stored."""
        if digest is None:
            return None
        try:
            return (self._paths.snapshots_dir / digest).read_bytes()
        except OSError:
            return None

    # --- reading the log ----------------------------------------------------------------

    def entries(self) -> tuple[JournalEntry, ...]:
        """Every readable entry, oldest first. Unreadable lines are skipped, not fatal.

        A half-written last line is the expected damage -- the app was killed mid-append --
        and the entries before it are still perfectly good history.
        """
        try:
            text = self._paths.journal.read_text(encoding="utf-8")
        except OSError:
            return ()

        found: list[JournalEntry] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                _log.warning("skipping an unreadable Journal line")
                continue
            entry = JournalEntry.from_json(payload)
            if entry is None:
                _log.warning("skipping an unrecognised Journal entry")
                continue
            found.append(entry)
        return tuple(found)

    def last_known_good(self, module: str) -> bytes | None:
        """The newest bytes of `module` that a confirmed transaction left behind.

        ADR-0016's Last known good, and the reason `confirmed` is recorded at all. `None`
        means the app has never confirmed a write to this Module in the retained history --
        which Restore-last-good must treat as "there is nothing to restore to" rather than
        as "restore whatever is newest", because the newest may be exactly what broke.

        A Module the transaction *deleted* answers `None` too: its confirmed state is
        absence, and there are no bytes that spell that.
        """
        digest = self.last_known_good_digest(module)
        return self.snapshot(digest) if digest is not None else None

    def last_known_good_digest(self, module: str) -> str | None:
        for entry in reversed(self.entries()):
            if not entry.confirmed:
                continue
            change = entry.change(module)
            if change is not None and change.after is not None:
                return change.after
        return None

    # --- writing the log ----------------------------------------------------------------

    def append(self, entry: JournalEntry) -> None:
        """Add one entry, then prune. Never raises -- see the module docstring."""
        line = json.dumps(entry.as_json(), sort_keys=False) + "\n"
        try:
            self._paths.journal.parent.mkdir(parents=True, exist_ok=True)
            with self._paths.journal.open("a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError as error:
            _log.warning("could not append to the Journal: %s", error)
            return
        self.prune()

    def prune(self) -> None:
        """Bound the Journal, then collect the Snapshots nothing refers to any more.

        Retention is the newest `max_entries` entries **plus** every Module's newest
        confirmed entry, so an App dir with one Module nobody has touched in months keeps its
        Last known good while the churn around it rolls over. Both halves are needed: the
        window alone loses the pin, and the pin alone is not a bound.
        """
        entries = self.entries()
        retained = self._retained(entries)
        if len(retained) != len(entries):
            self._rewrite(retained)
        self._collect(retained)

    def _retained(self, entries: Sequence[JournalEntry]) -> tuple[JournalEntry, ...]:
        # By position, not by value: two entries can be equal (the same Module written back
        # to the same bytes in the same second) and dropping one of a pair would silently
        # halve the window.
        keep = set(range(max(0, len(entries) - self._max_entries), len(entries)))
        pinned: set[str] = set()
        for index in reversed(range(len(entries))):
            entry = entries[index]
            if not entry.confirmed:
                continue
            fresh = [
                change.module
                for change in entry.changes
                if change.module not in pinned and change.after is not None
            ]
            if fresh:
                pinned.update(fresh)
                keep.add(index)
        # Chronological order, whichever half put an entry on the list.
        return tuple(entries[index] for index in sorted(keep))

    def _rewrite(self, entries: Sequence[JournalEntry]) -> None:
        text = "".join(json.dumps(entry.as_json(), sort_keys=False) + "\n" for entry in entries)
        path = self._paths.journal
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, path)
        except OSError as error:
            _log.warning("could not prune the Journal: %s", error)

    def _collect(self, entries: Sequence[JournalEntry]) -> None:
        """Delete Snapshot files no retained entry names.

        Runs after the rewrite rather than before it: a blob deleted while an entry still
        referenced it would leave the Journal claiming a Snapshot that is not there, which
        is worse than a blob nobody reads.
        """
        directory = self._paths.snapshots_dir
        if not directory.is_dir():
            return
        referenced = {
            digest
            for entry in entries
            for change in entry.changes
            for digest in (change.before, change.after)
            if digest is not None
        }
        try:
            present = list(directory.iterdir())
        except OSError:
            return
        for path in present:
            if path.name.startswith(".") or path.name in referenced:
                continue
            try:
                path.unlink()
            except OSError as error:
                _log.warning("could not prune Snapshot %s: %s", path.name, error)

    # --- internals ----------------------------------------------------------------------

    def _path_for(self, module: str) -> Path:
        """Where an app-owned name lives. The Entrypoint is the one outside the App dir."""
        if module == ENTRYPOINT_NAME:
            return self._paths.entrypoint
        return self._paths.app_dir / module
