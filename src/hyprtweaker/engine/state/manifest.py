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
Manifest never crashes the app. It does not read as "nothing was ever written" either --
that would make every hand edit invisible and therefore silently overwritable, now that
`hand_edited` gates the writer. `is_damaged` is the distinction that keeps that honest:

- **absent** -- a fresh App dir. Nothing was written here, so there is nothing to protect.
- **present but unreadable** -- the app wrote here and lost its record. It can vouch for
  nothing in the directory, so the writer treats the whole set as hand-edited rather than
  overwriting on a guess.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..paths import ENTRYPOINT_NAME, ConfigPaths

FORMAT_VERSION = 2
"""Bumped from 1 when `ModuleRecord`'s `bytes` key became `size` (#51, pre-release).

An older file therefore reads as damaged rather than as a Manifest whose every record
happens to be unparseable -- which would look exactly like an empty App dir.
"""


def is_damaged(path: Path) -> bool:
    """True when a Manifest file exists but cannot be read as one.

    Deliberately distinct from *absent*. Both make `load` return an empty Manifest, but they
    mean opposite things to a writer about to overwrite files: no Manifest is a fresh
    directory, an unreadable one is a directory full of files the app can no longer account
    for.
    """
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not isinstance(payload, dict) or payload.get("format_version") != FORMAT_VERSION


def content_hash(data: bytes | str) -> str:
    """The SHA-256 of a file's bytes -- the hand-edit detector's whole mechanism."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class ModuleRecord:
    """One app-written file as the app last left it."""

    sha256: str
    size: int
    """Length in bytes. A cheap first comparison, and a sanity check on the digest."""

    @classmethod
    def of(cls, text: str) -> ModuleRecord:
        data = text.encode("utf-8")
        return cls(sha256=content_hash(data), size=len(data))

    def matches(self, path: Path) -> bool:
        """True when the file on disk is byte-for-byte what the app wrote."""
        try:
            data = path.read_bytes()
        except OSError:
            return False
        return len(data) == self.size and content_hash(data) == self.sha256

    def as_json(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size}

    @classmethod
    def from_json(cls, payload: Any) -> ModuleRecord | None:
        if not isinstance(payload, dict):
            return None
        digest, size = payload.get("sha256"), payload.get("size")
        if not isinstance(digest, str) or not isinstance(size, int):
            return None
        return cls(sha256=digest, size=size)


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

        Never raises: refusing to start would turn a truncated write from an unrelated crash
        into a bricked app. Callers that are about to *overwrite* files need more than an
        empty Manifest, though -- they need to know whether it was empty because there was
        nothing, or because the record was lost. That is `is_damaged`.
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

    def hand_edited(self, paths: ConfigPaths) -> tuple[str, ...]:
        """Every app-owned file whose bytes on disk are not the bytes the app wrote.

        Includes the Entrypoint, which lives beside the App dir rather than inside it: a
        hand-edited `hyprland.lua` is the one ADR-0016 calls Entrypoint refusal, and storing
        its hash without ever comparing it would be dead data.

        A missing file counts as edited -- someone deleted it, which is a change the app
        should surface rather than silently re-create.
        """
        edited = [
            name
            for name, record in sorted(self.modules.items())
            if not record.matches(paths.app_dir / name)
        ]
        if self.entrypoint is not None and not self.entrypoint.matches(paths.entrypoint):
            edited.append(ENTRYPOINT_NAME)
        return tuple(edited)
