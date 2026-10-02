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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..bridge import (
    BridgeEntry,
    bridges_from_entrypoint,
    in_require_order,
    render_line,
    same_module,
)
from ..model.options import ConfigModel
from ..paths import (
    ANIMATIONS_MODULE,
    AUTOSTART_MODULE,
    BINDS_MODULE,
    DEVICES_MODULE,
    ENTRYPOINT_NAME,
    ENV_MODULE,
    GESTURES_MODULE,
    LAYER_RULES_MODULE,
    MONITORS_MODULE,
    PERMISSIONS_MODULE,
    PLUGINS_MODULE,
    WINDOW_RULES_MODULE,
    WORKSPACE_RULES_MODULE,
    ConfigPaths,
)
from ..state.manifest import Manifest, ModuleRecord, RetiredValue
from ..state.manifest import is_damaged as manifest_is_damaged
from . import syntax
from .animations import render_animations_module
from .binds import render_binds_module
from .inputs import render_devices_module, render_gestures_module
from .lua import GENERATED_BANNER
from .modules import (
    ENTITY_MODULES,
    BridgeRequire,
    is_entity_module,
    is_generated_module,
    module_relpath,
    render_entrypoint,
    render_module,
)
from .monitors import render_monitors_module, render_workspace_rules_module
from .plugins import render_plugins_module
from .rules import render_layer_rules_module, render_window_rules_module
from .session_scope import (
    render_autostart_module,
    render_env_module,
    render_permissions_module,
)


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
    bridges: tuple[BridgeRequire, ...]
    """Every Bridge line: the Manifest's entries in require order, each loading or commented
    with its reason, then any `bridge/*.lua` no entry names, loading as it always has."""

    user: str | None

    quarantined: tuple[str, ...] = ()
    """The lines held out of the four tiers above, of files present on disk (ADR-0016).

    Carried rather than discarded because the Entrypoint states them as commented-out lines:
    a `user.lua` that stopped loading has to be legible as a decision in the file itself,
    not as an absence. Only ever names files that exist -- quarantining something that is
    not there would put a comment in the config about a file the user never had.
    """

    @property
    def foreign(self) -> tuple[tuple[str, str], ...]:
        """Every require the app does not own, with its hypr-dir-relative file: `legacy`,
        the Bridges, `user`.

        The three tiers Quarantine may touch, and the ones the Writer must never rewrite.
        """
        named = [(name, f"{name}.lua") for name in (self.legacy, self.user) if name is not None]
        return (*named, *((bridge.require, bridge.file) for bridge in self.bridges))

    def require_for(self, path: str) -> str | None:
        """The foreign `require` a printed error path names, or `None` for none of them.

        Matched by suffix against the files this app would actually require, never derived
        from the printed path by stripping a prefix -- the path Hyprland printed is the one
        it opened, which may have travelled through a symlinked dotfile directory or a `$HOME`
        resolved differently (`ownership.py` documents the same hazard). A quarantine recorded
        under a name the Entrypoint never emits would be a Banner claiming a file is disabled
        while the config went on loading it. A `require("<name>")` failure names the require
        itself, in either spelling.
        """
        for require, file in self.foreign:
            if path == file or path.endswith(f"/{file}") or same_module(path, require):
                return require
        return None

    @classmethod
    def discover(
        cls,
        paths: ConfigPaths,
        module_paths: Sequence[str],
        quarantined: Sequence[str] = (),
        bridges: Sequence[BridgeEntry] = (),
    ) -> ModuleSet:
        """The require order for `module_paths` plus whatever else is on disk.

        `quarantined` names require paths ADR-0016's Quarantine has disabled. They are
        dropped from the tiers the app does not own -- `legacy`, the Bridges, `user` -- and
        never from the generated Modules: those are rendered from the model, so leaving one
        out would put the Entrypoint and the model permanently at odds. A quarantine naming a
        generated Module is therefore ignored rather than obeyed.

        `bridges` are the Manifest's entries (ADR-0006 §Placement). Each renders its line,
        native path or not; one whose file is missing renders as waiting, never as a require
        that would fail. A `bridge/*.lua` no entry names keeps loading as before #163: a
        hand-placed module, or one whose entry went with a lost Manifest, must not silently
        stop.
        """
        disabled = frozenset(quarantined)
        generated = []
        if paths.vars_lua.is_file():
            # First: the imported `$variable` table the other Modules read.
            generated.append(paths.require_path(paths.vars_lua))
        generated += [paths.require_path(paths.app_dir / name) for name in sorted(module_paths)]

        held: list[str] = []
        lines: list[BridgeRequire] = []
        for entry in in_require_order(bridges):
            present = (paths.hypr_dir / entry.file).is_file()
            if entry.module in disabled and present:
                held.append(entry.line)
            else:
                text = render_line(entry, present=present)
                lines.append(BridgeRequire(entry.module, entry.file, text))
        named = {entry.file for entry in bridges}
        unregistered = (
            sorted(path for path in paths.bridge_dir.glob("*.lua") if path.is_file())
            if paths.bridge_dir.is_dir()
            else []
        )
        for path in unregistered:
            file = path.relative_to(paths.hypr_dir).as_posix()
            if file in named:
                continue
            require = paths.require_path(path)
            if require in disabled:
                held.append(f'require("{require}")')
            else:
                lines.append(BridgeRequire(require, file, f'require("{require}")'))

        legacy = paths.require_path(paths.legacy_lua) if paths.legacy_lua.is_file() else None
        user = paths.require_path(paths.user_lua) if paths.user_lua.is_file() else None
        held += [f'require("{name}")' for name in (legacy, user) if name in disabled]
        return cls(
            modules=tuple(generated),
            legacy=None if legacy in disabled else legacy,
            bridges=tuple(lines),
            user=None if user in disabled else user,
            quarantined=tuple(sorted(held)),
        )


def load_manifest(paths: ConfigPaths, *, app_version: str, schema_version: str) -> Manifest:
    """The Manifest, with its Bridge entries rebuilt from the Entrypoint if it was lost.

    Once requires come from the Manifest, an absent or unreadable one would drop every Bridge
    line from the next Entrypoint and silently end the user's theming. The app's own
    Entrypoint states every entry as a line (S4), so it is read back into entries instead
    (`bridges_from_entrypoint`). Only the app's own: a foreign `hyprland.lua` is the
    Migration wizard's to import, not this function's to adopt.
    """
    manifest = Manifest.load(
        paths.manifest, app_version=app_version, schema_version=schema_version
    )
    if paths.manifest.is_file() and not manifest_is_damaged(paths.manifest):
        return manifest
    try:
        text = paths.entrypoint.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return manifest
    if not text.startswith(GENERATED_BANNER.split("{", 1)[0]):
        return manifest
    return manifest.with_bridges(bridges_from_entrypoint(text))


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


BeforeReplace = Callable[[Path], None]
"""Called with a file's path just before the Writer replaces or deletes it.

How a transaction's Journal Draft makes the bytes about to go durable (`Draft.preserve`)
while they still exist. Per call rather than held: the Draft is one transaction's, and the
Writer outlives every transaction.
"""


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

        entities = model.entities
        version = self._app_version
        # One table rather than a block per kind: with eleven Entity Modules the
        # render-then-skip-if-None shape is the same four lines eleven times, and the
        # kinds that were forgotten in an earlier pass were the ones furthest down the
        # repetition. `None` means "nothing to write", which the prune reads as a deletion.
        entity_modules: tuple[tuple[str, str | None], ...] = (
            (BINDS_MODULE, render_binds_module(entities, app_version=version)),
            (
                WINDOW_RULES_MODULE,
                render_window_rules_module(entities.window_rules, app_version=version),
            ),
            (
                LAYER_RULES_MODULE,
                render_layer_rules_module(entities.layer_rules, app_version=version),
            ),
            (MONITORS_MODULE, render_monitors_module(entities.monitors, app_version=version)),
            (
                WORKSPACE_RULES_MODULE,
                render_workspace_rules_module(entities.workspace_rules, app_version=version),
            ),
            (
                ANIMATIONS_MODULE,
                render_animations_module(
                    entities.curves, entities.animations, app_version=version
                ),
            ),
            (GESTURES_MODULE, render_gestures_module(entities.gestures, app_version=version)),
            (DEVICES_MODULE, render_devices_module(entities.devices, app_version=version)),
            (ENV_MODULE, render_env_module(entities.env, app_version=version)),
            (
                PERMISSIONS_MODULE,
                render_permissions_module(entities.permissions, app_version=version),
            ),
            (AUTOSTART_MODULE, render_autostart_module(entities.startup, app_version=version)),
            (PLUGINS_MODULE, render_plugins_module(entities.plugins, app_version=version)),
        )
        for relpath, text in entity_modules:
            if text is not None:
                rendered[relpath] = text
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
        names.update(ENTITY_MODULES)
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

    def write(
        self,
        model: ConfigModel,
        *,
        overwrite_hand_edits: bool = False,
        before_replace: BeforeReplace | None = None,
    ) -> WriteResult:
        """Render, gate, and land the whole Module set plus the Entrypoint.

        Files an editor got to first are **skipped**, not rewritten. ADR-0005 makes that a
        user's choice -- "on mismatch the app warns and offers adopt-into-legacy or
        overwrite" -- and ADR-0016 spells out the recovery: a Banner offering
        restore-last-known-good or open-in-editor, never an automatic write. So the default
        reports and stands down; `overwrite_hand_edits=True` is the caller carrying the
        user's answer back in.

        Nothing reaches disk until every rendered file has passed the syntax gate: a
        half-written Module set is worse than no write at all. `before_replace` sees each
        Module and the Entrypoint before it is replaced or pruned.
        """
        manifest = self._manifest_for(model)

        rendered = self.render_modules(model)
        # Read before the Entrypoint is rendered, because Quarantine is a fact about which
        # requires the Entrypoint may emit -- a write that discovered the require list first
        # would regenerate the very line the user disabled.
        module_set = ModuleSet.discover(
            self._paths, list(rendered), manifest.quarantined, manifest.bridges
        )
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
            elif self._write_if_changed(self._paths.app_dir / name, text, before_replace):
                written.append(name)
            else:
                unchanged.append(name)

        removed = self._prune(
            manifest,
            keep=set(rendered),
            off_limits=off_limits,
            prune_entities=model.entities_loaded,
            before_replace=before_replace,
        )

        if ENTRYPOINT_NAME in off_limits:
            skipped.append(ENTRYPOINT_NAME)
            entrypoint_written = False
        else:
            entrypoint_written = self._write_if_changed(
                self._paths.entrypoint, entrypoint_text, before_replace
            )

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
        *,
        before_replace: BeforeReplace | None = None,
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
        overwritten bytes are not lost -- `before_replace` hands them to the Journal before
        the rename, which is what makes the ADR willing to spend them.

        Recording the hash is what makes the restored file the app's own again. It has to
        be: leaving the old record would make the file it just wrote read as hand-edited, so
        the very next write would stand down from it and the user's recovery would be frozen
        in place.

        Returns whether the bytes on disk actually changed.
        """
        path = self._paths.file_for(module)
        if path in self._paths.protected:
            raise ProtectedFile(f"{path} is never rewritten by hyprtweaker")

        text = data.decode("utf-8")
        # Gated like anything else. These bytes parsed once -- a confirmed transaction wrote
        # them -- but "nothing reaches disk ungated" is cheaper to keep than to reason about,
        # and a Snapshot store is a file tree a user can corrupt like any other.
        syntax.gate(text, module)

        changed = self._write_if_changed(path, text, before_replace)
        self._record_one(model, module, ModuleRecord.of(text, options))
        return changed

    def regenerate_entrypoint(
        self, model: ConfigModel, *, before_replace: BeforeReplace | None = None
    ) -> bool:
        """Rewrite `hyprland.lua` from the Module set, whatever is in it now.

        ADR-0016's Entrypoint recovery, and the reason that class gets a one-click Fix while
        a broken Module gets a Banner: the Entrypoint holds no user decisions at all. It is
        derived entirely from which files exist, which requires are quarantined and what the
        Manifest's Bridge entries say, so regenerating it can lose nothing -- there is no
        hand edit here worth the name, only a file that has stopped doing its one job.

        Unconditional, unlike `write`, which stands down from a hand-edited Entrypoint. That
        is the whole point: a hand edit is exactly how the Entrypoint gets broken in the
        first place (the app syntax-gates its own writes), so a recovery that respected it
        would refuse in precisely the case it exists for.

        `before_replace` is called just before the rename, as in `write`: the recovery's
        Journal draft keeps the bytes being overwritten (ADR-0010 §Rollback).
        """
        manifest = self._manifest_for(model)
        text = self.entrypoint_text(model, manifest)
        changed = self._write_if_changed(self._paths.entrypoint, text, before_replace)
        self._save(replace(manifest, entrypoint=ModuleRecord.of(text)))
        return changed

    def entrypoint_text(self, model: ConfigModel, manifest: Manifest) -> str:
        """The Entrypoint this model renders under `manifest`'s Quarantine and Bridge
        entries, syntax-gated.

        Writes nothing, so a recovery can find out it would be refused before it is queued.
        """
        rendered = self.render_modules(model)
        module_set = ModuleSet.discover(
            self._paths, list(rendered), manifest.quarantined, manifest.bridges
        )
        text = self.render_entrypoint(module_set)
        syntax.gate(text, ENTRYPOINT_NAME)
        return text

    def set_quarantine(
        self,
        model: ConfigModel,
        requires: Sequence[str],
        *,
        before_replace: BeforeReplace | None = None,
    ) -> bool:
        """Record exactly `requires` as quarantined and regenerate the Entrypoint.

        One call for both halves, because they are one act: the Manifest is where the
        decision lives and the Entrypoint is where it takes effect, and an install that
        recorded one without the other would either keep loading a file it believes it
        disabled or keep excluding one it believes it re-enabled.

        Reversal is this same call with the name removed -- which is what makes the ADR's
        "one-click re-enable" one click rather than an undo path of its own.
        """
        self._save(self._manifest_for(model).with_quarantine(requires))
        return self.regenerate_entrypoint(model, before_replace=before_replace)

    def set_bridges(
        self,
        model: ConfigModel,
        entries: Sequence[BridgeEntry],
        *,
        before_replace: BeforeReplace | None = None,
    ) -> bool:
        """Record exactly `entries` as the Bridge entries and regenerate the Entrypoint.

        `set_quarantine`'s shape and for its reason: the Manifest is where a Color source
        choice lives and the Entrypoint is where it takes effect (ADR-0014). Returns whether
        the Entrypoint's bytes changed.
        """
        self.record_bridges(model, entries)
        return self.regenerate_entrypoint(model, before_replace=before_replace)

    def record_bridges(self, model: ConfigModel, entries: Sequence[BridgeEntry]) -> None:
        """Record exactly `entries`, Manifest only: the next `write` renders their lines.

        For a caller whose own Apply transaction carries the Entrypoint -- a Preset applying
        its colours with the bridges gated in the same write (#170, S6) -- so one reload
        lands both rather than two.
        """
        self._save(self._manifest_for(model).with_bridges(entries))

    def set_retired(self, model: ConfigModel, retired: Mapping[str, RetiredValue]) -> None:
        """Record exactly `retired` as the kept values of removed Options (ADR-0012).

        Manifest only: the Module stops carrying a retired key on the next `write`, because
        the model no longer renders it. Loaded fresh and saved, like `set_quarantine`, so it
        never reverts a record another write made a moment earlier.
        """
        self._save(self._manifest_for(model).with_retired(retired))

    def record_retired_notice(self, model: ConfigModel, release: str) -> None:
        """Record that the user saw `release`'s Retired notice (ADR-0012). Manifest only."""
        self._save(self._manifest_for(model).with_retired_notice(release))

    # --- internals ----------------------------------------------------------------------

    def _manifest_for(self, model: ConfigModel) -> Manifest:
        return load_manifest(
            self._paths,
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

    def _write_if_changed(
        self, path: Path, text: str, before_replace: BeforeReplace | None = None
    ) -> bool:
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
        if before_replace is not None:
            before_replace(path)
        os.replace(temporary, path)
        return True

    def _prune(
        self,
        manifest: Manifest,
        keep: set[str],
        off_limits: frozenset[str],
        *,
        prune_entities: bool = True,
        before_replace: BeforeReplace | None = None,
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
            if name in keep or name in off_limits or not is_generated_module(name):
                continue
            if is_entity_module(name) and not prune_entities:
                # The model's Entity half was never read, so "the model renders no binds"
                # is ignorance, not a decision -- and deleting the file on the strength of
                # it would throw away every bind the user has. An Option Module cannot
                # reach this state: Options are recovered from the compositor at startup.
                continue
            path = self._paths.app_dir / name
            if path.is_file():
                if before_replace is not None:
                    before_replace(path)
                path.unlink()
                removed.append(name)
        return removed
