"""Type and widget inference over a `descriptions` record (research #3 §1.4, rules R1-R13).

`hyprctl -j descriptions` carries no type field. `jsonify()` prints a per-type template,
so the type has to be recovered from which *keys* a record has and what shape its default
is -- with the stub and the C++ source resolving the cases where two types print
identically.

This is engine code, not generator-only code, because ADR-0012 makes the app run these
same rules at runtime: on a Hyprland newer than any shipped schema, options absent from
the schema get minimal shape-inferred records and render in a *New in <version>* group.
Same rules, less input.
"""

from __future__ import annotations

import re
from typing import Any

from .sources import SourceFacts, lua_key_for
from .types import (
    CurationFlag,
    GeneratedOption,
    GetOptionKey,
    OptionType,
    Vec2Range,
    Widget,
)

INT32_MAX = 2**31 - 1

STRING_SENTINELS = ("[[EMPTY]]", "[[Auto]]")
"""`StringValue.hpp`'s "no value" defaults. They are not text the user ever typed."""

_ALTERNATIVES = re.compile(r"\[[^\]]+/[^\]]+\]")
"""A `[adaptive/flat/custom]` list inside a description -- the only in-band value list."""

_SMALL_INT_ENUM_SPAN = 6
"""A map-less int no wider than this is an enum in disguise, not a quantity."""


def infer_type(
    record: dict[str, Any], stub_type: str | None, ms_type: str | None
) -> OptionType:
    """Recover an Option's value type from record shape, stub type, and `MS<T>` class.

    The discriminator between Int and Float is the *presence of the `map` key*, never the
    JSON number type: 21 of the 34 Floats on 0.56.2 have whole-number defaults and print
    as `1`, not `1.0`. Typing on `isinstance(default, int)` misclassifies every one of
    them, and a Float rendered as an int spinner silently rounds the user's value.
    """
    default = record["default"]

    if isinstance(default, bool):
        return OptionType.BOOL
    if isinstance(default, list):
        return OptionType.VEC2
    if "map" in record:
        return OptionType.INT
    if isinstance(default, int | float):
        return OptionType.FLOAT
    if not isinstance(default, str):
        raise ValueError(f"{record['name']}: unrecognised default shape {default!r}")

    # A string default with bounds is the CssGap template ("t r b l" plus min/max).
    if "min" in record or "max" in record:
        return OptionType.CSS_GAPS

    # Four types print as a bare JSON string. The stub separates two of them...
    if stub_type == "string|HL.Gradient":
        return OptionType.GRADIENT
    if stub_type == "integer|string":
        return OptionType.FONT_WEIGHT

    # ...and only the C++ `MS<Color>` table separates Color from String. Without the
    # source the generator degrades to string, and the Overlay carries the type instead.
    if ms_type == "Color":
        return OptionType.COLOR
    return OptionType.STRING


def infer_widget(
    option_type: OptionType,
    record: dict[str, Any],
    choices: tuple[str, ...] | None,
) -> Widget:
    """Pick the control for a typed Option (rules R1-R13)."""
    match option_type:
        case OptionType.BOOL:
            return Widget.TOGGLE
        case OptionType.VEC2:
            return Widget.VEC2
        case OptionType.CSS_GAPS:
            return Widget.CSS_GAPS
        case OptionType.GRADIENT:
            return Widget.GRADIENT
        case OptionType.FONT_WEIGHT:
            return Widget.FONT_WEIGHT
        case OptionType.COLOR:
            return Widget.COLOR
        case OptionType.INT:
            if record.get("map"):
                return Widget.ENUM_MAP
            low, high = record.get("min"), record.get("max")
            # An INT_MAX ceiling is "no ceiling" wearing a number: a slider across two
            # billion steps is not a control, so those stay plain spin buttons.
            if low is None or high is None or high >= INT32_MAX:
                return Widget.FREE_INT
            return Widget.INT_RANGE
        case OptionType.FLOAT:
            low, high = record.get("min"), record.get("max")
            if low is None or high is None:
                return Widget.FREE_FLOAT
            return Widget.FLOAT_RANGE
        case OptionType.STRING:
            return Widget.ENUM_STRING if choices else Widget.STRING


def getoption_key(option_type: OptionType) -> GetOptionKey:
    """Which `hyprctl -j getoption` JSON key carries this type under the Lua engine."""
    match option_type:
        case OptionType.BOOL | OptionType.INT | OptionType.COLOR:
            return GetOptionKey.INT
        case OptionType.FLOAT:
            return GetOptionKey.FLOAT
        case OptionType.STRING:
            return GetOptionKey.STR
        case OptionType.VEC2:
            return GetOptionKey.VEC2
        case OptionType.GRADIENT:
            return GetOptionKey.GRADIENT
        case OptionType.CSS_GAPS:
            return GetOptionKey.CSS
        case OptionType.FONT_WEIGHT:
            return GetOptionKey.FONT_WEIGHT


def normalise_default(
    option_type: OptionType,
    raw: Any,
    declared_negative_one: bool,
) -> tuple[Any, bool]:
    """Return `(value, is_sentinel)` -- the typed default, or `None` when it is a sentinel.

    A sentinel is Hyprland saying "no value here": the row must show "Device default" or
    "Inherit", never the sentinel dressed as data. Prototype #8 measured this as the most
    damaging defect class -- `input:accel_profile` defaults to `[[EMPTY]]` and the raw
    generated page showed **`adaptive`, selected**, stating something false.

    Only the unambiguous classes are decided here. A numeric `-1` is *flagged* rather than
    resolved, because it means "unset" for `input:tablettool:pressure_range_min` and
    "random" for `misc:force_default_wallpaper` -- a distinction nothing machine-readable
    carries. `CurationFlag.NEEDS_NULLABLE` pushes that call to the Overlay.
    """
    if option_type is OptionType.STRING and raw in STRING_SENTINELS:
        return None, True

    # `MS<Color>` and `MS<Gradient>` declared with -1 mean "fall back to the related
    # colour". Colour prints "-1"; Gradient prints a plausible-looking "ffffffff 0deg"
    # that is not the default and never a value the user set -- source is the only tell.
    if option_type is OptionType.COLOR and (raw == "-1" or declared_negative_one):
        return None, True
    if option_type is OptionType.GRADIENT and declared_negative_one:
        return None, True

    return raw, False


def curation_flags(
    option_type: OptionType,
    widget: Widget,
    record: dict[str, Any],
    *,
    sentinel_default: bool,
    declared_negative_one: bool,
    vec2_range: Vec2Range | None,
) -> tuple[CurationFlag, ...]:
    """Every hand-curation demand this record raises.

    A flag marks a defect the generator can detect but not repair, because the missing
    fact lives in wiki prose or in a human's judgement. The Overlay completeness test
    turns each flag into a required Overlay field, which is what makes an uncurated option
    fail the build instead of shipping a wrong row (prototype #8 curated 126 options by
    hand and still missed two titles until a report script counted them).
    """
    flags: list[CurationFlag] = []

    # A numeric -1 is read off the record rather than the source, so a degraded run (no
    # C++ consulted) still raises the question -- and so a `-1.0` spelling cannot hide it.
    numeric_negative_one = option_type in (OptionType.INT, OptionType.FLOAT) and (
        record["default"] == -1 or declared_negative_one
    )
    if sentinel_default or numeric_negative_one:
        flags.append(CurationFlag.NEEDS_NULLABLE)

    # A map-less int spanning a handful of values is an enum nobody wrote a map for:
    # `input:touchpad:drag_lock` renders as a 0-2 spinner and nobody knows what "2" means.
    if option_type is OptionType.INT and not record.get("map"):
        low, high = record.get("min"), record.get("max")
        if low is not None and high is not None and high - low <= _SMALL_INT_ENUM_SPAN:
            flags.append(CurationFlag.NEEDS_LABELS)

    if widget is Widget.ENUM_STRING:
        flags.append(CurationFlag.NEEDS_KNOWN_VALUES)

    # Every plain-string row is a question the generator cannot answer: is this really
    # free text, or a font / monitor / file / regex / XKB picker wearing a text entry?
    # Research #3 §3.3 found 25 of them are pickers. Forcing an explicit Overlay `widget`
    # -- even one that just confirms `string` -- is the only way that question gets asked.
    if widget is Widget.STRING:
        flags.append(CurationFlag.NEEDS_WIDGET)

    # Vec2 bounds exist only as a `vec2Range` validator; a generator run without the
    # source has none, and an unbounded vec2 accepts values Hyprland will reject.
    if option_type is OptionType.VEC2 and vec2_range is None:
        flags.append(CurationFlag.NEEDS_RANGE)
    if widget is Widget.FREE_INT:
        flags.append(CurationFlag.NEEDS_RANGE)

    return tuple(flags)


def build_option(
    record: dict[str, Any],
    order: int,
    stub_types: dict[str, str],
    facts: SourceFacts,
) -> GeneratedOption:
    """Turn one `descriptions` record into a Generated schema record."""
    name = str(record["name"])
    lua_key = lua_key_for(name)
    stub_type = stub_types.get(lua_key)
    ms_type = facts.ms_type.get(name)
    declared_negative_one = name in facts.negative_one_defaults

    option_type = infer_type(record, stub_type, ms_type)

    # `strChoice` is authoritative; the `[a/b/c]` list in the description is the fallback
    # for a generator run without the source, and for options that never had a validator.
    choices = facts.str_choices.get(name)
    if choices is None and option_type is OptionType.STRING:
        found = _ALTERNATIVES.search(str(record.get("description", "")))
        if found is not None:
            inner = found.group(0)[1:-1]
            choices = tuple(part.strip() for part in inner.split("/") if part.strip())

    widget = infer_widget(option_type, record, choices)
    default, sentinel = normalise_default(option_type, record["default"], declared_negative_one)
    vec2_range = facts.vec2_ranges.get(name)

    # `descriptions` prints the enum map as an array of single-key objects in
    # unordered_map order (`ConfigValues.cpp` `opt<OptionMap>`), so it must be sorted by
    # value or the combo's item order changes between Hyprland builds.
    raw_map = record.get("map")
    enum_map: dict[str, int] | None = None
    if raw_map:
        pairs = [(key, int(value)) for entry in raw_map for key, value in entry.items()]
        enum_map = dict(sorted(pairs, key=lambda pair: pair[1]))

    return GeneratedOption(
        name=name,
        lua_key=lua_key,
        section=name.split(":", 1)[0],
        path=tuple(lua_key.split(".")),
        order=order,
        type=option_type,
        widget=widget,
        description=str(record.get("description", "")),
        default=default,
        default_raw=record["default"],
        sentinel_default=sentinel,
        getoption_key=getoption_key(option_type),
        min=record.get("min"),
        max=record.get("max"),
        map=enum_map,
        choices=choices,
        vec2_range=vec2_range,
        device_overridable=name in facts.device_overridable,
        refresh=facts.refresh.get(name, ()),
        curation_flags=curation_flags(
            option_type,
            widget,
            record,
            sentinel_default=sentinel,
            declared_negative_one=declared_negative_one,
            vec2_range=vec2_range,
        ),
    )
