"""The drift scan: which of the app's own settings something later in the load order beats.

ADR-0005: "after each reload the app compares `get_config`/`getoption` against its model and
badges diverging options as *overridden in user.lua*". A transaction's Read-back does that
for the keys it wrote; this does it for every key the app's Modules set, at launch and after
a foreign reload (ADR-0016 §Surfacing: "the full re-read + drift scan"), so a Row shows the
"Overridden" pill before the user has touched it (#191).

**The reference is the app's own Module, not the model.** The model may hold an edit not
yet written, and comparing that against `getoption` would badge a key that is only waiting
for its write. The Module holds what the app last wrote, and its Manifest hash proves it is
the app's write; the same Modules are what the model takes its values from at launch and
keeps them from after a reload (`written_values`, `verified_options`, finding 11 of the
#153 review), so an override is never copied into the model as the user's own value. A
Module whose bytes no longer match is a hand edit (ADR-0016 ownership class 2) and says
nothing about what the app asked for, so it is skipped. The live side is `getoption` only
-- `user.lua` is never run (ADR-0018).

`reference` evaluates Lua, one subprocess per Module, so it blocks: the caller runs it off
the main loop.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..importer.lua.sandbox import config_values
from ..ipc import CommandClient, MalformedReply, NoSuchOption
from ..model.values import parse_lua
from ..schema import ResolvedOption, Schema
from ..schema.sources import lua_key_for
from ..state import Manifest, ModuleRecord, content_hash
from ..writer import is_option_module
from .result import Mismatch
from .transaction import compare

Expected = tuple[ResolvedOption, Any]
"""One Option and the model value the app's own Module sets it to."""


def verified_options(app_dir: Path, manifest: Manifest) -> frozenset[str]:
    """Every Option an options Module sets whose bytes still hash to the Manifest's record.

    The model rendered those bytes, so it already holds what they say: a re-read must keep
    the model's value for each, whatever the compositor answers, since a later file
    (`user.lua`, a theming tool's Bridge module) may be what answers. No Lua is run.
    """
    return frozenset(
        name for _path, record in _verified(app_dir, manifest) for name in record.options
    )


def written_values(
    app_dir: Path, schema: Schema, manifest: Manifest, *, timeout: float = 5.0
) -> dict[str, Any]:
    """What the app's own options Modules set, as model values, by Option name.

    Only Modules whose bytes hash to the Manifest's record, and only the Options that record
    names -- restart-flagged ones included. What the model takes at launch for those
    Options instead of the live values. Raises `LuaUnavailable` without an interpreter.
    """
    raw: dict[str, Any] = {}
    for path, record in _verified(app_dir, manifest):
        keys = {lua_key_for(name): name for name in record.options}
        for key, value in config_values(path, keys, timeout=timeout).items():
            raw[keys[key]] = value
    values: dict[str, Any] = {}
    for option in schema.options:
        if option.name not in raw:
            continue
        try:
            values[option.name] = parse_lua(option, raw[option.name])
        except (ValueError, TypeError):
            continue
    return values


def reference(
    app_dir: Path, schema: Schema, manifest: Manifest, *, timeout: float = 5.0
) -> tuple[Expected, ...]:
    """What the app's own options Modules set, as model values, in declaration order.

    `written_values` without the restart-flagged Options, left out for the reason Read-back
    leaves them out: the running compositor keeps the value it started with, so a
    difference is not an override (ADR-0010 §Restart-flagged). Raises `LuaUnavailable`
    without an interpreter, which means no scan, never "no overrides".
    """
    values = written_values(app_dir, schema, manifest, timeout=timeout)
    return tuple(
        (option, values[option.name])
        for option in schema.options
        if option.name in values and option.restart is None
    )


def _verified(app_dir: Path, manifest: Manifest) -> list[tuple[Path, ModuleRecord]]:
    found = []
    for relpath, record in manifest.modules.items():
        if not is_option_module(relpath):
            continue
        path = app_dir / relpath
        try:
            if content_hash(path.read_bytes()) != record.sha256:
                continue
        except OSError:
            continue
        found.append((path, record))
    return found


async def scan(client: CommandClient, expected: Sequence[Expected]) -> tuple[Mismatch, ...]:
    """Each Option in `expected` whose live value is not the one the app wrote.

    By the Read-back's own rules (`transaction.compare`): agreement is not drift, and a key
    the compositor does not know or answers unreadably about is no evidence either way.
    Other IPC errors propagate: a compositor that stopped answering has said nothing.
    """
    found: list[Mismatch] = []
    for option, value in expected:
        try:
            mismatch = compare(option, value, await client.getoption(option.name))
        except (NoSuchOption, MalformedReply):
            continue
        if mismatch is not None:
            found.append(mismatch)
    return tuple(found)
