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
from dataclasses import dataclass
from pathlib import Path

from .. import paths as paths_module
from ..model.options import ConfigModel
from ..paths import ConfigPaths
from ..state.manifest import Manifest, ModuleRecord
from . import syntax
from .modules import module_relpath, render_entrypoint, render_module


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


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What one `Writer.write` actually did, for the Journal and for tests."""

    written: tuple[str, ...]
    unchanged: tuple[str, ...]
    removed: tuple[str, ...]
    entrypoint_written: bool
    hand_edited: tuple[str, ...]
    """Modules whose bytes on disk did not match the Manifest *before* this write."""

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
        for section in model.sections():
            items = model.section(section)
            rendered[module_relpath(items[0][0])] = render_module(
                items, app_version=self._app_version
            )
        return rendered

    def discover_module_set(self, module_paths: list[str]) -> ModuleSet:
        """Turn the rendered Module list plus what is on disk into the require order."""
        app_dir = self._paths.app_dir

        generated = []
        if self._paths.vars_lua.is_file():
            # First: the imported `$variable` table the other Modules read.
            generated.append(self._paths.require_path(self._paths.vars_lua))
        generated += [self._paths.require_path(app_dir / name) for name in sorted(module_paths)]

        bridges = (
            sorted(
                self._paths.require_path(path)
                for path in self._paths.bridge_dir.glob("*.lua")
                if path.is_file()
            )
            if self._paths.bridge_dir.is_dir()
            else []
        )

        return ModuleSet(
            modules=tuple(generated),
            legacy=(
                self._paths.require_path(self._paths.legacy_lua)
                if self._paths.legacy_lua.is_file()
                else None
            ),
            bridges=tuple(bridges),
            user=(
                self._paths.require_path(self._paths.user_lua)
                if self._paths.user_lua.is_file()
                else None
            ),
        )

    def render_entrypoint(self, module_set: ModuleSet) -> str:
        return render_entrypoint(
            modules=module_set.modules,
            legacy=module_set.legacy,
            bridges=module_set.bridges,
            user=module_set.user,
            app_version=self._app_version,
        )

    # --- writing ------------------------------------------------------------------------

    def write(self, model: ConfigModel) -> WriteResult:
        """Render, gate, and land the whole Module set plus the Entrypoint."""
        rendered = self.render_modules(model)
        module_set = self.discover_module_set(list(rendered))
        entrypoint_text = self.render_entrypoint(module_set)

        # Gate everything before anything lands: a half-written set is worse than no write.
        for name, text in sorted(rendered.items()):
            syntax.gate(text, name)
        syntax.gate(entrypoint_text, paths_module.ENTRYPOINT_NAME)

        manifest = Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        )
        hand_edited = manifest.hand_edited(self._paths.app_dir)

        self._paths.options_dir.mkdir(parents=True, exist_ok=True)

        written: list[str] = []
        unchanged: list[str] = []
        for name, text in sorted(rendered.items()):
            if self._write_if_changed(self._paths.app_dir / name, text):
                written.append(name)
            else:
                unchanged.append(name)

        removed = self._prune(manifest, keep=set(rendered))
        entrypoint_written = self._write_if_changed(self._paths.entrypoint, entrypoint_text)

        manifest = manifest.with_versions(
            app_version=self._app_version,
            schema_version=model.schema.hyprland_version,
        ).with_modules(
            {name: ModuleRecord.of(text) for name, text in rendered.items()},
            ModuleRecord.of(entrypoint_text),
        )
        self._write_if_changed(self._paths.manifest, manifest.render())

        return WriteResult(
            written=tuple(written),
            unchanged=tuple(unchanged),
            removed=tuple(removed),
            entrypoint_written=entrypoint_written,
            hand_edited=hand_edited,
        )

    # --- internals ----------------------------------------------------------------------

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

    def _prune(self, manifest: Manifest, keep: set[str]) -> list[str]:
        """Delete Modules the model no longer produces.

        Scoped to `options/` and to files the Manifest says the app wrote: a Module the app
        never claimed is somebody else's, and deleting it would be exactly the "manager over
        your dots" behaviour the app refuses (ADR-0005).
        """
        removed: list[str] = []
        for name in sorted(manifest.modules):
            if name in keep or not name.startswith(f"{paths_module.OPTIONS_DIR}/"):
                continue
            path = self._paths.app_dir / name
            if path.is_file():
                path.unlink()
                removed.append(name)
        return removed
