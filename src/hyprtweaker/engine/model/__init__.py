"""Model: Options (tri-state, Unset means not emitted) and Entities.

The single in-memory truth (ADR-0005). Two halves:

- **Options** (`options.py`) -- every `hl.config` value, tri-state: Unset (not emitted, so
  Hyprland's default applies), set to a value (always emitted, even when it equals the
  default), or set to null (emitted as the Overlay's curated "no value").
- **Value representations** (`values.py`) -- the up-to-three forms one value takes: display
  text, Lua literal, and the `getoption` parse. Gradients and css-gaps are Lua *tables*;
  emitting `descriptions`' text for them produces a config Hyprland rejects.

- **Entities** (`entities.py`) -- every config object with its own `hl.*` constructor:
  Binds, Rules, monitor rules, animations. No Schema describes them, so their shapes are
  declared rather than generated, and they are held in an `EntitySet` per config.

Typical use::

    from hyprtweaker.engine.model import ConfigModel
    from hyprtweaker.engine.schema import load_schema

    model = ConfigModel(load_schema("0.56.2"))
    model.set("decoration:rounding", 10)
    model.set("general:col.active_border", "rgba(33ccffee) rgba(00ff99ee) 45deg")
    model.unset("general:gaps_in")      # back to Hyprland's default
"""

from __future__ import annotations

from .entities import (
    Animation,
    Bind,
    BindDevice,
    BindOptions,
    Curve,
    Device,
    DispatcherCall,
    EntitySet,
    EnvVar,
    Gesture,
    LayerRule,
    MonitorRule,
    Permission,
    PluginLoad,
    StartupCommand,
    Submap,
    Unbind,
    WindowRule,
    WorkspaceRule,
)
from .options import (
    UNSET,
    ConfigModel,
    NotNullable,
    OptionValue,
    UnknownOption,
)
from .values import (
    COMPLEX_TYPES,
    Color,
    ComplexValue,
    CssGaps,
    FontWeight,
    Gradient,
    LuaValue,
    Vec2,
    display_text,
    getoption_raw,
    has_emittable_null,
    lua_literal,
    lua_literal_for,
    lua_string,
    parse_getoption,
    parse_value,
    values_match,
)

__all__ = [
    "COMPLEX_TYPES",
    "UNSET",
    "Animation",
    "Bind",
    "BindDevice",
    "BindOptions",
    "Color",
    "ComplexValue",
    "ConfigModel",
    "CssGaps",
    "Curve",
    "Device",
    "DispatcherCall",
    "EntitySet",
    "EnvVar",
    "FontWeight",
    "Gesture",
    "Gradient",
    "LayerRule",
    "LuaValue",
    "MonitorRule",
    "NotNullable",
    "OptionValue",
    "Permission",
    "PluginLoad",
    "StartupCommand",
    "Submap",
    "Unbind",
    "UnknownOption",
    "Vec2",
    "WindowRule",
    "WorkspaceRule",
    "display_text",
    "getoption_raw",
    "has_emittable_null",
    "lua_literal",
    "lua_literal_for",
    "lua_string",
    "parse_getoption",
    "parse_value",
    "values_match",
]
