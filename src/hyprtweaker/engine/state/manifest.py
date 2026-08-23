"""The Manifest: what the app wrote, and proof of what it looked like (ADR-0005).

`manifest.json` sits in the App dir and answers three questions nothing else can:

- **Which Hyprland version's Schema produced these Modules?** A user who upgrades the
  compositor needs the app to notice, so retired Options can be kept rather than dropped
  (ADR-0012).
- **Did anyone hand-edit an app-owned file?** Each Module carries the SHA-256 of the bytes
  the app last wrote. A mismatch means an editor got there first, and the recovery is a
  banner offering restore-or-adopt -- *never* a silent overwrite (ADR-0016).
- **Where did this config come from?** Migration provenance (date, source hash) survives
  every later write, because the Importer records it once and the writer must not lose it.

The file is plain JSON with a `format_version`, read defensively: a corrupt or truncated
Manifest degrades to "everything looks hand-edited", which is annoying but safe, rather
than to a crash on startup.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1


def content_hash(data: bytes | str) -> str:
    """The SHA-256 of a file's bytes -- the hand-edit detector's whole mechanism."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """One app-written file as the app last left it."""

    sha256: str
    bytes: int

    @classmethod
    def of(cls, text: str) -> ModuleRecord:
        data = text.encode("utf-8")
        return cls(sha256=content_hash(data), bytes=len(data))

    def matches(self, path: Path) -> bool:
        """True when the file on disk is byte-for-byte what the app wrote."""
        try:
            data = path.read_bytes()
        except OSError:
            return False
        return len(data) == self.bytes and content_hash(data) == self.sha256

    def as_json(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "bytes": self.bytes}

    @classmethod
    def from_json(cls, payload: Any) -> ModuleRecord | None:
        if not isinstance(payload, dict):
            return None
        digest, size = payload.get("sha256"), payload.get("bytes")
        if not isinstance(digest, str) or not isinstance(size, int):
            return None
        return cls(sha256=digest, bytes=size)


@dataclass(frozen=True, slots=True)
class Manifest:
    """The App dir's record of itself."""

    app_version: str
    schema_version: str
    entrypoint: ModuleRecord | None = None
    modules: dict[str, ModuleRecord] = field(default_factory=dict)
    migration: dict[str, Any] | None = None
    """Provenance written once by the Importer: when, from what tree, at what hash."""

    @classmethod
    def load(cls, path: Path, *, app_version: str, schema_version: str) -> Manifest:
        """Read the Manifest, or return an empty one when it is missing or unreadable.

        Unreadable is treated as missing on purpose. The alternative -- refusing to start --
        turns a truncated write from an unrelated crash into a bricked app, and every
        consequence of an empty Manifest (modules read as hand-edited) is a prompt, not a
        loss.
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(app_version=app_version, schema_version=schema_version)

        if not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION:
            return cls(app_version=app_version, schema_version=schema_version)

        raw_modules = payload.get("modules")
        modules: dict[str, ModuleRecord] = {}
        if isinstance(raw_modules, dict):
            for name, record in raw_modules.items():
                parsed = ModuleRecord.from_json(record)
                if parsed is not None:
                    modules[str(name)] = parsed

        migration = payload.get("migration")
        return cls(
            app_version=str(payload.get("app_version", app_version)),
            schema_version=str(payload.get("schema_version", schema_version)),
            entrypoint=ModuleRecord.from_json(payload.get("entrypoint")),
            modules=modules,
            migration=migration if isinstance(migration, dict) else None,
        )

    def as_json(self) -> dict[str, Any]:
        return {
            "format_version": FORMAT_VERSION,
            "app_version": self.app_version,
            "schema_version": self.schema_version,
            "entrypoint": self.entrypoint.as_json() if self.entrypoint else None,
            "modules": {
                name: record.as_json() for name, record in sorted(self.modules.items())
            },
            "migration": self.migration,
        }

    def render(self) -> str:
        """The exact bytes to write: sorted keys, two-space indent, trailing newline.

        Formatted for a human and for `git diff`: a user's App dir may well be in a dotfile
        repo, and a one-line JSON blob that rewrites wholesale on every change is hostile
        there.
        """
        return json.dumps(self.as_json(), indent=2, sort_keys=False) + "\n"

    def with_versions(self, *, app_version: str, schema_version: str) -> Manifest:
        return replace(self, app_version=app_version, schema_version=schema_version)

    def with_modules(
        self, modules: dict[str, ModuleRecord], entrypoint: ModuleRecord | None
    ) -> Manifest:
        """The Manifest after a write: a fresh Module set, provenance carried over."""
        return replace(self, modules=dict(modules), entrypoint=entrypoint)

    def hand_edited(self, app_dir: Path) -> tuple[str, ...]:
        """The recorded Modules whose bytes on disk are not the bytes the app wrote.

        A missing file counts as edited -- someone deleted it, which is a change the app
        should surface rather than silently re-create.
        """
        return tuple(
            name
            for name, record in sorted(self.modules.items())
            if not record.matches(app_dir / name)
        )
