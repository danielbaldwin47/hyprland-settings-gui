"""`monitor` / `monitorv2` -> MonitorRule.

The one-line form is four positional fields (`NAME, MODE, POSITION, SCALE`) followed by
free `KEY, VALUE` pairs, plus three shorthands that do something different: `disable`,
`transform, N` and `addreserved, T, B, L, R`. The last two *edit an existing rule* rather
than building a fresh one, which is why `map_monitor` reports whether its result should
merge -- getting that backwards silently discards the mode and position of the line above.

The lossy parts are all shape, not meaning (L18):

- `addreserved` is positional **top, bottom, left, right** in hyprlang and a named
  `{top, right, bottom, left}` table in Lua. The two orders are not the same and swapping
  them looks plausible either way.
- `scale = -1` was hyprlang's "auto"; Lua's parser rejects anything below 0.25, so it has
  to become the word.
- `sdr_eotf` accepted numeric codes only in the `monitorv2` block; Lua takes transfer
  function *names*, and the numeric correspondence is not in the published sources, so the
  conversion is a stated guess rather than a silent one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..model.entities import MonitorRule
from .loss import LossClass, LossCode, LossContext, LossReport
from .scalars import number as _number

__all__ = ["map_monitor", "map_monitor_block"]

_TRAILING_KEYS: frozenset[str] = frozenset(
    ["mirror", "bitdepth", "cm", "sdrsaturation", "sdrbrightness", "transform", "vrr", "icc"]
)

_INT_FIELDS: frozenset[str] = frozenset(
    [
        "bitdepth",
        "transform",
        "vrr",
        "supports_wide_color",
        "supports_hdr",
        "sdr_max_luminance",
        "max_luminance",
        "max_avg_luminance",
    ]
)
_FLOAT_FIELDS: frozenset[str] = frozenset(
    ["sdrsaturation", "sdrbrightness", "sdr_min_luminance", "min_luminance"]
)

#: The numeric `sdr_eotf` codes the legacy block accepted, and the transfer-function names
#: they most likely meant. Reported, never silent -- see the module docstring.
_SDR_EOTF: dict[str, str] = {"0": "default", "1": "srgb", "2": "gamma22"}


def _reserved(values: list[str], notes: LossContext) -> dict[str, int]:
    """`T, B, L, R` (hyprlang order) as Lua's `{top, right, bottom, left}` table."""
    numbers = [int(n) for n in (_number(v) for v in values) if n is not None]
    while len(numbers) < 4:
        numbers.append(0)
    top, bottom, left, right = numbers[:4]
    table = {"top": top, "right": right, "bottom": bottom, "left": left}
    notes.note(
        LossCode.MONITOR_SHAPE,
        "addreserved is positional top,bottom,left,right in hyprlang and named in Lua",
        replacement=str(table),
    )
    return table


def _scale(raw: str, notes: LossContext) -> Any:
    stripped = raw.strip()
    if not stripped or stripped.lower().startswith("auto"):
        return "auto"
    if stripped == "-1":
        notes.note(
            LossCode.MONITOR_SHAPE,
            "scale -1 meant auto; Lua's parser rejects anything below 0.25",
            replacement='scale = "auto"',
        )
        return "auto"
    number = _number(stripped)
    return number if number is not None else stripped


def _mode(raw: str, notes: LossContext) -> str:
    stripped = raw.strip()
    if not stripped:
        return "preferred"
    if "X" in stripped and "x" not in stripped:
        notes.note(
            LossCode.MONITOR_SHAPE,
            f"mode {stripped!r} uses a capital X, which the mode parser does not accept",
            replacement=stripped.replace("X", "x"),
        )
        return stripped.replace("X", "x")
    return stripped


def _typed(key: str, raw: str, notes: LossContext) -> Any:
    if key == "sdr_eotf":
        stripped = raw.strip()
        if stripped in _SDR_EOTF:
            name = _SDR_EOTF[stripped]
            notes.note(
                LossCode.MONITOR_SHAPE,
                f"sdr_eotf = {stripped} is a legacy numeric code; Lua takes a transfer "
                f"function name, read here as {name!r}",
                replacement=f'sdr_eotf = "{name}"',
                loss_class=LossClass.NEEDS_REVIEW,
            )
            return name
        return stripped
    if key in _INT_FIELDS:
        number = _number(raw)
        return int(number) if number is not None else raw.strip()
    if key in _FLOAT_FIELDS:
        number = _number(raw)
        return float(number) if number is not None else raw.strip()
    return raw.strip()


def map_monitor(
    value: str, *, origin: str, report: LossReport
) -> tuple[MonitorRule, bool] | None:
    """One `monitor = ...` line.

    Returns the rule and whether it *merges* into an existing rule for the same output --
    true for the `transform` and `addreserved` shorthands, false for a full line, which
    replaces wholesale.
    """
    notes = LossContext(report, origin, f"monitor = {value}")
    parts = [part.strip() for part in value.split(",")]
    output = parts[0]
    rest = parts[1:]
    if not rest:
        notes.note(LossCode.UNSUPPORTED_KEYWORD, "monitor line has only a name")
        return None

    second = rest[0].lower()
    if second in ("disable", "disabled"):
        return MonitorRule(output=output, fields={"disabled": True}, origin=origin), False
    if second == "transform" and len(rest) > 1:
        number = _number(rest[1])
        transform = {"transform": int(number) if number is not None else rest[1]}
        return MonitorRule(output=output, fields=transform, origin=origin), True
    if second == "addreserved":
        return (
            MonitorRule(
                output=output, fields={"reserved": _reserved(rest[1:], notes)}, origin=origin
            ),
            True,
        )

    fields: dict[str, Any] = {"mode": _mode(rest[0], notes)}
    if len(rest) > 1:
        fields["position"] = rest[1].strip() or "auto"
    if len(rest) > 2:
        fields["scale"] = _scale(rest[2], notes)
    trailing = rest[3:]
    index = 0
    while index < len(trailing):
        key = trailing[index].strip().lower()
        if not key:
            index += 1
            continue
        if key not in _TRAILING_KEYS:
            notes.note(
                LossCode.UNSUPPORTED_KEYWORD,
                f"monitor setting {key!r} is not one this Hyprland accepts on a monitor line",
            )
            index += 2
            continue
        if index + 1 >= len(trailing):
            notes.note(LossCode.UNSUPPORTED_KEYWORD, f"monitor setting {key!r} has no value")
            break
        fields[key] = _typed(key, trailing[index + 1], notes)
        index += 2
    return MonitorRule(output=output, fields=fields, origin=origin), False


def map_monitor_block(
    fields: Mapping[str, str], *, origin: str, report: LossReport
) -> MonitorRule | None:
    """A `monitorv2 { ... }` block. Always merges, as `hl.monitor` itself does."""
    notes = LossContext(report, origin, "monitorv2 { ... }")
    output = fields.get("output", "").strip()
    mapped: dict[str, Any] = {}
    for key, raw in fields.items():
        name = key.strip()
        if name == "output":
            continue
        if name == "addreserved":
            mapped["reserved"] = _reserved([v.strip() for v in raw.split(",")], notes)
        elif name == "mode":
            mapped["mode"] = _mode(raw, notes)
        elif name == "scale":
            mapped["scale"] = _scale(raw, notes)
        elif name in ("disable", "disabled"):
            mapped["disabled"] = raw.strip().lower() in ("1", "true", "yes", "on")
        else:
            mapped[name] = _typed(name, raw, notes)
    return MonitorRule(output=output, fields=mapped, origin=origin)
