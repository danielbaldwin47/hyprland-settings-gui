"""The rule effects whose value is a string with a grammar of its own (ADR-0008).

`opacity`, `fullscreen_state` and `suppress_event` are strings in the Lua API: the
compositor parses the text, so the Config view's helper widgets have to speak exactly that
text (`docs/research/hyprlang-to-lua.md`, the effects table). Per effect, `parse_<effect>`
turns text into a typed value and `emit_<effect>` turns it back into the string.

A parse returns `None` for any text its typed value cannot carry *unchanged* -- an unknown
token, a number outside the picker's range, a form whose meaning the typed value would
have to guess. The editor then opens that value as text, so nothing is dropped or
mangled. A value that is not a `str` at all (the importer keeps these effects as strings)
is `None` too.

`border_color` (#156) is the one grammar whose value may be a table: one gradient as
`{colors = {...}, angle = N}`, which `emit_border_color` returns as a dict. The legacy
string for the active+inactive pair has no table form, so it stays text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from hyprtweaker.engine.model.values import Color, Gradient

_OVERRIDE = "override"
"""Opacity's keyword: use the value as it is rather than multiplying it into the global
`active_opacity`/`inactive_opacity`."""

_NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?|\.[0-9]+")

_STATE_DIGITS = 2
"""Opacity is edited on a two-decimal spin button; a value finer than that stays text."""


@dataclass(frozen=True, slots=True)
class OpacityState:
    """One window state's opacity: the value and whether it overrides the global one."""

    value: float
    override: bool = False


@dataclass(frozen=True, slots=True)
class Opacity:
    """The three states of the `opacity` effect, in the order the grammar lists them."""

    active: OpacityState
    inactive: OpacityState
    fullscreen: OpacityState

    @property
    def states(self) -> tuple[OpacityState, OpacityState, OpacityState]:
        return (self.active, self.inactive, self.fullscreen)


OPAQUE = OpacityState(1.0)
"""What a state the text leaves out amounts to: the window is not made any less opaque."""


def parse_opacity(value: object) -> Opacity | None:
    """`"a [override] [b [override] [c [override]]]"`, one to three values from 0 to 1.

    One value copies, override included, to the other two states (`WindowRule.cpp:130-133`);
    a second value leaves fullscreen opaque.
    """
    tokens = _tokens(value)
    if not tokens:
        return None

    values: list[OpacityState] = []
    for token in tokens:
        if token == _OVERRIDE:
            if not values or values[-1].override:
                return None
            values[-1] = OpacityState(values[-1].value, True)
            continue
        number = _opacity_number(token)
        if number is None or len(values) == 3:
            return None
        values.append(OpacityState(number))

    if len(values) == 1:
        return Opacity(values[0], values[0], values[0])
    if len(values) == 2:
        return Opacity(values[0], values[1], OPAQUE)
    return Opacity(values[0], values[1], values[2])


def emit_opacity(opacity: Opacity) -> str:
    """The shortest text that means the same: one value when all three states agree, two
    when fullscreen is left opaque, else all three. A string, never a number: the Lua
    API rejects `opacity = 0.9`."""
    active, inactive, fullscreen = opacity.states
    if active == inactive == fullscreen:
        return _opacity_state_text(active)
    if fullscreen == OPAQUE:
        return f"{_opacity_state_text(active)} {_opacity_state_text(inactive)}"
    return " ".join(_opacity_state_text(state) for state in opacity.states)


def _opacity_state_text(state: OpacityState) -> str:
    # `round` first: a spin button's stepped float carries noise (0.1 + 0.2).
    number = f"{round(state.value, _STATE_DIGITS):g}"
    return f"{number} {_OVERRIDE}" if state.override else number


def _opacity_number(token: str) -> float | None:
    if not _NUMBER.fullmatch(token):
        return None
    number = Decimal(token)
    exponent = number.as_tuple().exponent
    assert isinstance(exponent, int)  # finite: the pattern admits digits only
    if exponent < -_STATE_DIGITS or number > 1:
        return None
    return float(number)


@dataclass(frozen=True, slots=True)
class FullscreenState:
    """`fullscreen_state`: what the compositor thinks (`internal`) and what the client
    is told (`client`). Each is 0 none, 1 maximized, 2 fullscreen, 3 both."""

    internal: int
    client: int


FULLSCREEN_STATE_CHOICES = (
    "0 · None",
    "1 · Maximized",
    "2 · Fullscreen",
    "3 · Maximized and fullscreen",
)
"""The picker's labels, indexed by the number they stand for."""


def parse_fullscreen_state(value: object) -> FullscreenState | None:
    """`"<internal> <client>"`, both 0 to 3. A single number or `-1` means "leave this one
    as it is", which two pickers cannot say: those stay text."""
    tokens = _tokens(value)
    if len(tokens) != 2 or not all(token in ("0", "1", "2", "3") for token in tokens):
        return None
    return FullscreenState(int(tokens[0]), int(tokens[1]))


def emit_fullscreen_state(state: FullscreenState) -> str:
    return f"{state.internal} {state.client}"


SUPPRESS_EVENTS = (
    "fullscreen",
    "maximize",
    "activate",
    "activatefocus",
    "fullscreenoutput",
    "x11configurerequest",
)
"""The events a window can be kept from triggering, in the checklist's order."""


def parse_suppress_event(value: object) -> tuple[str, ...] | None:
    """A space-separated list of `SUPPRESS_EVENTS`, returned in that order and without
    repeats. One token this list does not know sends the whole value to text."""
    tokens = _tokens(value)
    if not tokens or any(token not in SUPPRESS_EVENTS for token in tokens):
        return None
    return tuple(event for event in SUPPRESS_EVENTS if event in tokens)


def emit_suppress_event(events: tuple[str, ...]) -> str:
    return " ".join(event for event in SUPPRESS_EVENTS if event in events)


def _tokens(value: object) -> list[str]:
    return value.split() if isinstance(value, str) else []


# --- border_color -------------------------------------------------------------------------

MAX_STOPS = 10
"""The most colour stops the editor shows in a row; a longer list stays text."""

MAX_ANGLE = 360
"""The angle scale runs 0 to this many whole degrees."""

_GRADIENT_ANGLE = re.compile(r"([0-9]+)deg")


def parse_border_color(value: object) -> Gradient | None:
    """One gradient: a table `{colors = {...}, angle = N}` or a legacy `colors... [Ndeg]`.

    The other shapes the legacy string takes -- an active+inactive pair (`"c1 c2"`),
    a second gradient, an angle between colours -- have no table form, and nor does a
    colour `Color.parse` rejects, a fractional or out-of-range angle, a key the table does
    not know, or more than `MAX_STOPS` stops: those are `None`, so the editor opens them as
    text. A plain colour is one stop at angle 0.
    """
    if isinstance(value, str):
        return _parse_gradient_text(value)
    if isinstance(value, Mapping):
        return _parse_gradient_table(value)
    return None


def _parse_gradient_text(text: str) -> Gradient | None:
    tokens = text.split()
    angle: int | None = None
    if tokens and (match := _GRADIENT_ANGLE.fullmatch(tokens[-1])) is not None:
        angle = int(match.group(1))
        tokens = tokens[:-1]
    if angle is None and len(tokens) > 1:
        return None  # two colours and no angle: the active+inactive pair
    return _gradient(tokens, 0 if angle is None else angle)


def _parse_gradient_table(table: Mapping[object, object]) -> Gradient | None:
    if not set(table) <= {"colors", "angle"}:
        return None
    colors = table.get("colors")
    angle = table.get("angle", 0)
    if not isinstance(colors, list | tuple) or not all(isinstance(c, str) for c in colors):
        return None
    if isinstance(angle, bool) or not isinstance(angle, int | float) or angle % 1:
        return None
    return _gradient([str(color) for color in colors], int(angle))


def _gradient(colors: list[str], angle: int) -> Gradient | None:
    if not 1 <= len(colors) <= MAX_STOPS or not 0 <= angle <= MAX_ANGLE:
        return None
    try:
        return Gradient(tuple(Color.parse(color) for color in colors), float(angle))
    except ValueError:
        return None


def emit_border_color(gradient: Gradient) -> dict[str, object]:
    """The table the Lua API reads: `rgba(rrggbbaa)` strings (the form `Color.lua()` commits
    to, minus the Lua quoting, which the writer adds) and a whole-number angle."""
    return {
        "colors": [_stop_text(color) for color in gradient.colors],
        "angle": round(gradient.angle),
    }


def border_color_text(gradient: Gradient) -> str:
    """The gradient as the legacy string. Two or more stops always carry their angle: the
    same colours without it would read back as an active+inactive pair."""
    colors = " ".join(_stop_text(color) for color in gradient.colors)
    if len(gradient.colors) == 1 and not gradient.angle:
        return colors
    return f"{colors} {round(gradient.angle)}deg"


def border_color_source_text(value: object) -> str:
    """An original `border_color` as text, for a value `parse_border_color` rejected: the
    string as it was, or a table's colours and angle in the legacy order, spelling kept."""
    if isinstance(value, Mapping):
        colors = value.get("colors")
        parts = [str(color) for color in colors] if isinstance(colors, list | tuple) else []
        if "angle" in value:
            parts.append(f"{value['angle']}deg")
        return " ".join(parts)
    return str(value)


def _stop_text(color: Color) -> str:
    return f"rgba({color.rgba:08x})"
