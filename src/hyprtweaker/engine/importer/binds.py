"""`bind*` / `unbind` / `submap` keywords -> Bind, Unbind and Submap entities.

Three separate translations happen here, and each is lossy in its own way:

- **Flags to options.** The letters after `bind` are a set, in any order, and most map
  one-to-one onto an `HL.BindOptions` field under a different name (`e` is `repeating`,
  `p` is `dont_inhibit`). Two do not: `d` and `k` *consume an extra comma field* from the
  value, so flags have to be read before the value can be split at all.
- **Modifiers to a key string.** hyprlang matched modifier names as case-insensitive
  substrings anywhere in the field, so `SUPER_SHIFT`, `SUPERSHIFT` and `super shift` all
  meant the same mask. Lua splits on `+` and demands exact upper-case tokens, so the mask
  has to be re-derived and re-spelled (L1) -- passing the original through yields
  `Unknown keysym: "SUPER_SHIFT"`.
- **Keys.** A bare number above 9 was a keycode to hyprlang and is a keysym lookup to Lua,
  so it becomes `code:N` (L2).

`unbind` is the one that cannot be made exact: hyprlang removed a bind by modifier *mask*,
Lua removes it by comparing key *strings*. Canonicalising both sides identically is what
makes the common case work, and the case it cannot cover is reported (L6).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from ..model.entities import Bind, BindDevice, BindOptions, Submap, Unbind
from .dispatchers import ScriptLookup, translate_dispatcher
from .keysyms import known_keysym
from .loss import LossCode, LossContext, LossReport

__all__ = [
    "FLAG_OPTIONS",
    "canonical_key",
    "canonical_mods",
    "map_bind",
    "map_submap",
    "map_unbind",
]

#: Modifier bits as hyprlang matched them: canonical Lua name -> the substrings that meant
#: it. Order matters only for reporting; the mask itself is a set.
_MOD_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SHIFT", ("SHIFT",)),
    ("CAPS", ("CAPS",)),
    ("CTRL", ("CTRL", "CONTROL")),
    ("ALT", ("ALT", "MOD1")),
    ("MOD2", ("MOD2",)),
    ("MOD3", ("MOD3",)),
    ("SUPER", ("SUPER", "WIN", "LOGO", "MOD4", "META")),
    ("MOD5", ("MOD5",)),
)

#: Flag letter -> the `BindOptions` field it sets. `d`, `k`, `m` and `s` are absent: they
#: change how the *value* is read, and are handled in `map_bind`.
FLAG_OPTIONS: dict[str, str] = {
    "l": "locked",
    "r": "release",
    "e": "repeating",
    "n": "non_consuming",
    "a": "auto_consuming",
    "t": "transparent",
    "i": "ignore_mods",
    "o": "long_press",
    "u": "submap_universal",
    "p": "dont_inhibit",
    "c": "click",
    "g": "drag",
    "x": "allow_input_capture",
}

#: Common spellings that are not xkb keysym names, and the names they meant.
#:
#: Every entry is checked both ways by `test_importer_mapping.py`: the key must be one xkb
#: rejects and the value one it accepts, so this cannot drift into a table of guesses.
#: Anything not listed here is not renamed -- it is validated and *reported*, because
#: inventing a target for an unknown name would be a worse failure than naming it.
_KEY_RENAMES: dict[str, str] = {
    "enter": "Return",
    "esc": "Escape",
}

#: Keys passed through untouched -- "special syms" that are not xkb names at all.
_SPECIAL_PREFIXES: tuple[str, ...] = ("mouse:", "switch:", "code:")
_SPECIAL_EXACT: frozenset[str] = frozenset(
    {"mouse_up", "mouse_down", "mouse_left", "mouse_right", "catchall"}
)

CATCHALL = "catchall"


@dataclass(slots=True)
class _Flags:
    """The four flag letters that change how the *value* is read, not what it means.

    Named rather than returned as a tuple of bare booleans, because `d` and `k` each
    consume a comma field and the order they consume in is load-bearing -- a caller
    unpacking four anonymous flags is one swap away from reading a device list as a
    description.
    """

    has_description: bool = False
    has_device: bool = False
    mouse: bool = False
    multikey: bool = False


def canonical_mods(field: str) -> list[str]:
    """The modifier tokens hyprlang would have matched, spelled the way Lua needs.

    Substring matching is reproduced exactly, including its quirk that any separator (or
    none) works -- that is what makes `SUPERSHIFT` two modifiers rather than a keysym.
    """
    upper = field.upper()
    return [name for name, aliases in _MOD_ALIASES if any(a in upper for a in aliases)]


def canonical_key(key: str, ctx: LossContext | None = None) -> str:
    """One legacy key field as the token Lua expects."""
    stripped = key.strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if lowered in _SPECIAL_EXACT or lowered.startswith(_SPECIAL_PREFIXES):
        return stripped
    if stripped.isdigit() and int(stripped) > 9:
        # hyprlang read a bare number above 9 as a keycode; Lua would look it up as a
        # keysym name and fail (L2).
        if ctx is not None:
            ctx.note(
                LossCode.BARE_KEYCODE,
                f"bare keycode {stripped} rewritten as code:{stripped}",
                replacement=f"code:{stripped}",
            )
        return f"code:{stripped}"
    renamed = _KEY_RENAMES.get(lowered)
    if renamed is not None:
        if ctx is not None:
            ctx.note(
                LossCode.UNKNOWN_KEYSYM,
                f"{stripped!r} is not a keysym name; Lua needs {renamed!r}",
                replacement=renamed,
            )
        return renamed
    if ctx is not None and known_keysym(stripped) is False:
        # hyprlang resolved key names at press time and silently never matched an unknown
        # one; Lua resolves at bind time and refuses the config. A bind that quietly did
        # nothing becomes a config that will not load, so it has to be said out loud.
        ctx.note(
            LossCode.UNKNOWN_KEYSYM,
            f"{stripped!r} is not a key name xkb knows, so this bind never fired in "
            "hyprlang -- and Lua rejects the whole config rather than ignoring it",
        )
    return stripped


def _key_string(mods_field: str, key_field: str, ctx: LossContext, *, multikey: bool) -> str:
    """Build `hl.bind`'s single key string from the two legacy fields."""
    if multikey:
        # `binds` joined every token with `&` and made modifiers keysyms too (L4).
        tokens = [
            canonical_key(token, ctx)
            for part in (mods_field, key_field)
            for token in part.split("&")
            if token.strip()
        ]
        ctx.note(
            LossCode.MULTIKEY_BIND,
            "multi-key bind approximated by joining every key with '+'; Lua's matcher "
            "requires the whole set to be held, which is close but not the legacy matcher",
            replacement=" + ".join(tokens),
        )
        return " + ".join(tokens)

    mods = canonical_mods(mods_field)
    key = canonical_key(key_field, ctx)
    if key.lower() == CATCHALL and mods:
        # `catchall` swallows every key in the submap; the modifiers on the line were
        # already meaningless to hyprlang and are dropped rather than carried into a key
        # string Lua would read as a modifier requirement (ADR-0009, Needs review).
        ctx.note(
            LossCode.UNKNOWN_KEYSYM,
            f"catchall ignores modifiers, so {' + '.join(mods)} is dropped; the bind still "
            "catches every key in its submap",
            replacement=CATCHALL,
        )
        return CATCHALL
    if mods_field.strip() and not mods:
        ctx.note(
            LossCode.MODS_SPELLING,
            f"modifier field {mods_field.strip()!r} matches no modifier -- hyprlang "
            "treated this as an error too",
        )
    elif mods and _respelled(mods_field, mods):
        ctx.note(
            LossCode.MODS_SPELLING,
            f"modifiers {mods_field.strip()!r} respelled for Lua's strict '+' tokens",
            replacement=" + ".join(mods),
        )
    parts = [*mods, key] if key else list(mods)
    return " + ".join(parts)


def _respelled(field: str, mods: list[str]) -> bool:
    """Whether the canonical spelling actually differs from what the user wrote."""
    return field.strip().upper().replace(" ", "") != "+".join(mods).replace(" ", "")


def _read_flags(flags: str, ctx: LossContext) -> tuple[BindOptions, _Flags]:
    """Split the flag letters into option fields and the four that change parsing."""
    # `Any` rather than `bool`: the keys are `BindOptions` field names, whose types differ
    # per field, and narrowing here would only move the mismatch to the constructor call.
    options: dict[str, Any] = {}
    parsing = _Flags()
    for letter in flags.lower():
        field = FLAG_OPTIONS.get(letter)
        if field is not None:
            options[field] = True
        elif letter == "d":
            parsing.has_description = True
        elif letter == "k":
            parsing.has_device = True
        elif letter == "m":
            parsing.mouse = True
        elif letter == "s":
            parsing.multikey = True
        else:
            ctx.note(LossCode.UNSUPPORTED_KEYWORD, f"unknown bind flag {letter!r}")
    if options.get("click") or options.get("drag"):
        # Both imply release on the legacy side; Lua's `click` sets it itself, but making
        # it explicit keeps the entity readable.
        options["release"] = True
    return BindOptions(**options), parsing


def _device_field(text: str) -> BindDevice:
    """`[!]dev1 dev2 ...` -- a leading `!` is Lua's `inclusive = false`."""
    stripped = text.strip()
    inclusive = not stripped.startswith("!")
    names = tuple(name for name in stripped.lstrip("!").split() if name)
    return BindDevice(inclusive=inclusive, names=names)


def map_bind(
    flags: str,
    value: str,
    *,
    origin: str,
    report: LossReport,
    submap: str | None = None,
    lookup: ScriptLookup | None = None,
) -> Bind | None:
    """One `bind[flags] = ...` line as a Bind, or None when nothing survives.

    Returns None only when the dispatcher itself could not be translated -- the Loss report
    already carries the reason, and emitting a bind with no action would be worse than
    emitting none.
    """
    source = f"bind{flags} = {value}"
    ctx = LossContext(report=report, origin=origin, source=source)
    options, parsing = _read_flags(flags, ctx)

    fields = value.split(",")
    if len(fields) < 2:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind needs at least a modifier and a key")
        return None

    mods_field, key_field = fields[0], fields[1]
    rest = fields[2:]

    if parsing.has_description:
        if not rest:
            ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bindd has no description field")
            return None
        options = replace(options, description=rest[0].strip())
        rest = rest[1:]
    if parsing.has_device:
        if not rest:
            ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bindk has no device field")
            return None
        options = replace(options, device=_device_field(rest[0]))
        rest = rest[1:]

    keys = _key_string(mods_field, key_field, ctx, multikey=parsing.multikey)
    if not keys:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind has neither modifiers nor a key")
        return None

    if parsing.mouse:
        # `bindm` has no dispatcher field: the third value *is* the mouse action, and Lua
        # expresses the whole thing as a drag/resize dispatcher (L5).
        action = ",".join(rest).strip()
        ctx.note(
            LossCode.MOUSE_BIND,
            "mouse bind expressed as a drag/resize dispatcher; Lua has no mouse flag",
            replacement=action,
        )
        name, args = "mouse", action
    elif not rest:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind has no dispatcher")
        return None
    else:
        name, args = rest[0].strip(), ",".join(rest[1:])

    call = translate_dispatcher(
        name, args, origin=origin, report=report, source=source, lookup=lookup
    )
    if call is None:
        return None
    return Bind(keys=keys, dispatcher=call, options=options, submap=submap, origin=origin)


def map_unbind(
    value: str,
    *,
    origin: str,
    report: LossReport,
    submap: str | None = None,
) -> Unbind:
    """`unbind = MODS, key` or `unbind = all`.

    The emitted key string is canonicalised the same way a Bind's is, which is the only
    thing that makes Lua's string comparison find the bind hyprlang's mask would have (L6).
    """
    source = f"unbind = {value}"
    ctx = LossContext(report=report, origin=origin, source=source)
    if value.strip().lower() == "all":
        return Unbind(keys="all", all=True, submap=submap, origin=origin)
    fields = value.split(",")
    mods_field = fields[0] if fields else ""
    key_field = fields[1] if len(fields) > 1 else ""
    keys = _key_string(mods_field, key_field, ctx, multikey=False)
    ctx.note(
        LossCode.UNBIND_BY_STRING,
        "unbind matched by modifier mask in hyprlang but matches the key string in Lua; "
        "check it still names an existing bind",
        replacement=f'hl.unbind("{keys}")',
    )
    return Unbind(keys=keys, submap=submap, origin=origin)


def map_submap(value: str, *, origin: str) -> Submap | None:
    """`submap = name[, reset_target]`. `submap = reset` closes the block instead."""
    fields = [part.strip() for part in value.split(",")]
    name = fields[0] if fields else ""
    if not name or name.lower() == "reset":
        return None
    return Submap(name=name, reset_target=fields[1] if len(fields) > 1 else "", origin=origin)
