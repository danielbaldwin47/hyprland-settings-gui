"""The record a kept import leaves for the Session's next read-back (#259).

Keep is the moment an import becomes the user's config, and the moment nothing can confirm
it: the wizard reads no Options back, and Last known good (ADR-0016) is only what a
confirmed read-back left. So `MigrationFlow.keep` writes this record -- each imported
Module's hash and the Options it sets, from the Manifest the wizard wrote -- before it
clears the migration sentinel, and the Session's next read-back (`seed`) turns it into one
Journal entry marked as a boundary, then deletes it. Durable in between, so a relaunch
seeds what a closed app did not.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..files import write_atomic
from ..paths import ConfigPaths
from .journal import Journal, JournalEntry
from .manifest import Manifest, ModuleRecord

_log = logging.getLogger(__name__)

FORMAT_VERSION = 1


@dataclass(frozen=True, slots=True)
class KeptImport:
    """An import the user kept, not yet recorded in the Journal."""

    modules: Mapping[str, ModuleRecord] = field(default_factory=dict)
    """Each imported Module, as the Manifest named it at Keep."""

    known: bool = True
    """False for a record that could not be parsed: an import was kept, but which bytes it
    left is unknown, so its boundary is never Confirmed."""


def write(paths: ConfigPaths, manifest: Manifest) -> None:
    """Record `manifest`'s Modules as a kept import. Raises `OSError`."""
    payload = {
        "format_version": FORMAT_VERSION,
        "modules": {name: record.as_json() for name, record in manifest.modules.items()},
    }
    write_atomic(paths.kept_import, json.dumps(payload, indent=2) + "\n")


def read(paths: ConfigPaths) -> KeptImport | None:
    """The record, or `None` when no kept import is waiting."""
    try:
        text = paths.kept_import.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        _log.warning("could not read the kept-import record: %s", error)
        return KeptImport(known=False)
    try:
        payload = json.loads(text)
    except ValueError:
        return KeptImport(known=False)
    if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
        return KeptImport(known=False)
    raw = payload.get("modules")
    if not isinstance(raw, dict):
        return KeptImport(known=False)
    modules = {str(name): ModuleRecord.from_json(record) for name, record in raw.items()}
    if any(record is None for record in modules.values()):
        return KeptImport(known=False)
    return KeptImport(
        modules={name: record for name, record in modules.items() if record is not None}
    )


def clear(paths: ConfigPaths) -> None:
    """Drop the record. Raises `OSError` for anything but its absence."""
    paths.kept_import.unlink(missing_ok=True)


def seed(
    journal: Journal,
    paths: ConfigPaths,
    record: KeptImport,
    manifest: Manifest,
    *,
    confirmed: bool,
    outcome: str,
) -> JournalEntry | None:
    """Journal the kept import as a boundary, then delete the record.

    `confirmed` is the read-back's answer for the whole import (ADR-0016: empty
    `configerrors`, every Option read back). It is withdrawn when a Module no longer holds
    the bytes the import left, or the record could not be read: then which bytes were
    imported is unknown, and nothing unverified may become a restore point. A Module the
    Journal has history for and the import left absent is recorded as deleted, so nothing
    from before the import is offered for it.
    """
    modules = record.modules if record.known else manifest.modules
    intact = record.known and all(
        each.matches(paths.file_for(name)) for name, each in modules.items()
    )
    known = {name for entry in journal.entries() for name in entry.modules}
    deleted = sorted(name for name in known - set(modules) if journal.read_module(name) is None)
    names = [*modules, *deleted]
    draft = journal.begin(names)
    entry = draft.commit(
        keys=tuple(option for each in modules.values() for option in each.options),
        outcome=outcome,
        confirmed=confirmed and intact,
        changed=names,
        options={name: each.options for name, each in modules.items()},
        boundary=True,
    )
    try:
        clear(paths)
    except OSError as error:
        # Seeded twice at worst: the second boundary is the first one's bytes again.
        _log.warning("could not remove the kept-import record: %s", error)
    return entry
