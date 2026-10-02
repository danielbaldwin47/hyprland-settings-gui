"""Retirement (ADR-0012): stop emitting an Option a release removed, and keep its value.

The value of a removed Option exists in exactly one place: the app's own Module file, the
last bytes the app wrote for that Section. The model cannot hold it (a schema without the
Option refuses it, and the compositor answers `NoSuchOption`), and the first write after
the schema changes rewrites the Module without the key. So the sequence runs at session
start, **before the first write**, in this order:

    found = detect(manifest, schema, live)
    kept = retire(manifest, found, capture(paths.app_dir, found))
    remaining, restored = restore(kept, schema, live)
    writer.set_retired(model, kept.retired)           # every value safe before any write
    for each in restored: model.set(each.option.name, each.value)
    ... one write of the model ...
    writer.set_retired(model, landed(<manifest on disk>, restored).retired)

`Session._retire_and_restore` runs it. `detect`, `retire`, `restore` and `landed` are pure
over the Manifest; `capture` is the one reader. `unannounced(remaining)` is the notice, and
`UnkeptNotice.of` names what `capture` could not read, so nothing is dropped unannounced.
A rename runs through the same pass: the old name is detected and retired, and `restore`
hands its value straight to the Option that names it in `renamed_from` -- so a notice of
"retired this release" lists `found` less what `restored` took back under a new name.

One rule decides whether a name can be emitted (`emittable`), and both directions use it,
so an Option is never retired and restored by the same inputs. `restore` is the stricter
of the two offline (see its docstring): putting a refused key back costs a config error,
leaving a value kept costs nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..importer.lua.sandbox import Consent, LuaUnavailable, evaluate
from ..model.values import parse_lua
from ..schema import ResolvedOption, Schema, is_plugin_option
from ..schema.resolve import version_key
from ..schema.sources import lua_key_for
from .manifest import Manifest, RetiredValue, RetireReason


class LiveNames(Protocol):
    """What retirement needs of the live snapshot: a parsable version and the option names.

    A Protocol rather than the Session's snapshot type, so the engine takes any snapshot
    and the tests need no sockets."""

    @property
    def version(self) -> str: ...

    @property
    def names(self) -> frozenset[str]: ...


@dataclass(frozen=True, slots=True)
class Retirement:
    """An Option the user set that this session can no longer emit."""

    name: str
    """The colon-form name the Manifest records it under."""

    module: str
    """The App-dir-relative Module that holds its value, as the Manifest keys it."""

    retired_in: str
    """The running compositor's version, or the loaded schema's when there is no snapshot."""

    reason: RetireReason = RetireReason.REMOVED
    """Why it cannot be emitted; a quiet reason is kept and restored but never announced."""


def emittable(name: str, schema: Schema, live: LiveNames | None) -> bool:
    """Whether the app may write `name`: the schema holds it and nothing live contradicts it.

    A running compositor contradicts the schema only when it is at or past the schema's
    release: an older one lacking a name has not added it yet, which is `Not in this
    Hyprland`, not retirement. With no snapshot the schema is the only authority, as it is
    for every Option the app writes offline.
    """
    if name not in schema:
        return False
    if live is None or name in live.names:
        return True
    return version_key(live.version) < version_key(schema.hyprland_version)


def detect(
    manifest: Manifest, schema: Schema, live: LiveNames | None
) -> tuple[Retirement, ...]:
    """Every Option the app wrote last time that it can no longer emit.

    Reads the Manifest's raw `ModuleRecord.options`, never a schema-filtered view of them:
    filtering is exactly what would hide a name the schema dropped. Two triggers, one rule:
    (a) the loaded schema lacks the name -- the app's update shipped a schema without it, or
    a downgrade; (b) the schema holds it but a compositor at or past the schema's release
    does not. `reason_for` says which of them the user is told about.
    """
    retired_in = live.version if live is not None else schema.hyprland_version
    return tuple(
        Retirement(name, module, retired_in, reason_for(name, schema, live))
        for module, record in sorted(manifest.modules.items())
        for name in record.options
        if not emittable(name, schema, live)
    )


def reason_for(name: str, schema: Schema, live: LiveNames | None) -> RetireReason:
    """Why `name`, which `emittable` refuses, is retired: the one place a reason is chosen.

    Only the running compositor's own word that it lacks the name makes it `REMOVED`. A
    name it still describes is missing from the loaded schema alone: the startup read
    missed a Hyprland newer than every shipped schema, so the supplement that would have
    held the name is not loaded (#214). The value is kept all the same, since the first
    write drops the key, and the next start that reads the compositor restores it.

    A plugin's setting the compositor does not describe belongs to a plugin that is not
    loaded (#175), or to one this start could not ask about: kept quietly either way, and
    back on the first start that finds the plugin loaded.
    """
    if live is not None and name in live.names:
        return RetireReason.NOT_IN_SCHEMA
    if is_plugin_option(name):
        return RetireReason.PLUGIN_NOT_LOADED
    return RetireReason.REMOVED


def capture(
    app_dir: Path, found: Sequence[Retirement], *, timeout: float = 5.0
) -> dict[str, Any]:
    """Each retired Option's value, read from the Module the app last wrote it into.

    Evaluated in the importer's sandbox with consent granted, on the same footing as
    `binds.lua` (`writer/binds.py`): the file is the app's own output, already required by
    the Entrypoint on every reload. The values come back JSON-native, as the Lua importer
    reads any `hl.config` table.

    A name the Module no longer holds -- the file was deleted or hand-edited, or there is
    no Lua interpreter -- is left out rather than guessed: there is nothing left to keep.
    """
    modules: dict[str, list[str]] = {}
    for each in found:
        modules.setdefault(each.module, []).append(each.name)

    values: dict[str, Any] = {}
    for module, names in modules.items():
        tables = _config_tables(app_dir / module, timeout)
        for name in names:
            for table in tables:  # a later `hl.config` wins, as it does in Lua
                value = _lookup(table, lua_key_for(name).split("."))
                if value is not _ABSENT:
                    values[name] = value
    return values


def retire(
    manifest: Manifest, found: Sequence[Retirement], values: Mapping[str, Any]
) -> Manifest:
    """The Manifest keeping each found Option's captured value under its retired-in version.

    An Option `capture` could not read is not recorded: a kept value with nothing in it
    would claim a restore it cannot deliver. A name retired again replaces its old entry.
    """
    kept = {
        each.name: RetiredValue(each.retired_in, values[each.name], each.reason)
        for each in found
        if each.name in values
    }
    return manifest.with_retired({**manifest.retired, **kept})


@dataclass(frozen=True, slots=True)
class Restoration:
    """A kept value going back into the model, under the Option that now takes it."""

    retired_name: str
    """The name it was kept under -- the old name, when this is a rename."""

    option: ResolvedOption
    value: Any
    """The model value, parsed against `option`: hand it to `ConfigModel.set`."""

    @property
    def renamed(self) -> bool:
        """A rename migration, which ADR-0012 announces with its own Info notice."""
        return self.option.name != self.retired_name


def restore(
    manifest: Manifest, schema: Schema, live: LiveNames | None
) -> tuple[Manifest, tuple[Restoration, ...]]:
    """Every kept value whose Option is back, and the Manifest no longer keeping them.

    Back means one of two things (ADR-0012): the same name is emittable again (a downgrade,
    or a later release re-adding it), or an emittable Option names it in `renamed_from`.
    The value is parsed against that Option with `parse_lua`, so restoring needs no schema
    entry for the old name; a value the Option will not take stays kept, never guessed.

    Offline, a same-name return needs the schema to be at least as new as the release that
    retired it: an older schema still describing the Option is no evidence the user's
    Hyprland takes it back, and emitting a key it last refused is a config error.
    """
    restored: list[Restoration] = []
    for name, entry in sorted(manifest.retired.items()):
        option = _taker(name, entry, schema, live)
        if option is None:
            continue
        try:
            value = parse_lua(option, entry.value)
        except (ValueError, TypeError):
            continue
        restored.append(Restoration(name, option, value))

    taken = {each.retired_name for each in restored}
    remaining = {name: e for name, e in manifest.retired.items() if name not in taken}
    return manifest.with_retired(remaining), tuple(restored)


def _taker(
    name: str, entry: RetiredValue, schema: Schema, live: LiveNames | None
) -> ResolvedOption | None:
    """The Option that takes a kept value back now, if any.

    Stricter than `emittable` with a snapshot: an older Hyprland lacking the name is `Not
    in this Hyprland`, which keeps writes going, but putting a kept value back into a
    compositor without the key is a config error. So a running compositor must name it.
    """

    def takes(taker: str) -> bool:
        if live is not None and taker not in live.names:
            return False
        return emittable(taker, schema, live)

    option = schema.get(name)
    if option is not None:
        returned = live is not None or version_key(entry.retired_in) <= version_key(
            schema.hyprland_version
        )
        return option if returned and takes(name) else None
    renamed = next((each for each in schema if each.renamed_from == name), None)
    return renamed if renamed is not None and takes(renamed.name) else None


def landed(manifest: Manifest, restored: Sequence[Restoration]) -> Manifest:
    """The Manifest no longer keeping the restored values a write has put in a Module.

    `restore` hands the Session a Manifest without them, but saving that before the write
    would leave a value nowhere if the app stopped in between -- not kept, and not in any
    Module. So the Manifest keeps them until a Module records the Option that took them,
    and a write that skipped it (a hand-edited Module) leaves them kept, to restore again.
    """
    written = {name for record in manifest.modules.values() for name in record.options}
    done = {each.retired_name for each in restored if each.option.name in written}
    return manifest.with_retired(
        {name: entry for name, entry in manifest.retired.items() if name not in done}
    )


@dataclass(frozen=True, slots=True)
class RetiredNotice:
    """One release's one-time notice: the Options it removed whose values the app keeps."""

    release: str
    names: tuple[str, ...]
    """Colon-form names, sorted. A name with no schema entry left has no label but this."""


@dataclass(frozen=True, slots=True)
class RenamedNotice:
    """ADR-0012's Info notice for a rename: "renames migrate silently with an Info notice"."""

    renames: tuple[tuple[str, str], ...]
    """`(old name, new name)` per value that moved, sorted by old name."""

    @classmethod
    def of(cls, restored: Sequence[Restoration]) -> RenamedNotice | None:
        """The notice for this start's renames, or `None` when nothing moved names."""
        moved = tuple(
            (each.retired_name, each.option.name) for each in restored if each.renamed
        )
        return cls(tuple(sorted(moved))) if moved else None


@dataclass(frozen=True, slots=True)
class UnkeptNotice:
    """This start's notice for Options a release removed whose values `capture` could not
    read (no Lua, or a Module deleted or hand-edited): the write drops them, and the user
    is told so rather than finding them gone. Nothing records it: once the keys are gone,
    the next start detects nothing."""

    release: str
    names: tuple[str, ...]
    """Colon-form names, sorted."""

    @classmethod
    def of(cls, found: Sequence[Retirement], values: Mapping[str, Any]) -> UnkeptNotice | None:
        """The notice for the found names `values` lacks, or `None` when every one was
        read. One start detects under one release, so one notice covers them."""
        lost = sorted(each.name for each in found if each.name not in values)
        return cls(found[0].retired_in, tuple(lost)) if lost else None


def unannounced(manifest: Manifest) -> tuple[RetiredNotice, ...]:
    """A notice per release the Manifest keeps values for and has not recorded as seen.

    Read from `retired`, not from this start's `detect`: once the first write has dropped
    the keys, `detect` finds nothing, and a notice the user closed the app before seeing
    would never come back. Oldest release first. A value kept for a quiet reason is not
    news: its Option was not removed, so it raises no notice at all.
    """
    by_release: dict[str, list[str]] = {}
    for name, entry in manifest.retired.items():
        if entry.reason.announced and entry.retired_in not in manifest.retired_notices:
            by_release.setdefault(entry.retired_in, []).append(name)
    return tuple(
        RetiredNotice(release, tuple(sorted(names)))
        for release, names in sorted(by_release.items(), key=lambda item: version_key(item[0]))
    )


_ABSENT = object()


def _config_tables(path: Path, timeout: float) -> tuple[Mapping[str, Any], ...]:
    if not path.is_file():
        return ()
    try:
        recording = evaluate(
            path, consent=Consent(evaluate=True), timeout=timeout, assume_plugins_loaded=True
        )
    except LuaUnavailable:
        return ()
    return tuple(
        call.args
        for call in recording.calls
        if call.name == "config" and isinstance(call.args, Mapping)
    )


def _lookup(table: Mapping[str, Any], path: Sequence[str]) -> Any:
    node: Any = table
    for step in path:
        if not isinstance(node, Mapping) or step not in node:
            return _ABSENT
        node = node[step]
    return node
