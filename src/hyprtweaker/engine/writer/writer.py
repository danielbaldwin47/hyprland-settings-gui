"""The Writer: model in, App dir out.

One object owns the whole render-gate-write cycle, because the three steps only make sense
in that order (ADR-0010):

1. **render everything first.** Modules are rendered whole, never patched -- a partial
   Module is a Module whose missing values silently revert on the next reload.
2. **syntax-gate before touching disk.** A Lua syntax error aborts the *entire* reload, so
   a single bad byte would take the user's binds and monitors down with it. The gate turns
   that into an exception in the app instead.
3. **write only what changed, atomically.** Identical bytes are skipped: Hyprland watches
   every `require`d file, so rewriting an unchanged Module buys a reload for nothing.

What the Writer will not do is touch `user.lua` or `legacy.lua`. That is checked against
`ConfigPaths.protected` rather than merely avoided, so a future caller cannot lose the
escape hatch by passing the wrong path.

The Apply *transaction* -- debounce, reload, read-back, auto-revert -- is #54 and wraps
this; the Writer stays synchronous and ignorant of the compositor.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..model.options import ConfigModel
from ..paths import ENTRYPOINT_NAME, ConfigPaths
from ..state.manifest import Manifest, ModuleRecord
from ..state.manifest import is_damaged as manifest_is_damaged
from . import syntax
from .modules import is_option_module, module_relpath, render_entrypoint, render_module


@dataclass(frozen=True, slots=True)
class ModuleSet:
    """Everything the Entrypoint has to require, in the four ADR-0005 tiers.

    Derived from what is actually on disk rather than from the model: `legacy.lua`, the
    Bridge modules and `user.lua` are all files the app does not write, so their presence
    is the only honest source. Requiring one that does not exist adds an error to every
    reload.
    """

    modules: tuple[str, ...]
    legacy: str | None
    bridges: tuple[str, ...]
    user: str | None

    @classmethod
    def discover(cls, paths: ConfigPaths, module_paths: Sequence[str]) -> ModuleSet:
        """The require order for `module_paths` plus whatever else is on disk."""
        generated = []
        if paths.vars_lua.is_file():
            # First: the imported `$variable` table the other Modules read.
            generated.append(paths.require_path(paths.vars_lua))
        generated += [paths.require_path(paths.app_dir / name) for name in sorted(module_paths)]

        bridges = (
            sorted(
                paths.require_path(path)
                for path in paths.bridge_dir.glob("*.lua")
                if path.is_file()
            )
            if paths.bridge_dir.is_dir()
            else []
        )

        return cls(
            modules=tuple(generated),
            legacy=paths.require_path(paths.legacy_lua) if paths.legacy_lua.is_file() else None,
            bridges=tuple(bridges),
            user=paths.require_path(paths.user_lua) if paths.user_lua.is_file() else None,
        )


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What one `Writer.write` actually did, for the Journal and for tests."""

    written: tuple[str, ...]
    unchanged: tuple[str, ...]
    removed: tuple[str, ...]
    entrypoint_written: bool
    hand_edited: tuple[str, ...]
    """App-owned files whose bytes on disk did not match the Manifest before this write."""

    skipped: tuple[str, ...] = ()
    """Files this write would have changed but left alone because they were hand-edited.

    The model and the disk now disagree for these, on purpose. Resolving that is a user
    decision -- adopt-into-legacy or overwrite (ADR-0005) -- surfaced by the Banner
    (ADR-0016), so the Writer reports rather than picks.
    """

    syntax_gate_ran: bool = True
    """False when no `luac` was on this machine, so nothing was actually parse-checked.

    The gate degrades rather than blocking a save (`syntax.gate_available`), but a caller
    that assumed a guarantee it did not get would be worse than one that knows.
    """

    @property
    def changed(self) -> bool:
        return bool(self.written or self.removed or self.entrypoint_written)


class ProtectedFile(Exception):
    """An attempt to write a file the app has promised never to rewrite."""


class Writer:
    """Renders a `ConfigModel` into an App dir and keeps the Manifest honest."""

    def __init__(self, paths: ConfigPaths, app_version: str) -> None:
        self._paths = paths
        self._app_version = app_version

    @property
    def paths(self) -> ConfigPaths:
        return self._paths

    # --- rendering (pure) ---------------------------------------------------------------

    def render_modules(self, model: ConfigModel) -> dict[str, str]:
        """Every Module the model implies, keyed by its App-dir-relative path.

        A Section with no set Options yields no Module at all -- and the write step then
        deletes any file left over from when it did, because a stale Module keeps applying
        values the user has since reset.
        """
        rendered: dict[str, str] = {}
        sources: dict[str, str] = {}
        for section in model.sections():
            items = model.section(section)
            relpath = module_relpath(items[0][0])
            if relpath in rendered:
                # Two Sections sharing a Lua root would silently drop a whole Module's
                # worth of settings. Clean on 0.56.2; a future release must not make it
                # true quietly.
                raise ValueError(
                    f"Sections {sources[relpath]!r} and {section!r} both render to "
                    f"{relpath}; one of them would be lost"
                )
            sources[relpath] = section
            rendered[relpath] = render_module(items, app_version=self._app_version)
        return rendered

    def render_entrypoint(self, module_set: ModuleSet) -> str:
        return render_entrypoint(
            modules=module_set.modules,
            legacy=module_set.legacy,
            bridges=module_set.bridges,
            user=module_set.user,
            app_version=self._app_version,
        )

    # --- writing ------------------------------------------------------------------------

    def write(self, model: ConfigModel, *, overwrite_hand_edits: bool = False) -> WriteResult:
        """Render, gate, and land the whole Module set plus the Entrypoint.

        Files an editor got to first are **skipped**, not rewritten. ADR-0005 makes that a
        user's choice -- "on mismatch the app warns and offers adopt-into-legacy or
        overwrite" -- and ADR-0016 spells out the recovery: a Banner offering
        restore-last-known-good or open-in-editor, never an automatic write. So the default
        reports and stands down; `overwrite_hand_edits=True` is the caller carrying the
        user's answer back in.

        Nothing reaches disk until every rendered file has passed the syntax gate: a
        half-written Module set is worse than no write at all.
        """
        rendered = self.render_modules(model)
        module_set = ModuleSet.discover(self._paths, list(rendered))
        entrypoint_text = self.render_entrypoint(module_set)

        gate_ran = syntax.gate_available()
        for name, text in sorted(rendered.items()):
            syntax.gate(text, name)
        syntax.gate(entrypoint_text, ENTRYPOINT_NAME)

        manifest = Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        )
        if manifest_is_damaged(self._paths.manifest):
            # The record was lost, so every file already here is unaccounted for. Recorded
            # from now on by name rather than by hash: there is no hash to record, and this
            # write must not be the one that quietly claims authorship of them.
            manifest = replace(manifest, unverified=self._everything_here(rendered))

        hand_edited = manifest.hand_edited(self._paths)
        off_limits: frozenset[str] = (
            frozenset() if overwrite_hand_edits else frozenset(hand_edited)
        )

        self._paths.options_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        unchanged: list[str] = []
        skipped: list[str] = []
        for name, text in sorted(rendered.items()):
            if name in off_limits:
                skipped.append(name)
            elif self._write_if_changed(self._paths.app_dir / name, text):
                written.append(name)
            else:
                unchanged.append(name)

        removed = self._prune(manifest, keep=set(rendered), off_limits=off_limits)

        if ENTRYPOINT_NAME in off_limits:
            skipped.append(ENTRYPOINT_NAME)
            entrypoint_written = False
        else:
            entrypoint_written = self._write_if_changed(self._paths.entrypoint, entrypoint_text)

        # A record is the claim "the app wrote exactly these bytes", so it is only ever made
        # for a file this write actually laid down. A skipped file keeps the record it had,
        # or -- when there is none, because the Manifest was lost -- keeps its place on
        # `unverified` instead. Recording bytes nobody wrote would erase the very edit that
        # was just detected.
        records = {
            name: ModuleRecord.of(text)
            for name, text in rendered.items()
            if name not in off_limits
        }
        # A spared Module the model no longer renders was skipped by `_prune` too, so its
        # record has to survive. Dropping it would leave an orphan file that nothing
        # requires, nothing reports, and no later "overwrite" answer could ever reach.
        records.update(
            {
                name: record
                for name, record in manifest.modules.items()
                if name in off_limits and name not in records
            }
        )
        manifest = manifest.with_versions(
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        ).with_modules(
            records,
            manifest.entrypoint
            if ENTRYPOINT_NAME in off_limits
            else ModuleRecord.of(entrypoint_text),
            unverified=tuple(name for name in manifest.unverified if name in off_limits),
        )
        self._write_if_changed(self._paths.manifest, manifest.render())

        return WriteResult(
            written=tuple(written),
            unchanged=tuple(unchanged),
            removed=tuple(removed),
            entrypoint_written=entrypoint_written,
            hand_edited=hand_edited,
            skipped=tuple(sorted(skipped)),
            syntax_gate_ran=gate_ran,
        )

    # --- internals ----------------------------------------------------------------------

    def _everything_here(self, rendered: dict[str, str]) -> tuple[str, ...]:
        """Every file this write would touch that already exists -- the lost-record answer.

        Reached only when a Manifest file is there but will not parse: the app wrote in this
        directory and lost its record, so it cannot vouch for a single byte of it. Refusing
        to overwrite on a guess is the same call as for a real hand edit, and it lands the
        user in the same place -- a Banner offering overwrite (ADR-0016) -- rather than
        quietly replacing files it can no longer account for.

        Scoped to what this write would touch, so a file the app was never going to write is
        never dragged in.
        """
        return tuple(
            sorted(
                [name for name in rendered if (self._paths.app_dir / name).is_file()]
                + ([ENTRYPOINT_NAME] if self._paths.entrypoint.is_file() else [])
            )
        )

    def _write_if_changed(self, path: Path, text: str) -> bool:
        """Write `text` atomically unless the file already holds exactly those bytes."""
        if path in self._paths.protected:
            raise ProtectedFile(f"{path} is never rewritten by hyprtweaker")

        data = text.encode("utf-8")
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass

        path.parent.mkdir(parents=True, exist_ok=True)
        # Same directory, so the replace is a rename within one filesystem: a reader (the
        # compositor's watcher, or an editor) sees either the old file or the new one.
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return True

    def _prune(
        self, manifest: Manifest, keep: set[str], off_limits: frozenset[str]
    ) -> list[str]:
        """Delete Modules the model no longer produces.

        Scoped to `options/` and to files the Manifest says the app wrote: a Module the app
        never claimed is somebody else's, and deleting it would be exactly the "manager over
        your dots" behaviour the app refuses (ADR-0005). A hand-edited Module is somebody
        else's too now, so deleting it is as wrong as overwriting it.
        """
        removed: list[str] = []
        for name in sorted(manifest.modules):
            if name in keep or name in off_limits or not is_option_module(name):
                continue
            path = self._paths.app_dir / name
            if path.is_file():
                path.unlink()
                removed.append(name)
        return removed
