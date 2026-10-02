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

Gradient (`border_color`, #156) adds its own `parse_border_color`/`emit_border_color`
here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

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
