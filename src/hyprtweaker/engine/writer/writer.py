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

    quarantined: tuple[str, ...] = ()
    """Requires held out of the four tiers above, and present on disk (ADR-0016).

    Carried rather than discarded because the Entrypoint states them as commented-out lines:
    a `user.lua` that stopped loading has to be legible as a decision in the file itself,
    not as an absence. Only ever names files that exist -- quarantining something that is
    not there would put a comment in the config about a file the user never had.
    """

    @classmethod
    def discover(
        cls,
        paths: ConfigPaths,
        module_paths: Sequence[str],
        quarantined: Sequence[str] = (),
    ) -> ModuleSet:
        """The require order for `module_paths` plus whatever else is on disk.

        `quarantined` names require paths ADR-0016's Quarantine has disabled. They are
        dropped from the tiers the app does not own -- `legacy`, the Bridges, `user` -- and
        never from the generated Modules: those are rendered from the model, so leaving one
        out would put the Entrypoint and the model permanently at odds. A quarantine naming a
        generated Module is therefore ignored rather than obeyed.
        """
        disabled = frozenset(quarantined)
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
        legacy = paths.require_path(paths.legacy_lua) if paths.legacy_lua.is_file() else None
        user = paths.require_path(paths.user_lua) if paths.user_lua.is_file() else None

        foreign = (legacy, user, *bridges)
        held = [name for name in foreign if name is not None and name in disabled]
        return cls(
            modules=tuple(generated),
            legacy=None if legacy in disabled else legacy,
            bridges=tuple(name for name in bridges if name not in disabled),
            user=None if user in disabled else user,
            quarantined=tuple(sorted(held)),
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

    def module_options(self, model: ConfigModel) -> dict[str, tuple[str, ...]]:
        """Which Options each rendered Module carries, keyed as `render_modules` keys it.

        The Manifest records this so a later session can tell what the app wrote from what
        merely happens to live in the same Section (`apply/reread.py`). Derived from the
        same model walk as the rendering, so the two can never disagree about a Module's
        contents.
        """
        return {
            module_relpath(items[0][0]): tuple(option.name for option, _ in items)
            for section in model.sections()
            for items in (model.section(section),)
        }

    def candidate_files(self, model: ConfigModel) -> tuple[str, ...]:
        """Every app-owned name a write of `model` could create, replace or delete.

        The Journal's question, asked before the write because by then the answer's evidence
        is gone: a Snapshot of the previous bytes has to be taken while they still exist, and
        which files a write *actually* touches is only knowable from its `WriteResult`.

        Three sources, and each catches a case the others miss: what the model implies (a
        Module about to be created), what is on disk under `options/` (a Module about to be
        pruned because its Section lost its last set Option), and the Entrypoint, which lives
        outside the App dir and changes whenever the Module set does.

        Names only, and cheap: the model walk this shares with `module_options` does not
        render a single line of Lua.
        """
        names = set(self.module_options(model))
        if self._paths.options_dir.is_dir():
            names.update(
                path.relative_to(self._paths.app_dir).as_posix()
                for path in self._paths.options_dir.glob("*.lua")
                if path.is_file()
            )
        names.add(ENTRYPOINT_NAME)
        return tuple(sorted(names))

    def render_entrypoint(self, module_set: ModuleSet) -> str:
        return render_entrypoint(
            modules=module_set.modules,
            legacy=module_set.legacy,
            bridges=module_set.bridges,
            user=module_set.user,
            app_version=self._app_version,
            quarantined=module_set.quarantined,
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
        manifest = Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        )

        rendered = self.render_modules(model)
        # Read before the Entrypoint is rendered, because Quarantine is a fact about which
        # requires the Entrypoint may emit -- a write that discovered the require list first
        # would regenerate the very line the user disabled.
        module_set = ModuleSet.discover(self._paths, list(rendered), manifest.quarantined)
        entrypoint_text = self.render_entrypoint(module_set)

        gate_ran = syntax.gate_available()
        for name, text in sorted(rendered.items()):
            syntax.gate(text, name)
        syntax.gate(entrypoint_text, ENTRYPOINT_NAME)

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
        carried = self.module_options(model)
        records = {
            name: ModuleRecord.of(text, carried.get(name, ()))
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

    # --- recovery writes (ADR-0016) -----------------------------------------------------

    def restore(
        self,
        model: ConfigModel,
        module: str,
        data: bytes,
        options: Sequence[str] = (),
    ) -> bool:
        """Lay a Snapshot's bytes back down as `module`, and record them as the app's own.

        The one write in this class that does **not** come from the model, and it is the
        write ADR-0016's Restore last good is made of. It exists because the model cannot
        produce these bytes: they are what a *previous* model rendered, and the app cannot
        read its own Lua back to reconstruct that one (#62). What closes the loop is the
        caller -- a restore is followed by a reload and a re-read of `options`, which brings
        the model into step with the bytes rather than the other way round. Without that
        second half the next edit would re-render the broken version straight over this.

        **Overwrites a hand edit on purpose.** Every other path in this class stands down
        from a file an editor touched; this one is only ever reached because the user chose
        Restore last good, or because they are stranded without keybinds (§Zero-binds). The
        overwritten bytes are not lost -- the Journal snapshotted them, which is what makes
        the ADR willing to spend them.

        Recording the hash is what makes the restored file the app's own again. It has to
        be: leaving the old record would make the file it just wrote read as hand-edited, so
        the very next write would stand down from it and the user's recovery would be frozen
        in place.

        Returns whether the bytes on disk actually changed.
        """
        path = self._path_for(module)
        if path in self._paths.protected:
            raise ProtectedFile(f"{path} is never rewritten by hyprtweaker")

        text = data.decode("utf-8")
        # Gated like anything else. These bytes parsed once -- a confirmed transaction wrote
        # them -- but "nothing reaches disk ungated" is cheaper to keep than to reason about,
        # and a Snapshot store is a file tree a user can corrupt like any other.
        syntax.gate(text, module)

        changed = self._write_if_changed(path, text)
        self._record_one(model, module, ModuleRecord.of(text, options))
        return changed

    def regenerate_entrypoint(self, model: ConfigModel) -> bool:
        """Rewrite `hyprland.lua` from the Module set, whatever is in it now.

        ADR-0016's Entrypoint recovery, and the reason that class gets a one-click Fix while
        a broken Module gets a Banner: the Entrypoint holds no user decisions at all. It is
        derived entirely from which files exist and which requires are quarantined, so
        regenerating it can lose nothing -- there is no hand edit here worth the name, only a
        file that has stopped doing its one job.

        Unconditional, unlike `write`, which stands down from a hand-edited Entrypoint. That
        is the whole point: a hand edit is exactly how the Entrypoint gets broken in the
        first place (the app syntax-gates its own writes), so a recovery that respected it
        would refuse in precisely the case it exists for.
        """
        manifest = self._manifest_for(model)
        rendered = self.render_modules(model)
        module_set = ModuleSet.discover(self._paths, list(rendered), manifest.quarantined)
        text = self.render_entrypoint(module_set)
        syntax.gate(text, ENTRYPOINT_NAME)

        changed = self._write_if_changed(self._paths.entrypoint, text)
        self._save(replace(manifest, entrypoint=ModuleRecord.of(text)))
        return changed

    def set_quarantine(self, model: ConfigModel, requires: Sequence[str]) -> bool:
        """Record exactly `requires` as quarantined and regenerate the Entrypoint.

        One call for both halves, because they are one act: the Manifest is where the
        decision lives and the Entrypoint is where it takes effect, and an install that
        recorded one without the other would either keep loading a file it believes it
        disabled or keep excluding one it believes it re-enabled.

        Reversal is this same call with the name removed -- which is what makes the ADR's
        "one-click re-enable" one click rather than an undo path of its own.
        """
        self._save(self._manifest_for(model).with_quarantine(requires))
        return self.regenerate_entrypoint(model)

    # --- internals ----------------------------------------------------------------------

    def _path_for(self, module: str) -> Path:
        """Where an app-owned name lives. The Entrypoint is the one outside the App dir."""
        if module == ENTRYPOINT_NAME:
            return self._paths.entrypoint
        return self._paths.app_dir / module

    def _manifest_for(self, model: ConfigModel) -> Manifest:
        return Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        )

    def _record_one(self, model: ConfigModel, module: str, record: ModuleRecord) -> None:
        """Replace one file's Manifest record, leaving every other claim untouched.

        Loaded and saved rather than held, because the Manifest on disk is the shared truth
        and a recovery write is not the only thing that edits it. Reading it fresh is what
        keeps a restore from reverting a record an Apply transaction wrote a moment earlier.
        """
        manifest = self._manifest_for(model)
        if module == ENTRYPOINT_NAME:
            updated = replace(manifest, entrypoint=record)
        else:
            updated = replace(manifest, modules={**manifest.modules, module: record})
        # A restored file is accounted for again, so it is no longer unverifiable.
        self._save(
            replace(updated, unverified=tuple(n for n in updated.unverified if n != module))
        )

    def _save(self, manifest: Manifest) -> None:
        self._paths.app_dir.mkdir(parents=True, exist_ok=True)
        self._write_if_changed(self._paths.manifest, manifest.render())

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

        A consequence worth stating: after a lost record there is nothing the app can claim,
        so a stale Module can outlive the Section that produced it. That is the right trade.
        The Manifest is the *only* thing separating the app's files from the user's, and
        with it gone, scanning the directory and deleting what looks familiar is precisely
        the guess this method exists to refuse. The file is inert -- no Entrypoint requires
        it -- and an explicit overwrite re-establishes the record that makes it prunable.
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
