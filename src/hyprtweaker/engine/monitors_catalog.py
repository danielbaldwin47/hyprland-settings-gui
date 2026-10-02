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
from dataclasses import replace
from typing import Any

from .model.entities import MonitorRule

DISPLAY_BREAKING_FIELDS: frozenset[str] = frozenset(
    {
        "mode",
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
(vrr, reserved, sdr brightness/saturation) stays instant per ADR-0003. A custom modeline
is not a field of its own: it is a `mode` value (`mode = "modeline ..."`, Hyprland has no
`modeline` key), so it rides the breaking lane as `mode` -- a wrong custom modeline is the
most breaking value of all.
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

MODELINE_PREFIX = "modeline "
"""How a `mode` value spells a custom modeline: `"modeline <clock> <h...> <v...> [flags]"`."""

CM_PRESETS: tuple[tuple[str, str], ...] = (
    ("auto", "Automatic"),
    ("srgb", "sRGB"),
    ("dcip3", "DCI-P3"),
    ("dp3", "Display P3"),
    ("adobe", "Adobe RGB"),
    ("wide", "Wide gamut (BT.2020)"),
    ("edid", "From the display (EDID)"),
    ("hdr", "HDR"),
    ("hdredid", "HDR with the display's primaries"),
)
"""The `cm` presets as (value, label). The values are the nine `Hyprland --verify-config`
0.56.2 accepts; any other spelling is "error applying field 'cm'" (checked during #193)."""

SDR_EOTF_NAMES: tuple[tuple[str, str], ...] = (
    ("default", "Follow the global setting"),
    ("auto", "Automatic"),
    ("srgb", "sRGB"),
    ("gamma22", "Gamma 2.2"),
    ("gamma22force", "Gamma 2.2, forced"),
)
"""The `sdr_eotf` transfer functions as (name, label): `NTransferFunction`'s five, by name.
Lua takes names only; the numeric codes were the legacy `monitorv2` spelling."""

LEGACY_SDR_EOTF_CODES: dict[str, str] = {"0": "default", "1": "srgb", "2": "gamma22"}
"""The numeric `sdr_eotf` codes the legacy block accepted, and the transfer-function names
they most likely meant. The Importer converts them and reports the guess."""

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


def mode_sizes(available: Iterable[str]) -> list[tuple[int, int]]:
    """The distinct sizes in an `availableModes` list, in the order the display gives them."""
    sizes: list[tuple[int, int]] = []
    for mode in available:
        parsed = parse_mode(mode)
        if parsed is not None and parsed[:2] not in sizes:
            sizes.append(parsed[:2])
    return sizes


def mode_rates(available: Iterable[str], size: tuple[int, int]) -> list[float]:
    """The refresh rates `availableModes` offers at `size`, highest first."""
    rates = {
        parsed[2]
        for parsed in (parse_mode(mode) for mode in available)
        if parsed is not None and parsed[:2] == size and parsed[2] is not None
    }
    return sorted(rates, reverse=True)


def sdr_eotf_name(value: Any) -> str:
    """An `sdr_eotf` value by name: a legacy numeric code becomes the name it meant."""
    text = str(value).strip()
    return LEGACY_SDR_EOTF_CODES.get(text, text)


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


# --- Confirm-or-revert -----------------------------------------------------------------


def breaks_display(before: Sequence[MonitorRule], after: Sequence[MonitorRule]) -> bool:
    """Whether going from `before` to `after` changes a display-breaking field anywhere.

    What decides that an undo of a monitor step goes behind the countdown (#192): putting
    back a mode is as able to black-screen the session as choosing one.
    """
    return _breaking_by_output(before) != _breaking_by_output(after)


def revert_breaking(
    snapshot: Sequence[MonitorRule], current: Sequence[MonitorRule]
) -> list[MonitorRule]:
    """The rule list a Confirm-or-revert revert writes: the display as it was, nothing more.

    The snapshot's rules, in its order, with its display-breaking values (a breaking field
    the snapshot did not have is removed) and the current benign values, so a vrr or
    reserved-area edit made while the countdown ran survives the revert (#192). A rule
    created since keeps only its benign fields, and goes when it has none. A rule removed
    since comes back whole: removing it changed every breaking field it held.
    """
    now = {rule.output: rule for rule in current}
    before = {rule.output for rule in snapshot}
    reverted: list[MonitorRule] = []
    for rule in snapshot:
        live = now.get(rule.output)
        if live is None:
            reverted.append(rule)
            continue
        fields = {
            key: value if key in DISPLAY_BREAKING_FIELDS else live.fields[key]
            for key, value in rule.fields.items()
            if key in DISPLAY_BREAKING_FIELDS or key in live.fields
        }
        fields.update(
            (key, value)
            for key, value in live.fields.items()
            if key not in DISPLAY_BREAKING_FIELDS and key not in rule.fields
        )
        reverted.append(rule if fields == rule.fields else replace(live, fields=fields))
    for rule in current:
        if rule.output in before:
            continue
        benign = {k: v for k, v in rule.fields.items() if k not in DISPLAY_BREAKING_FIELDS}
        if benign:
            reverted.append(replace(rule, fields=benign))
    return reverted


def _breaking_by_output(rules: Iterable[MonitorRule]) -> dict[str, dict[str, Any]]:
    by_output: dict[str, dict[str, Any]] = {}
    for rule in rules:
        breaking = {k: v for k, v in rule.fields.items() if k in DISPLAY_BREAKING_FIELDS}
        if breaking:
            by_output[rule.output] = breaking
    return by_output


def arrangement_mismatches(
    rules: Sequence[MonitorRule], monitors: Sequence[Mapping[str, Any]]
) -> tuple[str, ...]:
    """Where a live display differs from what its rule asks for, one sentence each (#101).

    The Migration switch's monitor check (ADR-0009). Each connected display answers to its
    own rule, else to the catch-all. Only the fields a rule states as numbers are compared:
    `preferred`, `auto` and the other words leave Hyprland to choose, so there is nothing
    to hold it to. The refresh rate is not compared, because a rule is a request and
    Hyprland picks the closest advertised mode (`format_mode`). Rules for displays that are
    not connected are not a mismatch; nothing is live to differ.

    Reads helper data against rules and keeps neither: nothing here is written back or
    reconciled with the model (ADR-0008).
    """
    catch_all = next((rule for rule in rules if rule.output == CATCH_ALL_OUTPUT), None)
    found: list[str] = []
    for monitor in monitors:
        name = str(monitor.get("name", ""))
        rule = (
            rule_for(rules, connector=name, description=str(monitor.get("description", "")))
            or catch_all
        )
        if rule is not None:
            found.extend(_mismatches_with(rule, name, monitor))
    return tuple(found)


def _mismatches_with(rule: MonitorRule, name: str, monitor: Mapping[str, Any]) -> list[str]:
    fields = rule.fields
    if fields.get("disabled"):
        return [f"{name} is still active, the configuration disables it"]

    found: list[str] = []
    wanted_scale = _number(fields.get("scale"))
    live_scale = _number(monitor.get("scale"))
    if (
        wanted_scale is not None
        and live_scale is not None
        and abs(wanted_scale - live_scale) > _SCALE_TOLERANCE
    ):
        found.append(
            f"{name} is at scale {_trim(live_scale)}, "
            f"the configuration asks for {_trim(wanted_scale)}"
        )

    mode = parse_mode(str(fields.get("mode", "")))
    if mode is not None:
        live_size = (monitor.get("width"), monitor.get("height"))
        if live_size != (mode[0], mode[1]):
            found.append(
                f"{name} runs {live_size[0]}x{live_size[1]}, "
                f"the configuration asks for {mode[0]}x{mode[1]}"
            )

    # A mirror has no position of its own: Hyprland places it on its source.
    position = parse_position(str(fields.get("position", "")))
    if position is not None and not fields.get("mirror"):
        live_at = (monitor.get("x"), monitor.get("y"))
        if live_at != position:
            found.append(
                f"{name} is at position {live_at[0]}, {live_at[1]}, "
                f"the configuration asks for {position[0]}, {position[1]}"
            )

    wanted_transform = _number(fields.get("transform"))
    live_transform = _number(monitor.get("transform"))
    if (
        wanted_transform is not None
        and live_transform is not None
        and wanted_transform != live_transform
    ):
        found.append(
            f"{name}'s rotation is {_transform_name(live_transform)}, "
            f"the configuration asks for {_transform_name(wanted_transform)}"
        )
    return found


def _transform_name(transform: float) -> str:
    """A transform as the word the Monitors page shows for it, never the bare code."""
    index = int(transform)
    if index == transform and 0 <= index < len(TRANSFORM_NAMES):
        return TRANSFORM_NAMES[index].lower()
    return f"transform {_trim(transform)}"


_SCALE_TOLERANCE = 0.01
"""Hyprland rounds a fractional scale to what the output can express (1.566667, not 1.57)."""


def _number(value: object) -> float | None:
    """A rule or IPC value as a number, or `None` for `auto` and friends. Never a bool."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _trim(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


__all__ = [
    "CATCH_ALL_OUTPUT",
    "DISPLAY_BREAKING_FIELDS",
    "SPECIAL_MODES",
    "TRANSFORM_NAMES",
    "arrangement_mismatches",
    "breaks_display",
    "connected_rules",
    "description_of",
    "disconnected_rules",
    "format_mode",
    "format_position",
    "logical_size",
    "parse_mode",
    "parse_position",
    "preferred_identity",
    "revert_breaking",
    "rule_for",
    "rule_matches_output",
    "snap_position",
]
