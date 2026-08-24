"""What the Monitors editor knows about displays, headless (ADR-0008, #68).

The Arrangement canvas is a GTK widget, but everything it *decides* -- how big a display
is at logical size, where a dragged edge snaps, which rule speaks for which connected
output, what identity a new rule should take -- is plain arithmetic and string matching.
It lives here so the geometry that positions real monitors is testable on a machine that
has none (ADR-0011), the same seam `rules_catalog` draws for the Rule editor.

Nothing here imports `gi`, and nothing here holds state: helper data from
`hyprctl -j monitors` comes in as the raw mappings the IPC layer returns, and rule state
comes in as `MonitorRule` -- the two are never merged, because IPC reflects display
*state* while the model holds *rules* (ADR-0008).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from .model.entities import MonitorRule

DISPLAY_BREAKING_FIELDS: frozenset[str] = frozenset(
    {
        "mode",
        "modeline",
        "position",
        "scale",
        "transform",
        "disabled",
        "mirror",
        "bitdepth",
        "cm",
    }
)
"""Monitor rule fields whose misapplication can black-screen the session (ADR-0008).

Edits to these batch and apply behind the Confirm-or-revert countdown; everything else
(vrr, reserved, sdr brightness/saturation) stays instant per ADR-0003. `modeline` rides
along with `mode`: a wrong custom modeline is the most breaking value of all.
"""

CATCH_ALL_OUTPUT = ""
"""The `output` string of the fallback rule -- "Any other display" (ADR-0008)."""

TRANSFORM_NAMES: tuple[str, ...] = (
    "Normal",
    "Rotated 90°",
    "Rotated 180°",
    "Rotated 270°",
    "Flipped",
    "Flipped, rotated 90°",
    "Flipped, rotated 180°",
    "Flipped, rotated 270°",
)
"""The eight wl_output transforms, indexed by the `transform` value they emit."""

SPECIAL_MODES: tuple[str, ...] = ("preferred", "highres", "highrr", "maxwidth")
"""The mode words Hyprland accepts besides a literal `WxH@Hz` (ADR-0008)."""

_MODE = re.compile(r"^\s*(\d+)x(\d+)(?:@([\d.]+))?(?:Hz)?\s*$", re.IGNORECASE)
_POSITION = re.compile(r"^\s*(-?\d+)x(-?\d+)\s*$")

_DESC_PREFIX = "desc:"


# --- geometry --------------------------------------------------------------------------


def logical_size(
    width: int, height: int, *, scale: Any = 1.0, transform: Any = 0
) -> tuple[int, int]:
    """A display's footprint in layout coordinates: pixel mode ÷ scale, rotation-aware.

    This is the size the canvas draws and drags (ADR-0008: "logical-size rects") and the
    size position rules butt up against: two 1920-wide displays at scale 2 sit at `0x0`
    and `960x0`, not `1920x0`. An odd transform is a 90° rotation, so width and height
    swap *after* scaling. A non-numeric scale (`"auto"`) reads as 1.0 -- the canvas can
    only be as right as the data it was given, and 1.0 is what Hyprland defaults to when
    auto-scaling declines to scale.
    """
    try:
        factor = float(scale)
    except (TypeError, ValueError):
        factor = 1.0
    if factor <= 0:
        factor = 1.0
    logical_w = round(width / factor)
    logical_h = round(height / factor)
    try:
        rotated = int(transform) % 2 == 1
    except (TypeError, ValueError):
        rotated = False
    return (logical_h, logical_w) if rotated else (logical_w, logical_h)


def parse_mode(text: str) -> tuple[int, int, float | None] | None:
    """`"1920x1080@60.01Hz"` (the `availableModes` spelling) as numbers, or `None`.

    The special mode words and `auto` are not sizes, so they answer `None` -- a caller
    that needs a rect for one falls back to the output's current mode from IPC.
    """
    matched = _MODE.match(text or "")
    if matched is None:
        return None
    width, height = int(matched.group(1)), int(matched.group(2))
    refresh = float(matched.group(3)) if matched.group(3) else None
    return width, height, refresh


def format_mode(width: int, height: int, refresh: float | None = None) -> str:
    """The `mode` field spelling of a resolution: `1920x1080` or `1920x1080@60`.

    The refresh is emitted trimmed (`@60`, `@59.94`) because the rule is a *request*:
    Hyprland picks the closest advertised mode, and `@60.01` would over-promise a
    precision the parser does not need.
    """
    if refresh is None:
        return f"{width}x{height}"
    trimmed = f"{refresh:.2f}".rstrip("0").rstrip(".")
    return f"{width}x{height}@{trimmed}"


def parse_position(text: str) -> tuple[int, int] | None:
    """A `position` field's `"XxY"` as numbers, or `None` for `auto` and friends."""
    matched = _POSITION.match(text or "")
    if matched is None:
        return None
    return int(matched.group(1)), int(matched.group(2))


def format_position(x: int, y: int) -> str:
    """The integer `"XxY"` a canvas drop commits (ADR-0008)."""
    return f"{int(x)}x{int(y)}"


def snap_position(
    x: int,
    y: int,
    width: int,
    height: int,
    others: Iterable[tuple[int, int, int, int]],
    *,
    threshold: int = 24,
) -> tuple[int, int]:
    """Where a dragged rect lands: edge-snapped to its neighbours, in logical pixels.

    Each axis snaps independently to the nearest aligned or abutting edge of any other
    rect -- left-to-left, left-to-right, right-to-left, right-to-right, and likewise
    vertically -- when it is within `threshold`. Abutment is the case that matters:
    Hyprland tolerates gaps and overlaps, but the layout a user drags toward is almost
    always "this display starts where that one ends", and hitting it exactly by hand at
    canvas scale is luck.
    """
    best_dx: tuple[int, int] | None = None  # (|distance|, correction)
    best_dy: tuple[int, int] | None = None
    for other_x, other_y, other_w, other_h in others:
        for target in (other_x, other_x + other_w):
            for own in (x, x + width):
                distance = target - own
                if abs(distance) <= threshold and (
                    best_dx is None or abs(distance) < best_dx[0]
                ):
                    best_dx = (abs(distance), distance)
        for target in (other_y, other_y + other_h):
            for own in (y, y + height):
                distance = target - own
                if abs(distance) <= threshold and (
                    best_dy is None or abs(distance) < best_dy[0]
                ):
                    best_dy = (abs(distance), distance)
    snapped_x = x + best_dx[1] if best_dx is not None else x
    snapped_y = y + best_dy[1] if best_dy is not None else y
    return snapped_x, snapped_y


# --- identity --------------------------------------------------------------------------


def preferred_identity(
    connector: str, description: str, *, taken_descriptions: Collection[str] = ()
) -> str:
    """The `output` string a new rule for a connected display should take (ADR-0008).

    `desc:<description>` when the description is non-empty and unique among
    `taken_descriptions` -- the other connected and already-configured outputs -- because
    a description survives the dock shuffles that rename `DP-1` to `DP-3`. Identical
    monitors collide on it, and then the connector is the only honest address left.
    """
    cleaned = description.strip()
    if cleaned and cleaned not in taken_descriptions:
        return f"{_DESC_PREFIX}{cleaned}"
    return connector


def description_of(rule_output: str) -> str | None:
    """The description a `desc:` identity names, or `None` for a connector identity.

    The one place the `desc:` prefix is peeled, so no caller re-spells the literal.
    """
    if rule_output.startswith(_DESC_PREFIX):
        return rule_output[len(_DESC_PREFIX) :].strip()
    return None


def rule_matches_output(rule_output: str, *, connector: str, description: str) -> bool:
    """Whether a rule's `output` string speaks for this connected display.

    Mirrors Hyprland's static selector: a `desc:` identity matches by *prefix* against
    the description (the wiki drops the `(port)` suffix, so an exact compare would break
    every rule written against a truncated description), anything else is the connector,
    compared exactly. The catch-all matches nothing here -- it is a fallback, not an
    identity, and the canvas treats it separately.
    """
    if rule_output == CATCH_ALL_OUTPUT:
        return False
    wanted = description_of(rule_output)
    if wanted is not None:
        return bool(wanted) and description.strip().startswith(wanted)
    return rule_output == connector


def rule_for(
    rules: Sequence[MonitorRule], *, connector: str, description: str
) -> MonitorRule | None:
    """The rule speaking for a connected display, or `None` when it has no rule yet.

    `None` is the hotplug hint's trigger (ADR-0008): a newly connected output with no
    rule is the one case the Monitors page points out rather than silently defaults.
    """
    for rule in rules:
        if rule_matches_output(rule.output, connector=connector, description=description):
            return rule
    return None


def connected_rules(
    rules: Sequence[MonitorRule], monitors: Sequence[Mapping[str, Any]]
) -> dict[str, MonitorRule | None]:
    """Each connected output's rule (or `None`), keyed by connector name."""
    return {
        str(monitor.get("name", "")): rule_for(
            rules,
            connector=str(monitor.get("name", "")),
            description=str(monitor.get("description", "")),
        )
        for monitor in monitors
    }


def disconnected_rules(
    rules: Sequence[MonitorRule], monitors: Sequence[Mapping[str, Any]]
) -> tuple[MonitorRule, ...]:
    """The rules matching no connected output -- the "Not connected" group (ADR-0008).

    The catch-all is excluded: it renders as the fixed "Any other display" row, not as a
    disconnected display's leftovers.
    """
    claimed: set[str] = set()
    for monitor in monitors:
        rule = rule_for(
            rules,
            connector=str(monitor.get("name", "")),
            description=str(monitor.get("description", "")),
        )
        if rule is not None:
            claimed.add(rule.output)
    return tuple(
        rule for rule in rules if rule.output != CATCH_ALL_OUTPUT and rule.output not in claimed
    )


__all__ = [
    "CATCH_ALL_OUTPUT",
    "DISPLAY_BREAKING_FIELDS",
    "SPECIAL_MODES",
    "TRANSFORM_NAMES",
    "connected_rules",
    "description_of",
    "disconnected_rules",
    "format_mode",
    "format_position",
    "logical_size",
    "parse_mode",
    "parse_position",
    "preferred_identity",
    "rule_for",
    "rule_matches_output",
    "snap_position",
]
