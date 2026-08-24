"""The typed Match and Effect surface of window and layer rules (ADR-0008, #67).

What the Rule editor's pickers are generated from: the 18 match props and the 57 static
window effects plus 10 layer effects of `lua-api-surface.md` §5, each with the type that
decides its widget and the category that decides its shelf in the add-effect picker.

Declarative and UI-free, like `dispatchers.py` for binds. The Importer keeps its own
typed tables in `importer/rules.py` -- those answer "how do I coerce this legacy string",
this answers "what can the user add" -- and a drift-guard test asserts the two agree
name-for-name, so an effect added in one place cannot silently be missing from the other.

An effect *not* in this catalog is not an error anywhere: unknown keys pass through the
model, the writer and the compositor's dynamic/plugin effect registry untouched. The
editor shows them as raw custom effects (ADR-0008: "never dropped").
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class MatchKind(enum.Enum):
    """What a match prop's value is -- and therefore what widget edits it."""

    REGEX = "regex"
    BOOL = "bool"
    INT = "int"
    WORKSPACE = "workspace"
    TAG = "tag"


NEGATABLE_KINDS = frozenset({MatchKind.REGEX, MatchKind.WORKSPACE, MatchKind.TAG})
"""The kinds whose value takes the `negative:` prefix.

The string-valued kinds: the prefix is spelled inside the value, so only a string can
carry it. A bool match prop negates by flipping the switch, an int one by changing the
number -- offering a second negation on top would let one row say two opposite things.
"""


class EffectType(enum.Enum):
    """An effect's Lua value type -- and therefore its widget."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"


NEGATIVE_PREFIX = "negative:"
"""How negation is spelled on the wire: inside the match value, never beside it."""


def is_negated(value: object) -> bool:
    """Whether a match value carries the `negative:` prefix.

    Only strings can: the prefix lives inside the value, which is why `NEGATABLE_KINDS`
    is exactly the string-valued kinds.
    """
    return isinstance(value, str) and value.startswith(NEGATIVE_PREFIX)


def strip_negation(value: str) -> str:
    """The value without its `negative:` prefix -- what the editor's entry shows, so the
    user edits the pattern and the toggle owns the negation."""
    return value[len(NEGATIVE_PREFIX) :] if value.startswith(NEGATIVE_PREFIX) else value


def prop_title(name: str) -> str:
    """A match prop or effect name as UI title text: `initial_class` -> `Initial class`.

    Derived rather than curated: the names are the `hl.*` field names (CONTEXT.md's
    Entity rule), so a mechanical spelling keeps the title and the emitted key visibly
    the same thing.
    """
    return name.replace("_", " ").capitalize()


@dataclass(frozen=True, slots=True)
class MatchProp:
    """One typed match prop: the `match = {...}` key and the kind that picks its widget."""

    name: str
    kind: MatchKind


@dataclass(frozen=True, slots=True)
class Effect:
    """One typed effect: the spec-table key, its Lua value type, and its picker shelf."""

    name: str
    type: EffectType
    category: str


WINDOW_MATCH_PROPS: tuple[MatchProp, ...] = (
    MatchProp("class", MatchKind.REGEX),
    MatchProp("title", MatchKind.REGEX),
    MatchProp("initial_class", MatchKind.REGEX),
    MatchProp("initial_title", MatchKind.REGEX),
    MatchProp("content", MatchKind.REGEX),
    MatchProp("xdg_tag", MatchKind.REGEX),
    MatchProp("float", MatchKind.BOOL),
    MatchProp("xwayland", MatchKind.BOOL),
    MatchProp("fullscreen", MatchKind.BOOL),
    MatchProp("pin", MatchKind.BOOL),
    MatchProp("focus", MatchKind.BOOL),
    MatchProp("group", MatchKind.BOOL),
    MatchProp("modal", MatchKind.BOOL),
    MatchProp("fullscreen_state_internal", MatchKind.INT),
    MatchProp("fullscreen_state_client", MatchKind.INT),
    MatchProp("workspace", MatchKind.WORKSPACE),
    MatchProp("tag", MatchKind.TAG),
)

LAYER_MATCH_PROPS: tuple[MatchProp, ...] = (MatchProp("namespace", MatchKind.REGEX),)
"""The one match prop that does anything for a layer rule (research §2.6)."""


_PLACEMENT = "Placement"
_FOCUS = "Focus & input"
_APPEARANCE = "Appearance"
_BEHAVIOR = "Behavior"
_ADVANCED = "Advanced"

CATEGORIES: tuple[str, ...] = (_PLACEMENT, _FOCUS, _APPEARANCE, _BEHAVIOR, _ADVANCED)
"""Picker shelf order: what a rule does to a window before how it behaves."""

WINDOW_EFFECTS: tuple[Effect, ...] = (
    # Placement -- where and how big the window is.
    Effect("float", EffectType.BOOL, _PLACEMENT),
    Effect("tile", EffectType.BOOL, _PLACEMENT),
    Effect("center", EffectType.BOOL, _PLACEMENT),
    Effect("fullscreen", EffectType.BOOL, _PLACEMENT),
    Effect("maximize", EffectType.BOOL, _PLACEMENT),
    Effect("fullscreen_state", EffectType.STRING, _PLACEMENT),
    Effect("pseudo", EffectType.BOOL, _PLACEMENT),
    Effect("pin", EffectType.BOOL, _PLACEMENT),
    Effect("move", EffectType.STRING, _PLACEMENT),
    Effect("size", EffectType.STRING, _PLACEMENT),
    Effect("max_size", EffectType.STRING, _PLACEMENT),
    Effect("min_size", EffectType.STRING, _PLACEMENT),
    Effect("persistent_size", EffectType.BOOL, _PLACEMENT),
    Effect("monitor", EffectType.STRING, _PLACEMENT),
    Effect("workspace", EffectType.STRING, _PLACEMENT),
    Effect("group", EffectType.STRING, _PLACEMENT),
    # Focus & input -- who gets the pointer and the keys.
    Effect("focus_on_activate", EffectType.BOOL, _FOCUS),
    Effect("stay_focused", EffectType.BOOL, _FOCUS),
    Effect("no_focus", EffectType.BOOL, _FOCUS),
    Effect("no_initial_focus", EffectType.BOOL, _FOCUS),
    Effect("no_follow_mouse", EffectType.BOOL, _FOCUS),
    Effect("allows_input", EffectType.BOOL, _FOCUS),
    Effect("no_shortcuts_inhibit", EffectType.BOOL, _FOCUS),
    Effect("confine_pointer", EffectType.BOOL, _FOCUS),
    Effect("scroll_mouse", EffectType.FLOAT, _FOCUS),
    Effect("scroll_touchpad", EffectType.FLOAT, _FOCUS),
    Effect("scrolling_width", EffectType.FLOAT, _FOCUS),
    # Appearance -- how the window is drawn.
    Effect("opacity", EffectType.STRING, _APPEARANCE),
    Effect("opaque", EffectType.BOOL, _APPEARANCE),
    Effect("border_color", EffectType.STRING, _APPEARANCE),
    Effect("border_size", EffectType.INT, _APPEARANCE),
    Effect("rounding", EffectType.INT, _APPEARANCE),
    Effect("rounding_power", EffectType.FLOAT, _APPEARANCE),
    Effect("no_blur", EffectType.BOOL, _APPEARANCE),
    Effect("no_dim", EffectType.BOOL, _APPEARANCE),
    Effect("no_shadow", EffectType.BOOL, _APPEARANCE),
    Effect("no_anim", EffectType.BOOL, _APPEARANCE),
    Effect("animation", EffectType.STRING, _APPEARANCE),
    Effect("dim_around", EffectType.BOOL, _APPEARANCE),
    Effect("decorate", EffectType.BOOL, _APPEARANCE),
    Effect("nearest_neighbor", EffectType.BOOL, _APPEARANCE),
    Effect("xray", EffectType.BOOL, _APPEARANCE),
    # Behavior -- what the window is allowed or made to do.
    Effect("keep_aspect_ratio", EffectType.BOOL, _BEHAVIOR),
    Effect("idle_inhibit", EffectType.STRING, _BEHAVIOR),
    Effect("suppress_event", EffectType.STRING, _BEHAVIOR),
    Effect("no_close_for", EffectType.INT, _BEHAVIOR),
    Effect("no_max_size", EffectType.BOOL, _BEHAVIOR),
    Effect("sync_fullscreen", EffectType.BOOL, _BEHAVIOR),
    Effect("tag", EffectType.STRING, _BEHAVIOR),
    Effect("content", EffectType.STRING, _BEHAVIOR),
    # Advanced -- rendering pipeline and capture quirks.
    Effect("immediate", EffectType.BOOL, _ADVANCED),
    Effect("force_rgbx", EffectType.BOOL, _ADVANCED),
    Effect("render_unfocused", EffectType.BOOL, _ADVANCED),
    Effect("no_screen_share", EffectType.BOOL, _ADVANCED),
    Effect("no_vrr", EffectType.BOOL, _ADVANCED),
    Effect("no_auto_hdr", EffectType.BOOL, _ADVANCED),
    Effect("tonemap", EffectType.STRING, _ADVANCED),
)

LAYER_EFFECTS: tuple[Effect, ...] = (
    Effect("blur", EffectType.BOOL, _APPEARANCE),
    Effect("blur_popups", EffectType.BOOL, _APPEARANCE),
    Effect("ignore_alpha", EffectType.FLOAT, _APPEARANCE),
    Effect("no_anim", EffectType.BOOL, _APPEARANCE),
    Effect("animation", EffectType.STRING, _APPEARANCE),
    Effect("dim_around", EffectType.BOOL, _APPEARANCE),
    Effect("xray", EffectType.BOOL, _APPEARANCE),
    Effect("order", EffectType.INT, _BEHAVIOR),
    Effect("above_lock", EffectType.INT, _BEHAVIOR),
    Effect("no_screen_share", EffectType.BOOL, _ADVANCED),
)


def match_props(kind: str) -> tuple[MatchProp, ...]:
    """The match surface for a rule kind -- `"window"` or `"layer"`.

    An unknown kind raises, matching `Session.rules`: every caller is dispatching on the
    same two-valued concept, and a typo answering with the window surface would be a
    picker quietly offering props the other kind rejects.
    """
    if kind == "window":
        return WINDOW_MATCH_PROPS
    if kind == "layer":
        return LAYER_MATCH_PROPS
    raise ValueError(f"unknown rule kind {kind!r}")


def effects(kind: str) -> tuple[Effect, ...]:
    """The typed effect surface for a rule kind. Unknown kinds raise, as above."""
    if kind == "window":
        return WINDOW_EFFECTS
    if kind == "layer":
        return LAYER_EFFECTS
    raise ValueError(f"unknown rule kind {kind!r}")


def find_effect(kind: str, name: str) -> Effect | None:
    """The typed spec for an effect name, or `None` for an unknown/plugin effect.

    `None` is an answer, not a failure: an unknown effect is legal everywhere (the
    dynamic/plugin registry), and this is how callers tell "typed widget" from "raw row".
    """
    for effect in effects(kind):
        if effect.name == name:
            return effect
    return None


def find_match_prop(kind: str, name: str) -> MatchProp | None:
    """The typed spec for a match prop name, or `None` for one the catalog predates."""
    for prop in match_props(kind):
        if prop.name == name:
            return prop
    return None


__all__ = [
    "CATEGORIES",
    "LAYER_EFFECTS",
    "LAYER_MATCH_PROPS",
    "NEGATABLE_KINDS",
    "NEGATIVE_PREFIX",
    "WINDOW_EFFECTS",
    "WINDOW_MATCH_PROPS",
    "Effect",
    "EffectType",
    "MatchKind",
    "MatchProp",
    "effects",
    "find_effect",
    "find_match_prop",
    "is_negated",
    "match_props",
    "prop_title",
    "strip_negation",
]
