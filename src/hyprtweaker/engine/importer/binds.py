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

from ..model.entities import Bind, BindDevice, BindOptions, DispatcherCall, Submap, Unbind
from .dispatchers import translate_dispatcher
from .loss import LossClass, LossCode, LossReport

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

#: Legacy key spellings this Hyprland's Lua side rejects, and what they should be. Kept
#: deliberately small: it holds only names the sources name, not guesses about xkb.
_KEY_RENAMES: dict[str, str] = {
    "enter": "Return",
    "esc": "Escape",
}

#: Keys passed through untouched -- "special syms" that are not xkb names at all.
_SPECIAL_PREFIXES: tuple[str, ...] = ("mouse:", "switch:", "code:")
_SPECIAL_EXACT: frozenset[str] = frozenset(
    {"mouse_up", "mouse_down", "mouse_left", "mouse_right", "catchall"}
)


@dataclass(slots=True)
class _Ctx:
    origin: str
    report: LossReport
    source: str

    def note(
        self,
        code: LossCode,
        message: str,
        *,
        replacement: str = "",
        loss_class: LossClass | None = None,
    ) -> None:
        self.report.add(
            code,
            message,
            origin=self.origin,
            source=self.source,
            replacement=replacement,
            loss_class=loss_class,
        )


def canonical_mods(field: str) -> list[str]:
    """The modifier tokens hyprlang would have matched, spelled the way Lua needs.

    Substring matching is reproduced exactly, including its quirk that any separator (or
    none) works -- that is what makes `SUPERSHIFT` two modifiers rather than a keysym.
    """
    upper = field.upper()
    return [name for name, aliases in _MOD_ALIASES if any(a in upper for a in aliases)]


def canonical_key(key: str, ctx: _Ctx | None = None) -> str:
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
    return stripped


def _key_string(mods_field: str, key_field: str, ctx: _Ctx, *, multikey: bool) -> str:
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


def _read_flags(flags: str, ctx: _Ctx) -> tuple[BindOptions, bool, bool, bool, bool]:
    """Split the flag letters into option fields and the four that change parsing."""
    options: dict[str, object] = {}
    has_description = has_device = mouse = multikey = False
    for letter in flags.lower():
        field = FLAG_OPTIONS.get(letter)
        if field is not None:
            options[field] = True
        elif letter == "d":
            has_description = True
        elif letter == "k":
            has_device = True
        elif letter == "m":
            mouse = True
        elif letter == "s":
            multikey = True
        else:
            ctx.note(LossCode.DEAD_DISPATCHER, f"unknown bind flag {letter!r}")
    if options.get("click") or options.get("drag"):
        # Both imply release on the legacy side; Lua's `click` sets it itself, but making
        # it explicit keeps the entity readable.
        options["release"] = True
    return BindOptions(**options), has_description, has_device, mouse, multikey  # type: ignore[arg-type]


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
) -> Bind | None:
    """One `bind[flags] = ...` line as a Bind, or None when nothing survives.

    Returns None only when the dispatcher itself could not be translated -- the Loss report
    already carries the reason, and emitting a bind with no action would be worse than
    emitting none.
    """
    source = f"bind{flags} = {value}"
    ctx = _Ctx(origin=origin, report=report, source=source)
    options, has_description, has_device, mouse, multikey = _read_flags(flags, ctx)

    fields = value.split(",")
    if len(fields) < 2:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind needs at least a modifier and a key")
        return None

    mods_field, key_field = fields[0], fields[1]
    rest = fields[2:]

    if has_description:
        if not rest:
            ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bindd has no description field")
            return None
        options = _with(options, description=rest[0].strip())
        rest = rest[1:]
        if "," in options.description:  # pragma: no cover -- split above forbids it
            ctx.note(LossCode.DESCRIPTION_COMMAS, "description kept verbatim")
    if has_device:
        if not rest:
            ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bindk has no device field")
            return None
        options = _with(options, device=_device_field(rest[0]))
        rest = rest[1:]

    keys = _key_string(mods_field, key_field, ctx, multikey=multikey)
    if not keys:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind has neither modifiers nor a key")
        return None

    if mouse:
        # `bindm` has no dispatcher field: the third value *is* the mouse action, and Lua
        # expresses the whole thing as a drag/resize dispatcher (L5).
        action = ",".join(rest).strip()
        ctx.note(
            LossCode.MOUSE_BIND,
            "mouse bind expressed as a drag/resize dispatcher; Lua has no mouse flag",
            replacement=action,
        )
        call = translate_dispatcher(
            "mouse", action, origin=origin, report=report, source=source
        )
        return _bind(keys, call, options, submap, origin) if call else None

    if not rest:
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "bind has no dispatcher")
        return None
    name, args = rest[0].strip(), ",".join(rest[1:])
    call = translate_dispatcher(name, args, origin=origin, report=report, source=source)
    if call is None:
        return None
    return _bind(keys, call, options, submap, origin)


def _bind(
    keys: str,
    call: DispatcherCall,
    options: BindOptions,
    submap: str | None,
    origin: str,
) -> Bind:
    return Bind(keys=keys, dispatcher=call, options=options, submap=submap, origin=origin)


def _with(options: BindOptions, **changes: object) -> BindOptions:
    return replace(options, **changes)  # type: ignore[arg-type]


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
    ctx = _Ctx(origin=origin, report=report, source=source)
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
