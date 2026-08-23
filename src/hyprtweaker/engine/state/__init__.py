"""State: the Manifest and the Journal now; the Prefs file later.

The **Manifest** (`manifest.json` in the App dir) records what the app wrote and what it
looked like: app version, Schema version, a SHA-256 per Module for hand-edit detection,
and the migration provenance the Importer stamps once (ADR-0005).

The **Journal** and its **Snapshots** (`$XDG_STATE_HOME/hyprtweaker/`) record what every
write replaced and whether the write was good: one content-addressed copy per distinct
Module version, one append-only line per Apply transaction, pruned to a bound that pins each
Module's newest confirmed Snapshot (ADR-0010 §Rollback, ADR-0016 §Last known good).

The two are deliberately separate files answering deliberately separate questions. The
Manifest is a claim about *now* -- "these are the bytes the app last wrote, so anything else
is a hand edit" -- and lives beside the config it describes. The Journal is history, lives
outside the hypr dir so a dotfile repo never sees its churn, and may be thrown away without
the app losing track of a single file.

The Prefs file arrives with #71.
"""

from __future__ import annotations

from .journal import (
    MAX_ENTRIES,
    Draft,
    Journal,
    JournalEntry,
    ModuleChange,
)
from .manifest import FORMAT_VERSION, Manifest, ModuleRecord, content_hash, is_damaged

__all__ = [
    "FORMAT_VERSION",
    "MAX_ENTRIES",
    "Draft",
    "Journal",
    "JournalEntry",
    "Manifest",
    "ModuleChange",
    "ModuleRecord",
    "content_hash",
    "is_damaged",
]
