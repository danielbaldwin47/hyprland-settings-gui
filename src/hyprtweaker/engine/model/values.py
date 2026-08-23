"""The Value representations: one Python object per Option type, three ways to say it.

An Option's value exists in three worlds at once (ADR-0005), and 19 of the 353 options
spell it differently in all three:

1. **display text** -- what `hyprctl -j descriptions` prints and what a `.conf` line holds
   (`"5 5 5 5"`, `"ff444444 0deg"`). This is the importer's input and the schema's default.
2. **Lua literal** -- what the writer emits. Gradients and css-gaps *must* be tables here:
   `LuaConfigGradient.cpp` will not read the display text back, so emitting `toString()`
   output produces a config Hyprland rejects (ADR-0005; research `lua-api-surface.md` §0).
3. **`getoption` parse** -- what comes back over IPC, keyed by type, packed differently
   again (a colour arrives as a 32-bit integer, not a string).

Every type below carries all three: `parse()` reads form 1, `__str__` writes form 1,
`lua()` writes form 2, `from_getoption()` reads form 3. Keeping them on the value rather
than in the writer is deliberate -- `{ colors = ..., angle = ... }` is Hyprland's grammar,
not a formatting preference, so there is exactly one place to be wrong about it.

The scalar types (bool, int, float, string) are plain Python objects; only the five that
Hyprland spells non-obviously get a class.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Protocol, runtime_checkable

from ..schema import GetOptionKey, OptionType, ResolvedOption

_HEX8 = re.compile(r"^(?:0x)?([0-9a-fA-F]{8})$")
_RGBA_HEX = re.compile(r"^rgba\(\s*([0-9a-fA-F]{8})\s*\)$")
_RGB_HEX = re.compile(r"^rgb\(\s*([0-9a-fA-F]{6})\s*\)$")
_RGBA_TUPLE = re.compile(r"^rgba\(([^)]*)\)$")
_RGB_TUPLE = re.compile(r"^rgb\(([^)]*)\)$")
_CSS_HEX = re.compile(r"^#([0-9a-fA-F]{3,8})$")
_ANGLE = re.compile(r"^(-?[0-9]*\.?[0-9]+)deg$")


def _clamp_byte(value: float) -> int:
    return max(0, min(255, round(value)))


@dataclass(frozen=True, slots=True, order=True)
class Color:
    """A colour as Hyprland's packed 32-bit ARGB word.

    Storing the word rather than a string is what makes the three representations agree:
    `descriptions` prints bare `aarrggbb`, CSS and `rgba()` put alpha last, and `getoption`
    hands back the integer. One canonical form in, one canonical form out per world.
    """

    argb: int

    def __post_init__(self) -> None:
        if not 0 <= self.argb <= 0xFFFFFFFF:
            raise ValueError(f"colour out of 32-bit range: {self.argb}")

    @classmethod
    def parse(cls, raw: object) -> Color:
        """Read every colour spelling Hyprland accepts (`ParserUtils.cpp:23-131`).

        Note the two conventions in play: a bare or `0x`-prefixed hex8 is **ARGB** (what
        `descriptions` prints), while `rgba()` and CSS `#rrggbbaa` put alpha **last**.
        Confusing them turns an opaque colour transparent, which is why the caller never
        gets to hand-roll this.
        """
        if isinstance(raw, Color):
            return raw
        if isinstance(raw, bool):
            raise ValueError(f"not a colour: {raw!r}")
        if isinstance(raw, int):
            return cls(raw)

        if not isinstance(raw, str):
            raise ValueError(f"not a colour: {raw!r}")
        value = raw.strip()

        if (match := _HEX8.match(value)) is not None:
            return cls(int(match.group(1), 16))

        if (match := _RGBA_HEX.match(value)) is not None:
            rgba = int(match.group(1), 16)
            return cls(((rgba & 0xFF) << 24) | (rgba >> 8))

        if (match := _RGB_HEX.match(value)) is not None:
            return cls(0xFF000000 | int(match.group(1), 16))

        if (match := _CSS_HEX.match(value)) is not None:
            return cls._from_css(match.group(1))

        if (match := _RGBA_TUPLE.match(value)) is not None:
            return cls._from_channels(match.group(1), with_alpha=True)

        if (match := _RGB_TUPLE.match(value)) is not None:
            return cls._from_channels(match.group(1), with_alpha=False)

        if value.isdigit():
            return cls(int(value))

        raise ValueError(f"not a colour: {raw!r}")

    @classmethod
    def _from_css(cls, digits: str) -> Color:
        if len(digits) in (3, 4):
            digits = "".join(digit * 2 for digit in digits)
        if len(digits) == 6:
            return cls(0xFF000000 | int(digits, 16))
        if len(digits) == 8:
            rgba = int(digits, 16)
            return cls(((rgba & 0xFF) << 24) | (rgba >> 8))
        raise ValueError(f"not a CSS colour: #{digits}")

    @classmethod
    def _from_channels(cls, body: str, *, with_alpha: bool) -> Color:
        parts = [part.strip() for part in body.split(",")]
        expected = 4 if with_alpha else 3
        if len(parts) != expected:
            raise ValueError(f"expected {expected} colour channels, got {len(parts)}")

        red, green, blue = (_clamp_byte(float(part)) for part in parts[:3])
        # `rgba(r,g,b,a)` takes alpha as a 0..1 float, unlike the 0..255 channels.
        alpha = _clamp_byte(float(parts[3]) * 255) if with_alpha else 255
        return cls((alpha << 24) | (red << 16) | (green << 8) | blue)

    @classmethod
    def from_getoption(cls, payload: object) -> Color:
        """`getoption` reports a colour under the `int` key, already packed ARGB."""
        if isinstance(payload, bool) or not isinstance(payload, int):
            raise ValueError(f"getoption colour is not an integer: {payload!r}")
        return cls(payload & 0xFFFFFFFF)

    def __str__(self) -> str:
        """The `descriptions` spelling: eight bare hex digits, alpha first."""
        return f"{self.argb:08x}"

    def lua(self) -> str:
        """The `rgba(rrggbbaa)` string form -- alpha last, as everywhere outside ARGB.

        Not the `0xaarrggbb` numeric literal the upstream example config uses for
        `decoration.shadow.color`: that one is an `MS<Color>`, and `LuaConfigColor` takes a
        number, but a gradient stop goes through `LuaConfigGradient`, which reads its
        `colors` entries as strings. One spelling that works in both positions beats two
        spellings that each work in one, and it is what every rice in `tests/corpus`
        writes by hand.
        """
        return lua_string(f"rgba({self.rgba:08x})")

    @property
    def rgba(self) -> int:
        """The same colour with alpha moved to the low byte -- the `rgba()`/CSS order."""
        return ((self.argb & 0x00FFFFFF) << 8) | (self.argb >> 24)


@dataclass(frozen=True, slots=True)
class Gradient:
    """One or more colours plus an angle in degrees.

    The type ADR-0005 singles out: `descriptions` prints `"ff444444 0deg"`, and handing
    that string back to `hl.config` is a config error -- the Lua parser wants
    `{ colors = { ... }, angle = ... }` and nothing else.
    """

    colors: tuple[Color, ...]
    angle: float = 0.0

    def __post_init__(self) -> None:
        if not self.colors:
            raise ValueError("a gradient needs at least one colour")

    @classmethod
    def parse(cls, raw: object) -> Gradient:
        """Read the `descriptions`/hyprlang form: colours space-separated, `<n>deg` last."""
        if isinstance(raw, Gradient):
            return raw
        if isinstance(raw, Color):
            return cls((raw,))
        if isinstance(raw, str):
            tokens = raw.split()
            if not tokens:
                raise ValueError("empty gradient")

            angle = 0.0
            if (match := _ANGLE.match(tokens[-1])) is not None:
                angle = float(match.group(1))
                tokens = tokens[:-1]
            if not tokens:
                raise ValueError(f"gradient has an angle but no colours: {raw!r}")

            return cls(tuple(Color.parse(token) for token in tokens), angle)

        raise ValueError(f"not a gradient: {raw!r}")

    @classmethod
    def from_getoption(cls, payload: object) -> Gradient:
        """Accept every shape the `gradient` key has been seen to carry.

        Research #3 swept `getoption` under hyprlang, where gradients come back as a
        `custom` *string*; prototype #8 caught the Lua engine answering with the structured
        form instead. A reader that handles only one of the two breaks on an engine swap,
        so this handles both rather than betting on the compositor.
        """
        if isinstance(payload, str):
            return cls.parse(payload)
        if isinstance(payload, dict):
            colors = payload.get("colors", ())
            if not isinstance(colors, list | tuple):
                raise ValueError(f"getoption gradient colours are not a list: {colors!r}")
            angle = payload.get("angle", 0.0)
            if isinstance(angle, bool) or not isinstance(angle, int | float):
                raise ValueError(f"getoption gradient angle is not a number: {angle!r}")
            return cls(tuple(Color.parse(color) for color in colors), float(angle))
        raise ValueError(f"not a getoption gradient: {payload!r}")

    def __str__(self) -> str:
        return " ".join([*(str(color) for color in self.colors), f"{_number(self.angle)}deg"])

    def lua(self) -> str:
        colors = ", ".join(color.lua() for color in self.colors)
        return f"{{ colors = {{ {colors} }}, angle = {_number(self.angle)} }}"


@dataclass(frozen=True, slots=True)
class CssGaps:
    """Four gap sides, entered CSS-style (1, 2, 3 or 4 numbers).

    Like `Gradient`, this must reach Lua as a table: `LuaConfigCssGap.cpp` accepts a plain
    integer or `{ top, right, bottom, left }`, never the `"5 5 5 5"` text `descriptions`
    prints. The writer always emits the four-key table -- one shape is easier to read in a
    diff than a value that changes syntax when its sides happen to match.
    """

    top: int
    right: int
    bottom: int
    left: int

    @classmethod
    def uniform(cls, gap: int) -> CssGaps:
        return cls(gap, gap, gap, gap)

    @classmethod
    def parse(cls, raw: object) -> CssGaps:
        """CSS shorthand: 1 value = all sides, 2 = vertical/horizontal, 3 = t/h/b, 4 = TRBL."""
        if isinstance(raw, CssGaps):
            return raw
        if isinstance(raw, bool):
            raise ValueError(f"not css gaps: {raw!r}")
        if isinstance(raw, int):
            return cls.uniform(raw)

        if isinstance(raw, str):
            parts = [int(float(part)) for part in raw.replace(",", " ").split()]
        elif isinstance(raw, list | tuple):
            parts = [int(part) for part in raw]
        else:
            raise ValueError(f"not css gaps: {raw!r}")

        match parts:
            case [all_sides]:
                return cls.uniform(all_sides)
            case [vertical, horizontal]:
                return cls(vertical, horizontal, vertical, horizontal)
            case [top, horizontal, bottom]:
                return cls(top, horizontal, bottom, horizontal)
            case [top, right, bottom, left]:
                return cls(top, right, bottom, left)
            case _:
                raise ValueError(f"css gaps take 1-4 values, got {len(parts)}: {raw!r}")

    @classmethod
    def from_getoption(cls, payload: object) -> CssGaps:
        """The `css` key, which `push()` fills as `{top, right, bottom, left}`."""
        if isinstance(payload, str):
            return cls.parse(payload)
        if isinstance(payload, dict):
            try:
                return cls(*(int(payload[side]) for side in ("top", "right", "bottom", "left")))
            except KeyError as error:
                raise ValueError(f"getoption css gaps missing a side: {payload!r}") from error
        if isinstance(payload, list | tuple):
            return cls.parse(payload)
        raise ValueError(f"not getoption css gaps: {payload!r}")

    def __str__(self) -> str:
        return f"{self.top} {self.right} {self.bottom} {self.left}"

    def lua(self) -> str:
        return (
            f"{{ top = {self.top}, right = {self.right}, "
            f"bottom = {self.bottom}, left = {self.left} }}"
        )


@dataclass(frozen=True, slots=True)
class Vec2:
    """A two-number vector.

    Emitted as the array `{ x, y }` and never as `{ x = ..., y = ... }`: the stub's
    `HL.Vec2Like` alias advertises the named form but `LuaConfigVec2.cpp:10-45` only ever
    reads indices 1 and 2, so the named form silently yields `(0, 0)`.
    """

    x: float
    y: float

    @classmethod
    def parse(cls, raw: object) -> Vec2:
        if isinstance(raw, Vec2):
            return raw
        if isinstance(raw, list | tuple):
            if len(raw) != 2:
                raise ValueError(f"a vec2 takes 2 numbers, got {len(raw)}: {raw!r}")
            return cls(float(raw[0]), float(raw[1]))
        if isinstance(raw, str):
            parts = raw.replace(",", " ").split()
            if len(parts) != 2:
                raise ValueError(f"a vec2 takes 2 numbers, got {len(parts)}: {raw!r}")
            return cls(float(parts[0]), float(parts[1]))
        raise ValueError(f"not a vec2: {raw!r}")

    @classmethod
    def from_getoption(cls, payload: object) -> Vec2:
        """The `vec2` key, pushed with both array indices and `x`/`y` names."""
        if isinstance(payload, dict):
            if "x" in payload and "y" in payload:
                return cls(float(payload["x"]), float(payload["y"]))
            raise ValueError(f"getoption vec2 has no x/y: {payload!r}")
        return cls.parse(payload)

    def __str__(self) -> str:
        return f"{_number(self.x)} {_number(self.y)}"

    def lua(self) -> str:
        return f"{{ {_number(self.x)}, {_number(self.y)} }}"


@dataclass(frozen=True, slots=True)
class FontWeight:
    """A numeric weight or one of the preset names (`"bold"`, `"medium"`, ...).

    Kept as a class rather than a bare `int | str` so the writer cannot mistake the preset
    name for an arbitrary string option and the display form stays single-valued.
    """

    weight: int | str

    def __post_init__(self) -> None:
        if isinstance(self.weight, int) and self.weight < 0:
            raise ValueError(f"font weight cannot be negative: {self.weight}")
        if isinstance(self.weight, str) and not self.weight:
            raise ValueError("font weight name is empty")

    @classmethod
    def parse(cls, raw: object) -> FontWeight:
        if isinstance(raw, FontWeight):
            return raw
        if isinstance(raw, bool):
            raise ValueError(f"not a font weight: {raw!r}")
        if isinstance(raw, int):
            return cls(raw)
        if isinstance(raw, str):
            value = raw.strip()
            return cls(int(value)) if value.isdigit() else cls(value)
        raise ValueError(f"not a font weight: {raw!r}")

    @classmethod
    def from_getoption(cls, payload: object) -> FontWeight:
        return cls.parse(payload)

    def __str__(self) -> str:
        return str(self.weight)

    def lua(self) -> str:
        return str(self.weight) if isinstance(self.weight, int) else lua_string(self.weight)


_LUA_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\a": "\\a",
    "\b": "\\b",
    "\f": "\\f",
    "\v": "\\v",
    "\0": "\\0",
}


def lua_string(text: str) -> str:
    """A double-quoted Lua string literal, every control character escaped.

    Lives here rather than in the writer because `FontWeight.lua()` needs it too, and a
    second copy of an escaping table is a second place for an injection bug to hide.
    """
    return '"' + "".join(_LUA_ESCAPES.get(char, char) for char in text) + '"'


def _number(value: float) -> str:
    """Shortest exact spelling of a number, with whole floats printed without `.0`.

    `descriptions` prints `0` for a whole float and the writer follows: Lua's numeric
    parsers take an integer literal for a float value, and `angle = 45` reads better in a
    generated file than `angle = 45.0`. Determinism is what matters -- one input, one
    spelling, so the content hash only moves when the value does.
    """
    if isinstance(value, int) or (math.isfinite(value) and float(value).is_integer()):
        return str(int(value))
    return repr(float(value))


ComplexValue = Color | Gradient | CssGaps | Vec2 | FontWeight
"""The five Option types Hyprland spells non-obviously enough to need their own class."""

COMPLEX_TYPES: dict[OptionType, type[ComplexValue]] = {
    OptionType.COLOR: Color,
    OptionType.GRADIENT: Gradient,
    OptionType.CSS_GAPS: CssGaps,
    OptionType.VEC2: Vec2,
    OptionType.FONT_WEIGHT: FontWeight,
}
"""One table, not three, so a sixth complex type is one line rather than a hunt.

Each class is the whole answer for its type: `parse` for display text, `from_getoption` for
an IPC reply, `lua` and `__str__` for the two ways back out.
"""


def parse_value(option_type: OptionType, raw: Any) -> Any:
    """Display text (or an already-typed value) -> the model's Python value.

    This is the funnel every value enters the model through: schema defaults, importer
    output and UI edits all land here, so an option can only ever hold a value of its own
    type. Rejecting early is the point -- a string in an `int` option is a config error at
    the next reload, and finding it at set time names the option instead.
    """
    if (complex_type := COMPLEX_TYPES.get(option_type)) is not None:
        return complex_type.parse(raw)

    match option_type:
        case OptionType.BOOL:
            return _parse_bool(raw)
        case OptionType.INT:
            return _parse_int(raw)
        case OptionType.FLOAT:
            if isinstance(raw, bool) or not isinstance(raw, int | float | str):
                raise ValueError(f"not a number: {raw!r}")
            return float(raw)
        case _:
            if isinstance(raw, str):
                return raw
            raise ValueError(f"not a string: {raw!r}")


def _parse_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    raise ValueError(f"not a boolean: {raw!r}")


def _parse_int(raw: Any) -> int:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float) and raw.is_integer():
        return int(raw)
    if isinstance(raw, str):
        text = raw.strip()
        # An enum-mapped int is often written as its own name in a `.conf`; the caller
        # resolves that against `ResolvedOption.map` before getting here, so anything
        # still non-numeric at this point really is wrong.
        return int(text, 0) if text.lower().startswith(("0x", "-0x")) else int(float(text))
    raise ValueError(f"not an integer: {raw!r}")


def _scalar_spelling(value: Any) -> str | None:
    """The spelling booleans and numbers share between display text and Lua source.

    Both worlds write `true` and `45` identically, so the two representations only diverge
    below this point -- at strings (quoted or not) and at the five complex types.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return _number(value)
    return None


def display_text(value: Any) -> str:
    """The `descriptions`/hyprlang spelling of a model value.

    Round-trips with `parse_value`, which is what lets a schema default, an imported
    `.conf` line and a UI edit be compared as text without caring where each came from.
    """
    scalar = _scalar_spelling(value)
    if scalar is not None:
        return scalar
    return value if isinstance(value, str) else str(value)


@runtime_checkable
class LuaValue(Protocol):
    """Anything that knows its own Lua literal -- the five types in `COMPLEX_TYPES`.

    A Protocol rather than a base class: these are frozen slotted dataclasses whose only
    shared behaviour *is* this one method, and it names the contract for mypy instead of
    leaving the writer to guess with `hasattr`.
    """

    def lua(self) -> str: ...


def lua_literal(value: Any) -> str:
    """The Lua source form of a model value -- what the writer puts in a Module."""
    scalar = _scalar_spelling(value)
    if scalar is not None:
        return scalar
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, LuaValue):
        return value.lua()
    raise TypeError(f"no Lua literal for {value!r}")


def has_emittable_null(option: ResolvedOption) -> bool:
    """Whether this Option's "no value" is something Lua can actually be told.

    Most nullable Options have a real null spelling: `input:kb_variant` takes `""`,
    `general:float_gaps` takes `-1`. Five do not. The colour and gradient Options whose
    fallback is "use the related colour" carry a curated `null_value` of `-1`, which is how
    the *C++ declaration* spells it -- and `LuaConfigColor` rejects it outright
    (`invalid color "-1"`, caught by the static verify gate). For those, the null state is
    absence: not emitting the key is exactly what makes Hyprland fall back.

    The test is "does the null value parse as a value of this Option's type", which is the
    same question the Lua parser will ask, rather than a hardcoded list of five names that
    a new release could silently outgrow.
    """
    if option.null_value is None:
        return False
    try:
        parse_value(option.type, option.null_value)
    except (ValueError, TypeError):
        return False
    return True


def lua_literal_for(option: ResolvedOption, value: Any) -> str:
    """The Lua literal for one Option's model value, explicit null included.

    Explicit null emits the curated `null_value` **verbatim**, not a re-typed version of it:
    `-1` in `general:float_gaps` is Hyprland's own "same as the outer gaps" marker, and
    rendering it as a css-gaps table of four `-1`s would be a different statement.
    """
    if value is None:
        if not has_emittable_null(option):
            raise ValueError(f"{option.name} is set to null but has no null value to emit")
        return lua_literal(option.null_value)
    return lua_literal(value)


def parse_getoption(option: ResolvedOption, payload: dict[str, Any]) -> Any:
    """One `hyprctl -j getoption` reply -> the model's Python value.

    The reply's type key is engine-dependent: under hyprlang the three complex types all
    answer as `custom`, under the Lua engine they answer as themselves. `GetOptionKey`
    records the expected key and `custom` stays a fallback, so the same reader survives
    both engines (`schema/types.py`).
    """
    key = option.getoption_key.value
    if key not in payload and GetOptionKey.CUSTOM.value in payload:
        key = GetOptionKey.CUSTOM.value
    if key not in payload:
        raise KeyError(f"getoption reply for {option.name} has no {key!r} key: {payload!r}")

    raw = payload[key]
    if (complex_type := COMPLEX_TYPES.get(option.type)) is not None:
        return complex_type.from_getoption(raw)
    return parse_value(option.type, raw)


FLOAT_RELATIVE_TOLERANCE = 1e-6
"""Hyprland holds config floats as 32-bit, so a `0.95` written from a Python double reads
back as the nearest float32. Comparing exactly would call every fractional Option the app
has ever written correctly a mismatch."""

FLOAT_ABSOLUTE_TOLERANCE = 1e-9


def values_match(expected: Any, actual: Any) -> bool:
    """Whether two model values mean the same thing, to the precision the wire preserves.

    The fourth question a value has to answer, and it belongs here with the other three:
    only this module knows that a `Gradient` is a dataclass wrapping floats, that a `Color`
    is an exact integer word, and that Hyprland's own storage is 32-bit. A caller comparing
    with `==` gets the right answer for colours and the wrong one for opacities.

    Used by the Apply transaction's Read-back and, later, by the ADR-0005 drift scan --
    which ask the same question of the same pair of values.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        # Checked before the numeric branch: `True` is an `int`, and `isclose(True, 1)` is
        # true, which would make a bool Option agree with a value it does not have.
        return bool(expected == actual)
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return math.isclose(
            expected,
            actual,
            rel_tol=FLOAT_RELATIVE_TOLERANCE,
            abs_tol=FLOAT_ABSOLUTE_TOLERANCE,
        )
    if is_dataclass(expected) and type(expected) is type(actual):
        # The five complex types. A top-level `==` on `Gradient` would compare its angle
        # exactly, so the walk has to reach the floats inside.
        return all(
            values_match(getattr(expected, item.name), getattr(actual, item.name))
            for item in fields(expected)
        )
    if (
        isinstance(expected, tuple | list)
        and isinstance(actual, tuple | list)
        and len(expected) == len(actual)
    ):
        return all(
            values_match(one, other) for one, other in zip(expected, actual, strict=True)
        )
    return bool(expected == actual)
