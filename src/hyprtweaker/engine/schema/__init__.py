"""The Schema layer: the Generated schema, the Overlay, and per-Option resolution.

The Schema is the typed, documented, curated description of every Option the UI is
generated from -- one generator, one Row set, 353 options, no per-option code. It comes in
two halves (ADR-0011):

- the **Generated schema**, `data/schema/hyprland-<ver>.json`, machine-produced per
  Hyprland release from `hyprctl -j descriptions` + the Lua stub + the C++ source;
- the **Overlay**, `data/schema/overlay.json`, hand-curated and version-independent, which
  carries everything the machine cannot know: what an option is *called*, whether its
  default is really a sentinel, which int is secretly an enum, what gates what.

Both are committed. Neither is generated at install time.

Typical use::

    from hyprtweaker.engine.schema import load_schema

    schema = load_schema("0.56.2")
    option = schema["input:accel_profile"]
    option.title       # "Acceleration profile"
    option.widget      # Widget.ENUM_STRING
    option.nullable    # True -- [[EMPTY]] means "libinput's own default"
    option.null_label  # "Device default"
"""

from __future__ import annotations

from .diff import SchemaDiff, diff_schemas
from .generated import GeneratedSchema
from .overlay import Overlay
from .resolve import (
    MINIMUM_HYPRLAND,
    Schema,
    available_versions,
    below_lua_floor,
    derive_section_title,
    derive_title,
    humanise,
    load_schema,
    resolve_option,
    schema_dir,
    select_version,
    stamp_added_in,
)
from .supplement import newer_than_shipped, supplement
from .types import (
    CurationFlag,
    Dependency,
    GeneratedOption,
    GetOptionKey,
    KnownValues,
    OptionType,
    OverlayEntry,
    Range,
    ResolvedOption,
    Restart,
    SectionOverlay,
    Supplement,
    SupplementKind,
    Vec2Range,
    Visibility,
    Widget,
)

__all__ = [
    "MINIMUM_HYPRLAND",
    "CurationFlag",
    "Dependency",
    "GeneratedOption",
    "GeneratedSchema",
    "GetOptionKey",
    "KnownValues",
    "OptionType",
    "Overlay",
    "OverlayEntry",
    "Range",
    "ResolvedOption",
    "Restart",
    "Schema",
    "SchemaDiff",
    "SectionOverlay",
    "Supplement",
    "SupplementKind",
    "Vec2Range",
    "Visibility",
    "Widget",
    "available_versions",
    "below_lua_floor",
    "derive_section_title",
    "derive_title",
    "diff_schemas",
    "humanise",
    "load_schema",
    "newer_than_shipped",
    "resolve_option",
    "schema_dir",
    "select_version",
    "stamp_added_in",
    "supplement",
]
