"""The canonical `binds.lua` Module: rendering it, and reading it back (ADR-0007).

Binds are the one Module the app both writes *and* reads. Options are verified over IPC,
but `hyprctl binds` is structurally blind to `code:N` binds -- `parseKeyString` files
keycodes under `sMkKeys` and the IPC reply carries `key:"", keycode:0` -- so reconstructing
bind state from the compositor would silently lose every layout-independent number-row
bind in the corpus. ADR-0007's answer is that the *file* is the interface: emit a canonical
form, and re-read that same file when it changes underneath us.

That makes hand-editability a property of this module rather than a feature elsewhere. The
user principle behind it (grilling on #12) is "no proprietary state" -- what the app writes
must be something a person can edit in a text editor and have the app agree with.

**Read-back goes through the importer's sandbox, not a parser here.** A hand-edited
`binds.lua` is arbitrary Lua -- a loop over a table of binds is a perfectly reasonable
thing for a user to write -- and a regex that only understands the app's own output would
report those binds as missing and then overwrite them. Evaluating under the recording stub
(#62) understands anything Lua does, and shares its bind mapping with the Lua importer so
the written form and the read form cannot drift.

**Order is meaning.** `hl.bind` appends; duplicates are legal and all fire in file order
(`lua-api-surface.md` §4). So identity is list position, and this renderer never sorts.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..model.entities import Bind, BindOptions, DispatcherCall, EntitySet, Submap, Unbind
from ..model.values import lua_string
from ..paths import BINDS_MODULE
from .lua import GENERATED_BANNER, INDENT, table_key


@dataclass(frozen=True, slots=True)
class ReadOnlyBind:
    """A construct in `binds.lua` the model cannot represent, kept rather than dropped.

    ADR-0007: constructs the parser cannot represent are surfaced as read-only rows and
    offered adopt-into-`legacy.lua`, "never silently dropped or overwritten". A function
    action is the common one -- it belongs to `user.lua` and has no editable form here.
    """

    keys: str
    reason: str
    origin: str = ""


@dataclass(frozen=True, slots=True)
class ParsedBinds:
    """What one read of `binds.lua` found."""

    binds: tuple[Bind, ...] = ()
    unbinds: tuple[Unbind, ...] = ()
    submaps: tuple[Submap, ...] = ()
    read_only: tuple[ReadOnlyBind, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# --- rendering -------------------------------------------------------------------------


def lua_value(value: Any) -> str:
    """Any Python value a bind carries, as a Lua literal.

    `DispatcherCall.__str__` exists but is a *debug* spelling built on `repr`, which prints
    Python's `True`, not Lua's `true` -- emitting it would produce a Module that fails to
    load. So emission goes through here instead, and the two are kept visibly separate.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, int | float):
        return repr(value) if isinstance(value, float) else str(value)
    if value is None:
        return "nil"
    if isinstance(value, Mapping):
        inner = ", ".join(
            f"{table_key(str(key))}{lua_value(item)}" for key, item in value.items()
        )
        return f"{{ {inner} }}" if inner else "{}"
    if isinstance(value, Sequence):
        inner = ", ".join(lua_value(item) for item in value)
        return f"{{ {inner} }}" if inner else "{}"
    return lua_string(str(value))


def render_dispatcher(call: DispatcherCall) -> str:
    """One `hl.dsp.*` call expression.

    The two call shapes are not interchangeable: `hl.dsp.submap("resize")` takes a bare
    string and `hl.dsp.focus{workspace="e+1"}` takes a table, so `DispatcherCall` keeps
    them apart and so does this.
    """
    if call.positional:
        return f"hl.dsp.{call.path}({', '.join(lua_value(arg) for arg in call.positional)})"
    if call.args:
        inner = ", ".join(f"{table_key(str(k))}{lua_value(v)}" for k, v in call.args.items())
        return f"hl.dsp.{call.path}{{ {inner} }}"
    return f"hl.dsp.{call.path}()"


def render_options(options: BindOptions) -> str:
    """The trailing options table, or `""` when every flag is at its default.

    `mouse` can never appear here because `BindOptions` has no such field, and that is the
    point: v0.56.2's `hlBind` never reads a `mouse` key, so emitting one would look like a
    setting and do nothing (ADR-0007, L5). A legacy `bindm` becomes a drag/resize
    *dispatcher* in the Importer instead.
    """
    table = options.as_table()
    if not table:
        return ""
    inner = ", ".join(
        f"{table_key(str(key))}{lua_value(value)}" for key, value in table.items()
    )
    return f"{{ {inner} }}"


DISABLED_PREFIX = "-- disabled: "
"""How a disabled bind is spelled in the file (#66).

A comment rather than an absence, because deleting would renumber every bind after it --
identity is position (ADR-0007) -- and a comment keeps the file the interface: the line is
readable in a text editor, and removing the prefix by hand re-enables the bind. Read-back
revives these lines before evaluation and re-marks them disabled by line number.
"""


def render_bind(bind: Bind, *, depth: int = 0) -> str | None:
    """One `hl.bind(...)` line, or `None` for a bind with no emittable action.

    A `dispatcher` of `None` means a function-valued action, which belongs to `user.lua`
    and renders read-only in the GUI (ADR-0007). There is no Lua the app could write for
    it, and inventing something would be worse than leaving it where it already works.

    A disabled bind renders as the same line behind `DISABLED_PREFIX`: canonical enough
    to read back, inert enough to never fire.
    """
    if bind.dispatcher is None:
        return None
    parts = [lua_string(bind.keys), render_dispatcher(bind.dispatcher)]
    if rendered := render_options(bind.options):
        parts.append(rendered)
    line = f"hl.bind({', '.join(parts)})"
    if not bind.enabled:
        line = f"{DISABLED_PREFIX}{line}"
    return f"{INDENT * depth}{line}"


def render_binds_module(entities: EntitySet, *, app_version: str) -> str | None:
    """The whole `binds.lua`, or `None` when there is nothing to write.

    `None` rather than an empty file so the writer prunes the Module instead of leaving one
    that requires cleanly and does nothing -- absence is how this config model spells "no
    binds", the same as for Options.

    Root binds come first, then one `hl.define_submap` block per Submap, because a submap's
    binds only exist inside its callback. Within each group the model's order is kept
    exactly: duplicates are legal and fire in the order written.
    """
    lines: list[str] = []

    for unbind in entities.unbinds:
        if unbind.submap is None and unbind.keys:
            lines.append(f"hl.unbind({lua_string(unbind.keys)})")

    for bind in entities.binds:
        if bind.submap is None and (rendered := render_bind(bind)) is not None:
            lines.append(rendered)

    for block in _submap_blocks(entities):
        if lines:
            lines.append("")
        lines.extend(block)

    if not lines:
        return None

    header = (
        f"{GENERATED_BANNER.format(version=app_version)}\n"
        f"-- Keybinds. Order is meaning: duplicates are legal and fire in file order.\n"
        f"-- Edits here are read back into the app, not overwritten.\n"
    )
    return header + "\n" + "\n".join(lines) + "\n"


def _submap_blocks(entities: EntitySet) -> list[list[str]]:
    """One `hl.define_submap` block per Submap.

    Submaps named by a bind but never declared still get a block: dropping the block would
    drop the binds, and a bind whose submap was only ever implied is exactly what an
    imported config looks like.

    A *declared* submap emits even when empty (#66): a submap the user just created has no
    binds yet, and pruning its block would silently delete it on the very next write. Only
    an implied-and-empty submap is skipped, because there is nothing to say about it.
    """
    declared = {submap.name: submap for submap in entities.submaps}
    order: list[str] = list(declared)
    for bind in entities.binds:
        if bind.submap is not None and bind.submap not in order:
            order.append(bind.submap)

    blocks: list[list[str]] = []
    for name in order:
        submap = declared.get(name) or Submap(name=name)
        body = [
            rendered
            for bind in entities.binds
            if bind.submap == name and (rendered := render_bind(bind, depth=1)) is not None
        ]
        body.extend(
            f"{INDENT}hl.unbind({lua_string(unbind.keys)})"
            for unbind in entities.unbinds
            if unbind.submap == name and unbind.keys
        )
        if not body and name not in declared:
            continue
        opener = ", ".join(
            filter(
                None,
                [
                    lua_string(name),
                    lua_string(submap.reset_target) if submap.reset_target else "",
                ],
            )
        )
        blocks.append([f"hl.define_submap({opener}, function()", *body, "end)"])
    return blocks


# --- reading back ----------------------------------------------------------------------


def parse_binds_module(source: str | Path, *, timeout: float = 5.0) -> ParsedBinds:
    """Read a `binds.lua` back into Entities.

    Takes text or a path. Evaluation happens in the importer's sandbox with consent
    granted, which is defensible only because of what this file is: a Module the app itself
    wrote into its own App dir, already required by the Entrypoint on every single reload.
    Reading it costs nothing the user is not already paying. Consent is emphatically *not*
    implied for a foreign config -- that path goes through the Migration wizard and asks.
    """
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source
    revived, disabled_lines = _revive_disabled(text)

    # Always evaluated from a scratch copy, even when given a real path: the disabled
    # pre-pass edits the text, and the one thing this must never do is write the edit back
    # over the user's file. The copy keeps the module's basename so origins still read as
    # `binds.lua:N`, and the pre-pass preserves line numbers exactly, so `N` is the line
    # in the real file.
    with tempfile.TemporaryDirectory(prefix="hyprtweaker-binds-") as scratch:
        path = Path(scratch) / BINDS_MODULE
        path.write_text(revived, encoding="utf-8")
        return _parse_path(path, timeout=timeout, disabled_lines=disabled_lines)


def _revive_disabled(text: str) -> tuple[str, frozenset[int]]:
    """Uncomment every `DISABLED_PREFIX` line, remembering which 1-based lines they were.

    Line-for-line, never inserting or removing one, because the line numbers are how the
    evaluated calls are matched back to their disabledness. Only the canonical spelling is
    revived -- `DISABLED_PREFIX` followed by an `hl.` call -- so an ordinary hand-written
    comment stays a comment.
    """
    lines = text.split("\n")
    disabled: set[int] = set()
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(DISABLED_PREFIX):
            rest = stripped[len(DISABLED_PREFIX) :]
            if rest.startswith("hl."):
                indent = line[: len(line) - len(stripped)]
                lines[number - 1] = f"{indent}{rest}"
                disabled.add(number)
    return "\n".join(lines), frozenset(disabled)


def _parse_path(
    path: Path, *, timeout: float, disabled_lines: frozenset[int] = frozenset()
) -> ParsedBinds:
    from ..importer.lua.mapping import (
        bind_options_from_value,
        dispatcher_from_value,
        positional_args,
        table_fields,
    )
    from ..importer.lua.sandbox import Consent, LuaUnavailable, evaluate

    try:
        recording = evaluate(path, consent=Consent(evaluate=True), timeout=timeout)
    except LuaUnavailable as error:
        # No interpreter is an installation problem, not a config one. The caller's fallback
        # is the model it already holds, so this must not look like "the file has no binds".
        return ParsedBinds(errors=(str(error),))

    binds: list[Bind] = []
    unbinds: list[Unbind] = []
    submaps: list[Submap] = []
    read_only: list[ReadOnlyBind] = []

    for call in recording.calls:
        if call.name == "bind":
            args = positional_args(call)
            keys = args[0] if args and isinstance(args[0], str) else ""
            action = args[1] if len(args) > 1 else None
            dispatcher = dispatcher_from_value(action)
            if dispatcher is None:
                read_only.append(
                    ReadOnlyBind(
                        keys=keys,
                        reason="a function action, which belongs to user.lua",
                        origin=call.origin,
                    )
                )
                continue
            binds.append(
                Bind(
                    keys=keys,
                    dispatcher=dispatcher,
                    options=bind_options_from_value(args[2] if len(args) > 2 else None),
                    submap=call.submap,
                    enabled=call.line not in disabled_lines,
                    origin=call.origin,
                )
            )
        elif call.name == "unbind":
            args = positional_args(call)
            keys = args[0] if args and isinstance(args[0], str) else ""
            unbinds.append(
                Unbind(keys=keys, all=not keys, submap=call.submap, origin=call.origin)
            )
        elif call.name == "define_submap":
            table = table_fields(call.args)
            name = table.get("name")
            if isinstance(name, str) and name:
                submaps.append(
                    Submap(
                        name=name,
                        reset_target=str(table.get("reset") or ""),
                        origin=call.origin,
                    )
                )

    return ParsedBinds(
        binds=tuple(binds),
        unbinds=tuple(unbinds),
        submaps=tuple(submaps),
        read_only=tuple(read_only),
        errors=tuple(recording.errors),
    )


__all__ = [
    "ParsedBinds",
    "ReadOnlyBind",
    "lua_value",
    "parse_binds_module",
    "render_bind",
    "render_binds_module",
    "render_dispatcher",
    "render_options",
]
