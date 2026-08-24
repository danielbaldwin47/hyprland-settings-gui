"""The canonical `animations.lua` Module: curves first, then the animations (#70).

Order inside the file is the whole reason these two kinds share one: an
`hl.animation{bezier = "easy"}` is refused unless a curve named `easy` already exists
(CR:430-441). Emitting every `hl.curve` before every `hl.animation` makes that a property
of this renderer, which can guarantee it. Splitting them into two Modules would have made
it a property of the Entrypoint's sorted require list, which puts `animations` before
`curves` and would break every reference in the file.

The dangling reference this ordering cannot fix -- a curve that is named but never
declared anywhere -- is not the writer's to refuse. It is reported by
`entities_catalog.dangling_curve_references` and shown on the row, for the reason ADR-0008
gives about unknown rule effects: a config the app declines to write is a config the user
cannot fix in the app.
"""

from __future__ import annotations

from ..model.entities import Animation, Curve
from ..model.values import lua_string
from .binds import lua_value
from .lua import ordered_fields, render_entity_module, table_key

_CURVE_KEY_ORDER = ("type", "points", "mass", "stiffness", "dampening")
"""`type` first because it decides which of the other keys are even legal.

The rest are listed so a spring reads mass, stiffness, dampening in the order the wiki
writes them rather than alphabetically, which would put dampening first and make two
adjacent springs harder to compare than they need to be.
"""

_ANIMATION_KEY_ORDER = ("enabled", "speed", "bezier", "spring", "style")
"""The order the legacy `animation = leaf, on, speed, curve, style` keyword had.

Anyone who has written a Hyprland config has read this order a hundred times; keeping it
means a generated line and a hand-written one scan the same way.
"""


def render_curve(curve: Curve) -> str:
    """One `hl.curve(name, {...})` line: the name positionally, then the spec as held.

    Positional because that is the only shape the parser takes -- `hl.curve` is the one
    declarative call in the API whose identity is an argument rather than a table key
    (`lua-api-surface.md` §10).
    """
    spec = ordered_fields(curve.spec, first=_CURVE_KEY_ORDER)
    body = ", ".join(f"{table_key(str(key))}{lua_value(value)}" for key, value in spec)
    return f"hl.curve({lua_string(curve.name)}, {{ {body} }})"


def render_animation(animation: Animation) -> str:
    """One `hl.animation({...})` line: the leaf first, then the fields as held."""
    parts = [f"leaf = {lua_string(animation.leaf)}"]
    parts.extend(
        f"{table_key(str(key))}{lua_value(value)}"
        for key, value in ordered_fields(animation.fields, first=_ANIMATION_KEY_ORDER)
    )
    return f"hl.animation({{ {', '.join(parts)} }})"


def render_animations_module(
    curves: list[Curve], animations: list[Animation], *, app_version: str
) -> str | None:
    """The whole `animations.lua`, or `None` when there is nothing to write.

    Curves first, always, then one call per animation leaf in the model's order.
    `EntitySet.add_animation` already collapsed the last-write-wins duplicates a config can
    contain, so what is written is what the compositor would have ended up with rather than
    a transcript of how it got there.

    A curve no animation uses is still written: a curve is a named thing the user made, and
    pruning it the moment its last animation changed shape would delete work that took
    experimenting to get right.
    """
    lines = [render_curve(curve) for curve in curves]
    if lines and animations:
        lines.append("")
    lines.extend(render_animation(animation) for animation in animations)
    return render_entity_module(
        lines,
        comment="Animation curves, then the animations that name them.",
        app_version=app_version,
    )


__all__ = [
    "render_animation",
    "render_animations_module",
    "render_curve",
]
