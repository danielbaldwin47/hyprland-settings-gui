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
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..paths import ENTRYPOINT_NAME, ConfigPaths

FORMAT_VERSION = 4
"""Bumped from 1 when `ModuleRecord`'s `bytes` key became `size` (#51, pre-release), from
2 when a record gained the `options` it carries (#56, still pre-release), and from 3 when
the Manifest gained `quarantined` (#60, still pre-release).

An older file therefore reads as damaged rather than as a Manifest whose every record
happens to be unparseable -- which would look exactly like an empty App dir. That is the
right reading for all three bumps: a version 2 record cannot say which Options it wrote,
and guessing "all of the Section's" is exactly the over-claim `options` exists to end; a
version 3 file cannot say which requires are quarantined, and reading it as "none are"
would silently re-enable a `user.lua` the user disabled because it was breaking their
config -- putting the error back without ever saying so.
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

    options: tuple[str, ...] = ()
    """The colon-form Options this Module sets, in the order it emits them.

    The Manifest's third question -- "what, exactly, did the app write?" -- and the finest
    grain there is, because the app cannot read its own Lua back (#62). Without it the only
    available answer is the Module's whole Section, and a re-read would adopt Options that
    `user.lua` or a Bridge set, render them as the app's own, and emit them into the app's
    Module on the next write. Empty for the Entrypoint, which sets no Options."""

    @classmethod
    def of(cls, text: str, options: Sequence[str] = ()) -> ModuleRecord:
        data = text.encode("utf-8")
        return cls(sha256=content_hash(data), size=len(data), options=tuple(options))

    def matches(self, path: Path) -> bool:
        """True when the file on disk is byte-for-byte what the app wrote."""
        try:
            data = path.read_bytes()
        except OSError:
            return False
        return len(data) == self.size and content_hash(data) == self.sha256

    def as_json(self) -> dict[str, Any]:
        return {"sha256": self.sha256, "size": self.size, "options": list(self.options)}

    @classmethod
    def from_json(cls, payload: Any) -> ModuleRecord | None:
        if not isinstance(payload, dict):
            return None
        digest, size = payload.get("sha256"), payload.get("size")
        if not isinstance(digest, str) or not isinstance(size, int):
            return None
        options = payload.get("options")
        return cls(
            sha256=digest,
            size=size,
            options=tuple(str(name) for name in options) if isinstance(options, list) else (),
        )


@dataclass(frozen=True, slots=True)
class Manifest:
    """The App dir's record of itself."""

    app_version: str
    schema_version: str
    entrypoint: ModuleRecord | None = None
    modules: dict[str, ModuleRecord] = field(default_factory=dict)
    migration: dict[str, Any] | None = None
    """Provenance written once by the Importer: when, from what tree, at what hash."""

    unverified: tuple[str, ...] = ()
    """App-owned files the app cannot vouch for, because the record was lost.

    A hash says "the app wrote exactly these bytes". After a corrupt Manifest there is no
    such claim to make and none can be invented -- recording the bytes the app *would* have
    written would assert an authorship it does not have, and recording the bytes on disk
    would assert that whatever an editor left there is the app's own work.

    So the file is named instead, and stays named until a write actually resolves it. That
    is what makes the state sticky: without it, one write would relabel a directory full of
    unaccounted files as freshly authored, and the second write would overwrite them all.
    """

    quarantined: tuple[str, ...] = ()
    """`require` paths the Entrypoint currently leaves out -- ADR-0016's Quarantine.

    Require paths (`user`, `bridge/matugen`), not file paths: they are what the Entrypoint
    emits and what `ModuleSet` carries, so quarantining is one membership test where the
    require list is built rather than a path comparison where it is rendered.

    In the Manifest rather than in the Prefs file (#71), because this is a fact about the
    *config on disk* and not a preference. The generated Entrypoint physically lacks the
    line, so a user reading `hyprland.lua` sees something the app has to be able to explain
    -- and an install whose Prefs were thrown away would otherwise regenerate the require,
    silently undoing a recovery the user chose and putting the broken file back.

    Only ever holds foreign requires. An app-owned Module is recovered by *fixing* it --
    Restore last good, or the next write -- and leaving out a Module the model still renders
    would put the model and the Entrypoint permanently at odds.
    """

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
        raw_unverified = payload.get("unverified")
        raw_quarantined = payload.get("quarantined")
        return cls(
            app_version=str(payload.get("app_version", app_version)),
            schema_version=str(payload.get("schema_version", schema_version)),
            entrypoint=ModuleRecord.from_json(payload.get("entrypoint")),
            modules=modules,
            migration=migration if isinstance(migration, dict) else None,
            unverified=(
                tuple(str(name) for name in raw_unverified)
                if isinstance(raw_unverified, list)
                else ()
            ),
            quarantined=(
                tuple(sorted({str(name) for name in raw_quarantined}))
                if isinstance(raw_quarantined, list)
                else ()
            ),
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
            "unverified": list(self.unverified),
            "quarantined": list(self.quarantined),
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
        self,
        modules: dict[str, ModuleRecord],
        entrypoint: ModuleRecord | None,
        unverified: tuple[str, ...] = (),
    ) -> Manifest:
        """The Manifest after a write: a fresh Module set, provenance carried over."""
        return replace(
            self, modules=dict(modules), entrypoint=entrypoint, unverified=unverified
        )

    def with_quarantine(self, requires: Sequence[str]) -> Manifest:
        """The Manifest with exactly `requires` quarantined. Sorted, so writes are stable."""
        return replace(self, quarantined=tuple(sorted(set(requires))))

    def path_for(self, name: str, paths: ConfigPaths) -> Path:
        """Where a recorded name lives -- the Entrypoint is the one outside the App dir."""
        return paths.file_for(name)

    def hand_edited(self, paths: ConfigPaths) -> tuple[str, ...]:
        """Every app-owned file the app cannot show it wrote in its current form.

        Two ways to land here: a recorded hash that no longer matches, or a name on
        `unverified`, where there is no hash to match because the record was lost. Both mean
        the same thing to a writer -- do not overwrite this without asking.

        Includes the Entrypoint, which lives beside the App dir rather than inside it: a
        hand-edited `hyprland.lua` is the one ADR-0016 calls Entrypoint refusal, and storing
        its hash without ever comparing it would be dead data.

        A missing recorded file counts as edited -- someone deleted it, which is a change the
        app should surface rather than silently re-create. A missing *unverified* file does
        not: there was never a claim about it, and it is gone, so nothing is left to protect.
        """
        edited = {
            name
            for name, record in self.modules.items()
            if not record.matches(paths.app_dir / name)
        }
        if self.entrypoint is not None and not self.entrypoint.matches(paths.entrypoint):
            edited.add(ENTRYPOINT_NAME)
        edited |= {name for name in self.unverified if self.path_for(name, paths).is_file()}
        return tuple(sorted(edited))
