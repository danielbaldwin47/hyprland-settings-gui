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

from .generated import GeneratedSchema
from .overlay import Overlay
from .resolve import (
    Schema,
    available_versions,
    derive_title,
    load_schema,
    resolve_option,
    schema_dir,
    select_version,
)
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
    Vec2Range,
    Visibility,
    Widget,
)

__all__ = [
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
    "SectionOverlay",
    "Vec2Range",
    "Visibility",
    "Widget",
    "available_versions",
    "derive_title",
    "load_schema",
    "resolve_option",
    "schema_dir",
    "select_version",
]
