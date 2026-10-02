"""Retirement (ADR-0012): stop emitting an Option a release removed, and keep its value.

The value of a removed Option exists in exactly one place: the app's own Module file, the
last bytes the app wrote for that Section. The model cannot hold it (a schema without the
Option refuses it, and the compositor answers `NoSuchOption`), and the first write after
the schema changes rewrites the Module without the key. So the sequence runs at session
start, **before the first write**, in this order:

    found = detect(manifest, schema, live)
    manifest = retire(manifest, found, capture(paths.app_dir, found))
    manifest, restored = restore(manifest, schema, live)
    writer.set_retired(model, manifest.retired)
    for each in restored: model.set(each.option.name, each.value)

`detect`, `retire` and `restore` are pure over the Manifest; `capture` is the one reader.
A rename runs through the same pass: the old name is detected and retired, and `restore`
hands its value straight to the Option that names it in `renamed_from` -- so a notice of
"retired this release" lists `found` less what `restored` took back under a new name.

One rule decides whether a name can be emitted (`emittable`), and both directions use it,
so an Option is never retired and restored by the same inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..importer.lua.sandbox import Consent, LuaUnavailable, evaluate
from ..schema import Schema
from ..schema.resolve import version_key
from ..schema.sources import lua_key_for
from .manifest import Manifest, RetiredValue


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
    does not.
    """
    retired_in = live.version if live is not None else schema.hyprland_version
    return tuple(
        Retirement(name, module, retired_in)
        for module, record in sorted(manifest.modules.items())
        for name in record.options
        if not emittable(name, schema, live)
    )


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
        each.name: RetiredValue(each.retired_in, values[each.name])
        for each in found
        if each.name in values
    }
    return manifest.with_retired({**manifest.retired, **kept})


_ABSENT = object()


def _config_tables(path: Path, timeout: float) -> tuple[Mapping[str, Any], ...]:
    if not path.is_file():
        return ()
    try:
        recording = evaluate(path, consent=Consent(evaluate=True), timeout=timeout)
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
