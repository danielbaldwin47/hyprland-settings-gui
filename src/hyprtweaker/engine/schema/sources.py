"""Readers for the three inputs the Generated schema is built from.

The schema generator is the only caller (ADR-0011: the engine is what runs in tests, in
the schema generator, and in any future CLI). It lives here rather than in `tools/` so it
is covered by mypy and the unit tier like the rest of the engine.

The three sources, and why each is needed:

1. `hyprctl -j descriptions` -- names, descriptions, defaults, bounds, enum maps. Carries
   no type field, so type must be inferred from field *shape* (see `infer`).
2. `hl.meta.lua` -- the generated Lua stub. Its `HL.ConfigValueTypes` table separates
   Gradient, CssGap, FontWeight and Vec2 from plain strings and ints, which `descriptions`
   cannot; its `HL.ConfigKey` list is the writer's key vocabulary.
3. The Hyprland source at the matching tag -- `MS<T>` classes (the only place Color is
   distinguishable from String), `strChoice` value lists, `vec2Range` bounds, refresh bits,
   and the per-device-overridable name list. Optional: without it the generator degrades
   (Color falls back to string, vec2 ranges are empty) and the Overlay must fill the gaps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .types import Vec2Range

# --- 1. descriptions -------------------------------------------------------------------


def parse_descriptions(text: str) -> list[dict[str, Any]]:
    """Parse `hyprctl -j descriptions` output.

    Hyprland drops raw C string literals into this JSON without escaping
    (`HyprCtl.cpp`, `x->description()`), so a description containing a quote or a
    backslash would produce invalid JSON. None of the 353 do today; if one ever does,
    failing loudly here beats silently generating a short schema.
    """
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("descriptions JSON is not a list")

    records: list[dict[str, Any]] = []
    for entry in parsed:
        if not isinstance(entry, dict) or "name" not in entry:
            raise ValueError(f"malformed descriptions entry: {entry!r}")
        records.append(entry)

    if not records:
        raise ValueError("descriptions JSON is empty")
    return records


# --- 2. the Lua stub -------------------------------------------------------------------

_STUB_TYPE_BLOCK = re.compile(
    r"---@class HL\.ConfigValueTypes\n(.*?)\nlocal __HL_ConfigValueTypes",
    re.DOTALL,
)
_STUB_TYPE_FIELD = re.compile(r"---@field \['([^']+)'\] (\S+)")
_STUB_KEY_BLOCK = re.compile(r"---@alias HL\.ConfigKey\n((?:---\| \"[^\"]+\"\n)+)")
_STUB_KEY = re.compile(r"---\| \"([^\"]+)\"")


def parse_stub_types(text: str) -> dict[str, str]:
    """`HL.ConfigValueTypes` as `{lua_key: lua type expression}`.

    The type expression is what `meta/generateLuaStubs.py` mapped the `MS<T>` class to:
    `boolean`, `integer|boolean`, `number|boolean`, `string`, `string|HL.Gradient`,
    `HL.Vec2Like`, `integer|HL.CssGap`, `integer|string`.
    """
    block = _STUB_TYPE_BLOCK.search(text)
    if block is None:
        raise ValueError("no HL.ConfigValueTypes block in the stub")

    types = {key: value for key, value in _STUB_TYPE_FIELD.findall(block.group(1))}
    if not types:
        raise ValueError("HL.ConfigValueTypes block parsed but yielded no fields")
    return types


def parse_stub_keys(text: str) -> frozenset[str]:
    """The `HL.ConfigKey` alias list -- every dotted key the Lua engine accepts."""
    block = _STUB_KEY_BLOCK.search(text)
    if block is None:
        raise ValueError("no HL.ConfigKey alias block in the stub")

    keys = frozenset(_STUB_KEY.findall(block.group(1)))
    if not keys:
        raise ValueError("HL.ConfigKey block parsed but yielded no keys")
    return keys


def lua_key_for(name: str) -> str:
    """The stub's key rewrite: `:` -> `.` and `-` -> `_` (`generateLuaStubs.py`).

    This is what turns `input:touchpad:tap-to-click` into `input.touchpad.tap_to_click`.
    Only four keys actually contain a dash, which is exactly why a hand-written mapping
    would have been wrong for years before anyone noticed.
    """
    return name.replace(":", ".").replace("-", "_")


# --- 3. the Hyprland source ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceFacts:
    """What only the C++ source knows, keyed by colon-form option name."""

    ms_type: dict[str, str] = field(default_factory=dict)
    """`MS<T>` class per option: Bool, Int, Float, String, Color, Gradient, Vec2, ..."""

    str_choices: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """`strChoice({...})` validator value lists."""

    vec2_ranges: dict[str, Vec2Range] = field(default_factory=dict)
    """`vec2Range(minX, minY, maxX, maxY)` validator bounds."""

    refresh: dict[str, tuple[str, ...]] = field(default_factory=dict)
    """`.refresh = REFRESH_*` bits, informational (a reload refreshes everything anyway)."""

    negative_one_defaults: frozenset[str] = frozenset()
    """Options declared with a literal `-1` default -- the source-only sentinel class.

    `descriptions` cannot show these: `MS<Gradient>("decoration:shadow:color_inactive",
    ..., -1, ...)` prints as `"ffffffff 0deg"`, a colour that is not the default and not a
    value the user ever set. Without the source these two read as real gradients.
    """

    device_overridable: frozenset[str] = frozenset()
    """`CONFIG_DEVICE_VALUE_NAMES` -- the `input:*` options a `device {}` block can override."""

    @property
    def is_empty(self) -> bool:
        return not self.ms_type


def _split_top_level(text: str) -> list[str]:
    """Split a C++ argument list on commas that are not nested or inside a string.

    Written as a scanner rather than a regex because the naive `MS<...>\\("([^"]+)".*?
    strChoice` shape silently attaches a validator to whichever option happens to precede
    it: on 0.56.2 that pairs `general:layout` with `input:accel_profile`'s choices, and
    the resulting schema tells the user that `layout` accepts `flat`. Correct association
    requires knowing where each entry ends, which needs balanced-delimiter tracking.
    """
    parts: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    start = 0

    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1

    parts.append(text[start:])
    return [part.strip() for part in parts]


def _entry_bodies(text: str) -> list[tuple[str, str]]:
    """Every `MS<T>(...)` declaration as `(T, argument text)`, delimiters balanced."""
    entries: list[tuple[str, str]] = []

    for match in re.finditer(r"MS<([A-Za-z0-9_]+)>\(", text):
        ms_type = match.group(1)
        index = match.end()
        depth = 1
        in_string = False
        escaped = False

        while index < len(text) and depth:
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
            elif char == '"':
                in_string = True
            elif char in "([{":
                depth += 1
            elif char in ")]}":
                depth -= 1
            index += 1

        if depth:
            raise ValueError(f"unterminated MS<{ms_type}>( declaration in ConfigValues.cpp")
        entries.append((ms_type, text[match.end() : index - 1]))

    return entries


_STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
_VEC2_RANGE = re.compile(r"vec2Range\(([^)]*)\)")
_STR_CHOICE = re.compile(r"strChoice\(\{([^}]*)\}\)")
_REFRESH_BIT = re.compile(r"REFRESH_[A-Z_]+")
_DEVICE_NAMES = re.compile(
    r"CONFIG_DEVICE_VALUE_NAMES\s*=\s*\{(.*?)\};",
    re.DOTALL,
)


def _is_negative_one(expression: str) -> bool:
    """True for every C++ spelling of a literal -1 default.

    The same sentinel is written `-1`, `-1.0` and `-1.f` across ConfigValues.cpp, so a
    string comparison against `"-1"` silently misses the two Float cases
    (`input:tablettool:pressure_range_min` / `_max`, both declared `-1.0`).
    """
    text = expression.strip().rstrip("fF")
    try:
        return float(text) == -1.0
    except ValueError:
        return False


def parse_source(config_values_cpp: str, config_values_hpp: str) -> SourceFacts:
    """Extract every source-only fact from `ConfigValues.cpp` / `.hpp` at a release tag."""
    ms_type: dict[str, str] = {}
    str_choices: dict[str, tuple[str, ...]] = {}
    vec2_ranges: dict[str, Vec2Range] = {}
    refresh: dict[str, tuple[str, ...]] = {}
    negative_one: set[str] = set()

    for declared_type, body in _entry_bodies(config_values_cpp):
        args = _split_top_level(body)
        if len(args) < 3:
            continue

        name_literal = _STRING_LITERAL.match(args[0])
        if name_literal is None:
            continue
        name = name_literal.group(1)

        ms_type[name] = declared_type

        if _is_negative_one(args[2]):
            negative_one.add(name)

        options = args[3] if len(args) > 3 else ""

        choice = _STR_CHOICE.search(options)
        if choice is not None:
            str_choices[name] = tuple(_STRING_LITERAL.findall(choice.group(1)))

        vec2 = _VEC2_RANGE.search(options)
        if vec2 is not None:
            bounds = [float(part.strip()) for part in vec2.group(1).split(",")]
            if len(bounds) == 4:
                vec2_ranges[name] = Vec2Range(*bounds)

        bits = tuple(sorted(set(_REFRESH_BIT.findall(options))))
        if bits:
            refresh[name] = bits

    device_block = _DEVICE_NAMES.search(config_values_hpp)
    device_names = (
        frozenset(_STRING_LITERAL.findall(device_block.group(1)))
        if device_block is not None
        else frozenset()
    )

    if not ms_type:
        raise ValueError("no MS<> declarations found -- is this ConfigValues.cpp?")

    return SourceFacts(
        ms_type=ms_type,
        str_choices=str_choices,
        vec2_ranges=vec2_ranges,
        refresh=refresh,
        negative_one_defaults=frozenset(negative_one),
        device_overridable=device_names,
    )
