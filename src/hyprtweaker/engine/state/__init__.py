"""State: the Manifest now; Snapshots, Journal and the Prefs file later.

The Manifest (`manifest.json` in the App dir) records what the app wrote and what it
looked like: app version, Schema version, a SHA-256 per Module for hand-edit detection,
and the migration provenance the Importer stamps once (ADR-0005).

Snapshots and the Journal arrive with #59, the Prefs file with #71.
"""

from __future__ import annotations

from .manifest import FORMAT_VERSION, Manifest, ModuleRecord, content_hash

__all__ = ["FORMAT_VERSION", "Manifest", "ModuleRecord", "content_hash"]
