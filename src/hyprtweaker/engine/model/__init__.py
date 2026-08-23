"""Model: Options (tri-state, Unset means not emitted) and Entities.

The single in-memory truth (ADR-0005). Two halves:

- **Options** (`options.py`) -- every `hl.config` value, tri-state: Unset (not emitted, so
  Hyprland's default applies), set to a value (always emitted, even when it equals the
  default), or set to null (emitted as the Overlay's curated "no value").
- **Value representations** (`values.py`) -- the up-to-three forms one value takes: display
  text, Lua literal, and the `getoption` parse. Gradients and css-gaps are Lua *tables*;
  emitting `descriptions`' text for them produces a config Hyprland rejects.

Entities (Binds, Rules, monitors, ...) join this package in #64 and later.

Typical use::

    from hyprtweaker.engine.model import ConfigModel
    from hyprtweaker.engine.schema import load_schema

    model = ConfigModel(load_schema("0.56.2"))
    model.set("decoration:rounding", 10)
    model.set("general:col.active_border", "rgba(33ccffee) rgba(00ff99ee) 45deg")
    model.unset("general:gaps_in")      # back to Hyprland's default
"""

from __future__ import annotations

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
    has_emittable_null,
    lua_literal,
    lua_literal_for,
    lua_string,
    parse_getoption,
    parse_value,
)

__all__ = [
    "COMPLEX_TYPES",
    "UNSET",
    "Color",
    "ComplexValue",
    "ConfigModel",
    "CssGaps",
    "FontWeight",
    "Gradient",
    "LuaValue",
    "NotNullable",
    "OptionValue",
    "UnknownOption",
    "Vec2",
    "display_text",
    "has_emittable_null",
    "lua_literal",
    "lua_literal_for",
    "lua_string",
    "parse_getoption",
    "parse_value",
]
