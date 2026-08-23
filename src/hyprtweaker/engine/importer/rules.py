"""`windowrule` / `layerrule` / `workspace` keywords -> Rule entities.

Window and layer rules share one grammar in both engines, so they share one reader here.
The v3 keyword form is a comma-separated list of `NAME VALUE` elements where the name is
either `match:<prop>` or an effect; the block form is the same pairs as a keyed category.
What differs between the engines is *types*, not names: hyprlang took every value as a
string and applied `truthy()` where it wanted a bool, while Lua's typed descriptors reject
`"on"` for a bool field and reject a `rounding` above 20 outright. So the work here is
retyping (L14), plus three name-level fixes:

- `ignorezero` is gone from this Hyprland; `ignore_alpha = 0` is what it meant (L17).
- Workspace `border`/`shadow`/`rounding` invert into `no_border`/`no_shadow`/
  `no_rounding` -- a rule that says `border:false` becomes one that says `no_border = true`
  (L16), and getting the polarity wrong is invisible until a window has the wrong border.
- Pre-0.54 `windowrule = float, ^(kitty)$` and any `windowrulev2` are not accepted by this
  Hyprland *at all*, and no published table renames them, so they are Breakage rather than
  a best-effort guess (L13).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..model.entities import LayerRule, WindowRule, WorkspaceRule
from .loss import LossClass, LossCode, LossContext, LossReport
from .scalars import number as _number
from .scalars import truthy as _truthy

__all__ = [
    "map_layer_rule",
    "map_rule_block",
    "map_window_rule",
    "map_workspace_rule",
]

_BOOL_EFFECTS: frozenset[str] = frozenset(
    [
        "float",
        "tile",
        "fullscreen",
        "maximize",
        "center",
        "pseudo",
        "no_initial_focus",
        "pin",
        "persistent_size",
        "allows_input",
        "dim_around",
        "decorate",
        "focus_on_activate",
        "keep_aspect_ratio",
        "nearest_neighbor",
        "no_anim",
        "no_blur",
        "no_dim",
        "no_focus",
        "no_follow_mouse",
        "no_max_size",
        "no_shadow",
        "no_shortcuts_inhibit",
        "opaque",
        "force_rgbx",
        "sync_fullscreen",
        "immediate",
        "xray",
        "render_unfocused",
        "no_screen_share",
        "no_vrr",
        "no_auto_hdr",
        "confine_pointer",
        "stay_focused",
    ]
)

_INT_EFFECTS: dict[str, tuple[int, int] | None] = {
    "no_close_for": None,
    "border_size": None,
    "rounding": (0, 20),
}

_FLOAT_EFFECTS: dict[str, tuple[float, float] | None] = {
    "scrolling_width": None,
    "rounding_power": (1.0, 10.0),
    "scroll_mouse": (0.01, 10.0),
    "scroll_touchpad": (0.01, 10.0),
}

#: Effects that stay strings even when they look numeric -- `opacity 0.8` is a *grammar*
#: (`a [override] [b ...]`), not a number, and passing a Lua number is a type error.
_STRING_EFFECTS: frozenset[str] = frozenset(
    [
        "fullscreen_state",
        "monitor",
        "workspace",
        "group",
        "suppress_event",
        "content",
        "animation",
        "idle_inhibit",
        "opacity",
        "tag",
        "tonemap",
        "move",
        "size",
        "max_size",
        "min_size",
        "border_color",
    ]
)

_BOOL_MATCH: frozenset[str] = frozenset(
    ["xwayland", "float", "fullscreen", "pin", "focus", "group", "modal"]
)
_INT_MATCH: frozenset[str] = frozenset(["fullscreen_state_internal", "fullscreen_state_client"])

#: Rule names that existed before 0.56.2 and are in neither engine now. Emitting them would
#: raise `unknown effect`, so they are dropped with a finding instead.
_RETIRED_EFFECTS: frozenset[str] = frozenset(
    [
        "noscreenshare",
        "nomaxsize",
        "noinitialfocus",
        "windowdance",
        "prop",
        "unset",
        "bordercolor",
        "idleinhibit",
        "suppressevent",
        "keepaspectratio",
        "focusonactivate",
        "forcergbx",
        "syncfullscreen",
        "nearestneighbor",
        "renderunfocused",
        "noclosefor",
        "persistentsize",
        "scrollmouse",
        "scrolltouchpad",
        "dimaround",
        "noshortcutsinhibit",
        "fullscreenstate",
        "maxsize",
        "minsize",
        "noborder",
        "nodim",
        "noblur",
        "noanim",
        "noshadow",
        "nofocus",
        "stayfocused",
        "allowsinput",
        "no_wobble",
        "no_xdg_drags",
    ]
)

_LAYER_BOOL: frozenset[str] = frozenset(
    ["no_anim", "blur", "blur_popups", "dim_around", "xray", "no_screen_share"]
)
_LAYER_INT: dict[str, tuple[int, int] | None] = {"order": None, "above_lock": (0, 2)}

_WORKSPACE_FIELDS: dict[str, str] = {
    "monitor": "monitor",
    "default": "default",
    "persistent": "persistent",
    "gapsin": "gaps_in",
    "gapsout": "gaps_out",
    "bordersize": "border_size",
    "decorate": "decorate",
    "defaultName": "default_name",
    "on-created-empty": "on_created_empty",
    "layout": "layout",
    "animation": "animation",
}
_WORKSPACE_BOOL: frozenset[str] = frozenset({"default", "persistent", "decorate"})
#: The three that invert: hyprlang stored `!value` (L16).
_WORKSPACE_INVERTED: dict[str, str] = {
    "border": "no_border",
    "shadow": "no_shadow",
    "rounding": "no_rounding",
}


def _looks_bool(text: str) -> bool:
    return text.strip().lower() in (
        "0",
        "1",
        "true",
        "false",
        "yes",
        "no",
        "on",
        "off",
    )


def _effect_value(name: str, raw: str, notes: LossContext) -> Any:
    """Retype one window-rule effect value for Lua, reporting what changed."""
    if name in _BOOL_EFFECTS:
        value = _truthy(raw) if raw.strip() else True
        if raw.strip() and not _looks_bool(raw):
            notes.note(
                LossCode.RULE_VALUE_TYPE,
                f"{name} takes a boolean in Lua; read {raw.strip()!r} as {value}",
                replacement=f"{name} = {str(value).lower()}",
            )
        elif raw.strip().lower() not in ("true", "false", ""):
            notes.note(
                LossCode.VALUE_NORMALISED,
                f"{name} = {raw.strip()!r} normalised to a Lua boolean",
                replacement=f"{name} = {str(value).lower()}",
            )
        return value
    if name in _INT_EFFECTS:
        number = _number(raw)
        if number is None:
            notes.note(
                LossCode.RULE_VALUE_TYPE, f"{name} expects a number, got {raw.strip()!r}"
            )
            return raw.strip()
        number = int(number)
        bounds = _INT_EFFECTS[name]
        if bounds is not None and not bounds[0] <= number <= bounds[1]:
            notes.note(
                LossCode.RULE_VALUE_TYPE,
                f"{name} = {number} is outside the {bounds[0]}..{bounds[1]} range Lua "
                "accepts and would be rejected",
                loss_class=LossClass.BREAKAGE,
            )
        return number
    if name in _FLOAT_EFFECTS:
        number = _number(raw)
        if number is None:
            notes.note(
                LossCode.RULE_VALUE_TYPE, f"{name} expects a number, got {raw.strip()!r}"
            )
            return raw.strip()
        span = _FLOAT_EFFECTS[name]
        if span is not None and not span[0] <= float(number) <= span[1]:
            notes.note(
                LossCode.RULE_VALUE_TYPE,
                f"{name} = {number} is outside the {span[0]}..{span[1]} range Lua "
                "accepts and would be rejected",
                loss_class=LossClass.BREAKAGE,
            )
        return float(number)
    if name not in _STRING_EFFECTS:
        # Not in this Hyprland's effect table at all. Lua stringifies unknown keys and
        # hands them to the dynamic effect registry, which is how plugin rules work -- so
        # it is passed through, but the report says so rather than implying it was typed.
        notes.note(
            LossCode.RULE_VALUE_TYPE,
            f"{name!r} is not a built-in rule effect; passed through as a raw value for "
            "the dynamic or plugin effect registry",
        )
    return raw.strip()


def _match_value(name: str, raw: str, notes: LossContext) -> Any:
    if name in _BOOL_MATCH:
        value = _truthy(raw) if raw.strip() else True
        if raw.strip() and not _looks_bool(raw):
            notes.note(
                LossCode.RULE_VALUE_TYPE,
                f"match:{name} takes a boolean in Lua; read {raw.strip()!r} as {value}",
            )
        return value
    if name in _INT_MATCH:
        number = _number(raw)
        return int(number) if number is not None else raw.strip()
    return raw.strip()


def _pairs_from_keyword(value: str, notes: LossContext) -> list[tuple[str, str]] | None:
    """Split a v3 rule keyword value into `(name, value)` pairs.

    Every element must contain a space -- that is the check that distinguishes a v3 rule
    from a pre-0.54 one, and it is the same check hyprlang makes.
    """
    pairs: list[tuple[str, str]] = []
    for element in value.split(","):
        stripped = element.strip()
        if not stripped:
            continue
        name, sep, raw = stripped.partition(" ")
        if not sep:
            notes.note(
                LossCode.OLD_WINDOWRULE_SYNTAX,
                f"element {stripped!r} has no value, which is the pre-0.54 rule syntax; "
                "this Hyprland rejects it and no published table renames it",
            )
            return None
        pairs.append((name, raw))
    return pairs


def _split_rule_pairs(
    pairs: Iterable[tuple[str, str]], notes: LossContext, *, layer: bool
) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
    """Sort `(name, value)` pairs into match props, effects, the name and enabled."""
    match: dict[str, Any] = {}
    effects: dict[str, Any] = {}
    rule_name = ""
    enabled = True
    for raw_name, raw_value in pairs:
        name = raw_name.strip()
        if name == "name":
            rule_name = raw_value.strip()
            continue
        if name in ("enable", "enabled"):
            enabled = _truthy(raw_value)
            continue
        if name.startswith("match:"):
            prop = name[len("match:") :]
            match[prop] = _match_value(prop, raw_value, notes)
            continue
        if layer:
            effects.update(_layer_effect(name, raw_value, notes))
            continue
        if name in _RETIRED_EFFECTS:
            notes.note(
                LossCode.WIKI_DRIFT,
                f"rule effect {name!r} does not exist in this Hyprland and was dropped",
            )
            continue
        effects[name] = _effect_value(name, raw_value, notes)
    return match, effects, rule_name, enabled


def _layer_effect(name: str, raw: str, notes: LossContext) -> dict[str, Any]:
    if name == "ignorezero":
        notes.note(
            LossCode.LAYERRULE_DROPPED,
            "'ignorezero' is gone from this Hyprland; ignore_alpha = 0 is what it meant",
            replacement="ignore_alpha = 0",
        )
        return {"ignore_alpha": 0}
    if name == "unset":
        notes.note(
            LossCode.LAYERRULE_DROPPED,
            "'unset' is not a layer rule in this Hyprland; the effect is simply omitted",
        )
        return {}
    if name in _LAYER_BOOL:
        return {name: _truthy(raw) if raw.strip() else True}
    if name in _LAYER_INT:
        number = _number(raw)
        if number is None:
            return {name: raw.strip()}
        number = int(number)
        bounds = _LAYER_INT[name]
        if bounds is not None and not bounds[0] <= number <= bounds[1]:
            notes.note(
                LossCode.RULE_VALUE_TYPE,
                f"{name} = {number} is outside the {bounds[0]}..{bounds[1]} range Lua accepts",
                loss_class=LossClass.BREAKAGE,
            )
        return {name: number}
    if name == "ignore_alpha":
        number = _number(raw)
        return {name: float(number) if number is not None else raw.strip()}
    return {name: raw.strip()}


def map_window_rule(value: str, *, origin: str, report: LossReport) -> WindowRule | None:
    """`windowrule = match:class foo, border_size 10`."""
    notes = LossContext(report, origin, f"windowrule = {value}")
    pairs = _pairs_from_keyword(value, notes)
    if pairs is None:
        return None
    match, effects, name, enabled = _split_rule_pairs(pairs, notes, layer=False)
    if not match:
        notes.note(
            LossCode.UNSUPPORTED_KEYWORD, "window rule has no match props and matches nothing"
        )
    return WindowRule(match=match, effects=effects, name=name, enabled=enabled, origin=origin)


def map_layer_rule(value: str, *, origin: str, report: LossReport) -> LayerRule | None:
    """`layerrule = blur on, match:namespace waybar`."""
    notes = LossContext(report, origin, f"layerrule = {value}")
    pairs = _pairs_from_keyword(value, notes)
    if pairs is None:
        return None
    match, effects, name, enabled = _split_rule_pairs(pairs, notes, layer=True)
    return LayerRule(match=match, effects=effects, name=name, enabled=enabled, origin=origin)


def map_rule_block(
    fields: Mapping[str, str], *, origin: str, report: LossReport, layer: bool
) -> WindowRule | LayerRule:
    """The block form: `windowrule { name = x; match:class = y; border_size = 10 }`.

    Same pairs as the keyword form, already split by the grammar core -- so it shares the
    sorting and retyping and differs only in where the pairs came from.
    """
    kind = "layerrule" if layer else "windowrule"
    notes = LossContext(report, origin, f"{kind} {{ ... }}")
    match, effects, name, enabled = _split_rule_pairs(fields.items(), notes, layer=layer)
    cls = LayerRule if layer else WindowRule
    return cls(match=match, effects=effects, name=name, enabled=enabled, origin=origin)


def map_workspace_rule(value: str, *, origin: str, report: LossReport) -> WorkspaceRule | None:
    """`workspace = SELECTOR, rule:value, ...`.

    The first comma field is the selector; every later one is `key:value`, except
    `layoutopt:KEY:VAL` which collects into a single `layout_opts` table.
    """
    notes = LossContext(report, origin, f"workspace = {value}")
    parts = value.split(",")
    selector = parts[0].strip()
    if not selector:
        notes.note(LossCode.UNSUPPORTED_KEYWORD, "workspace rule has no selector")
        return None
    fields: dict[str, Any] = {}
    layout_opts: dict[str, str] = {}
    for part in parts[1:]:
        stripped = part.strip()
        if not stripped:
            continue
        key, sep, raw = stripped.partition(":")
        if not sep:
            notes.note(
                LossCode.UNSUPPORTED_KEYWORD,
                f"workspace rule element {stripped!r} is not key:value",
            )
            continue
        key = key.strip()
        if key == "layoutopt":
            opt_key, _, opt_value = raw.partition(":")
            layout_opts[opt_key.strip()] = opt_value.strip()
            continue
        if key in _WORKSPACE_INVERTED:
            target = _WORKSPACE_INVERTED[key]
            fields[target] = not _truthy(raw)
            notes.note(
                LossCode.WORKSPACE_INVERTED,
                f"{key}:{raw.strip()} inverted to {target} = {str(fields[target]).lower()}",
                replacement=f"{target} = {str(fields[target]).lower()}",
            )
            continue
        mapped = _WORKSPACE_FIELDS.get(key)
        if mapped is None:
            notes.note(
                LossCode.UNSUPPORTED_KEYWORD,
                f"workspace rule {key!r} has no field in this Hyprland and was dropped",
            )
            continue
        target = mapped
        if target in ("gaps_in", "gaps_out"):
            fields[target] = _css_gaps(raw, notes, target)
        elif key in _WORKSPACE_BOOL:
            fields[target] = _truthy(raw)
        elif target == "border_size":
            number = _number(raw)
            fields[target] = int(number) if number is not None else raw.strip()
        else:
            fields[target] = raw.strip()
    if layout_opts:
        fields["layout_opts"] = layout_opts
    return WorkspaceRule(workspace=selector, fields=fields, origin=origin)


def _css_gaps(raw: str, notes: LossContext, name: str) -> Any:
    """CSS shorthand to Lua's explicit four sides.

    Lua takes an integer (all sides) or a full `{top, right, bottom, left}` table -- the
    2- and 3-value CSS shorthands have to be expanded here or they silently become zeros
    (L24).
    """
    numbers = [_number(token) for token in raw.replace(",", " ").split()]
    values = [int(n) for n in numbers if n is not None]
    if not values:
        return raw.strip()
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        top, right = values
        expanded = {"top": top, "right": right, "bottom": top, "left": right}
    elif len(values) == 3:
        top, right, bottom = values
        expanded = {"top": top, "right": right, "bottom": bottom, "left": right}
    else:
        top, right, bottom, left = values[:4]
        expanded = {"top": top, "right": right, "bottom": bottom, "left": left}
    if len(values) in (2, 3):
        notes.note(
            LossCode.VALUE_NORMALISED,
            f"{name} CSS shorthand expanded to all four sides",
            replacement=str(expanded),
        )
    return expanded
