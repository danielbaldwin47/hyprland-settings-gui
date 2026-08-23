"""The vocabulary of the Schema layer: types, widgets, and the three record kinds.

The Schema is two halves (ADR-0011): the **Generated schema**, machine-produced per
Hyprland version, and the **Overlay**, hand-curated and version-independent. Resolution
merges them into a `ResolvedOption`, which is the only shape the rest of the app sees --
nothing above this package ever reads a raw generated record or an overlay entry.

Every enum here is closed on purpose. An unknown widget or type in a schema file is a
generator bug or a hand-edit, and failing to parse it is better than passing an
unrenderable string to the Row factory.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class OptionType(enum.StrEnum):
    """The nine value types every Option resolves to (research #3 §1.1)."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    COLOR = "color"
    GRADIENT = "gradient"
    VEC2 = "vec2"
    CSS_GAPS = "css_gaps"
    FONT_WEIGHT = "font_weight"


class Widget(enum.StrEnum):
    """Every control the Row factory can build for an Option.

    The first block is what type inference (rules R1-R13) produces on its own; the second
    is reachable only by an Overlay `widget` override, because no rule over `descriptions`
    can tell a font name from a locale from a regex.
    """

    # Inferable from descriptions + stub + source.
    TOGGLE = "toggle"
    INT_RANGE = "int-range"
    FREE_INT = "free-int"
    FLOAT_RANGE = "float-range"
    FREE_FLOAT = "free-float"
    ENUM_MAP = "enum-map"
    ENUM_STRING = "enum-string"
    STRING = "string"
    GRADIENT = "gradient"
    VEC2 = "vec2"
    COLOR = "color"
    CSS_GAPS = "css-gaps"
    FONT_WEIGHT = "font-weight"

    # Overlay-only overrides.
    SEGMENTED = "segmented"
    XKB_LAYOUT = "xkb-layout"
    XKB_VARIANT = "xkb-variant"
    XKB_OPTIONS = "xkb-options"
    XKB_MODEL = "xkb-model"
    MONITOR_PICKER = "monitor-picker"
    FONT_PICKER = "font-picker"
    FILE_PICKER = "file-picker"
    REGEX = "regex"
    FLOAT_LIST = "float-list"


class GetOptionKey(enum.StrEnum):
    """Which `hyprctl -j getoption` JSON key carries an Option's value.

    The last three fire only under the Lua config manager. Research #3 swept `getoption`
    under hyprlang, where Gradient/CssGap/FontWeight all come back as `custom`, and
    concluded those branches never fire; prototype #8 caught them firing live on the
    engine this app targets. A reader must still accept `custom` as a fallback for the
    three, but these are the keys to expect.
    """

    INT = "int"
    FLOAT = "float"
    STR = "str"
    VEC2 = "vec2"
    GRADIENT = "gradient"
    CSS = "css"
    FONT_WEIGHT = "font_weight"

    CUSTOM = "custom"
    """The hyprlang answer for the three complex types. Accepted on read, never expected."""


class Visibility(enum.StrEnum):
    """Disclosure tier (ADR-0013).

    `advanced` Rows render in place once the global Advanced switch is on; `hidden` ones
    additionally never appear in the Tasks view. Search indexes all three tiers.
    """

    DEFAULT = "default"
    ADVANCED = "advanced"
    HIDDEN = "hidden"


class Restart(enum.StrEnum):
    """What a changed Option needs before it takes effect, when a reload is not enough.

    Wiki prose only -- nothing in source or IPC exports this, so it is hand-verified
    Overlay data (research #3 §5).
    """

    HYPRLAND = "hyprland"
    MONITOR_RELOAD = "monitor-reload"
    XWAYLAND = "xwayland"


class CurationFlag(enum.StrEnum):
    """A generator-raised demand for hand curation.

    Each flag names a defect the Generated schema can *detect* but not *fix*: the data
    needed is in wiki prose, in a human's head, or nowhere. The Overlay completeness test
    turns every flag into a required Overlay field, which is what makes an uncurated
    option fail the build rather than render a falsehood (prototype #8).
    """

    NEEDS_NULLABLE = "needs-nullable"
    """Default is a sentinel ([[EMPTY]], [[Auto]], -1): needs `nullable` + `null_label`."""

    NEEDS_LABELS = "needs-labels"
    """A map-less small-range int -- really an enum. Needs `labels`."""

    NEEDS_KNOWN_VALUES = "needs-known-values"
    """A string whose legal values live in prose or `strChoice`. Needs `known_values`."""

    NEEDS_RANGE = "needs-range"
    """Bounds exist only in source (`vec2Range`) or are absent/INT_MAX. Needs `range`."""

    NEEDS_WIDGET = "needs-widget"
    """A free-text string that is really a picker (font, monitor, file, regex, xkb)."""


@dataclass(frozen=True, slots=True)
class Range:
    """Numeric bounds for a scalar Option.

    `soft_max` exists for the INT_MAX cases: the spin button stops being useful long
    before the type does, so the Overlay caps the slider without forbidding the value.
    """

    min: float | None = None
    max: float | None = None
    step: float | None = None
    soft_max: float | None = None


@dataclass(frozen=True, slots=True)
class Vec2Range:
    """The `vec2Range(minX, minY, maxX, maxY)` validator from ConfigValues.hpp."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float


@dataclass(frozen=True, slots=True)
class KnownValues:
    """The value list for a string Option.

    `open` is the difference between a combo and an editable combo: `general:layout`
    accepts `lua:<name>` for any user-registered layout, so its list can never be closed.
    """

    values: tuple[str, ...]
    open: bool = False


@dataclass(frozen=True, slots=True)
class Dependency:
    """`depends_on`: the Option whose value gates this one.

    The Row stays visible either way -- only its control goes insensitive, behind a
    "Requires <title>" badge that navigates to the controlling Row (ADR-0013).
    """

    option: str
    value: Any


@dataclass(frozen=True, slots=True)
class GeneratedOption:
    """One machine-derived Option record: the per-version half of the Schema.

    Everything here comes from `hyprctl -j descriptions`, the Lua stub, and the Hyprland
    source at the matching tag, with no human in the loop. Regenerating it per release and
    diffing is the whole point of the release-check protocol (ADR-0012).
    """

    name: str
    """Colon form -- the hyprctl/hyprlang key, e.g. `input:touchpad:tap-to-click`."""

    lua_key: str
    """Dot form -- the `HL.ConfigKey` the writer emits, e.g. `input.touchpad.tap_to_click`."""

    section: str
    path: tuple[str, ...]
    order: int
    """Declaration order in ConfigValues.cpp: upstream's own grouping intent, for free."""

    type: OptionType
    widget: Widget
    description: str
    default: Any
    """Normalised and typed. `None` when `sentinel_default` -- the sentinel is not a value."""

    default_raw: Any
    """Exactly what `descriptions` printed, sentinels and all."""

    sentinel_default: bool
    getoption_key: GetOptionKey

    min: float | None = None
    max: float | None = None
    map: dict[str, int] | None = None
    choices: tuple[str, ...] | None = None
    vec2_range: Vec2Range | None = None
    device_overridable: bool = False
    refresh: tuple[str, ...] = ()
    curation_flags: tuple[CurationFlag, ...] = ()


@dataclass(frozen=True, slots=True)
class OverlayEntry:
    """The hand-curated half for one Option. Every field overrides the generated record."""

    title: str | None = None
    help: str | None = None
    help_url: str | None = None
    widget: Widget | None = None
    labels: dict[str, str] | None = None
    known_values: KnownValues | None = None
    nullable: bool | None = None
    null_label: str | None = None
    null_value: Any = None
    range: Range | None = None
    unit: str | None = None
    depends_on: Dependency | None = None
    restart: Restart | None = None
    visibility: Visibility | None = None
    group: str | None = None
    order: int | None = None
    """Position *within* `group`. Never the Option's position in its Section -- that is
    Hyprland's own declaration order, which no curation should silently rewrite."""

    deprecated_in: str | None = None
    """Set by a release check when a Hyprland release removes the Option.

    The Overlay is version-independent and entries outlive the schemas they describe, so
    a removed Option's entry stays and keeps serving older schemas in the support window
    (ADR-0012; `docs/agents/hyprland-release-check.md` §3)."""

    renamed_from: str | None = None
    """The Option's previous name, so a rename migrates the user's value silently."""


@dataclass(frozen=True, slots=True)
class SectionOverlay:
    """Per-Section curation: a title, a wiki anchor, and a visibility floor.

    The floor is how `debug`/`quirks`/`experimental`/`input-capture` become hidden without
    27 repeated per-option entries -- and it stays data, so a new dangerous section is an
    Overlay edit, not a code change.
    """

    title: str | None = None
    help_url: str | None = None
    visibility: Visibility | None = None


@dataclass(frozen=True, slots=True)
class ResolvedOption:
    """Generated plus Overlay: the only Option shape the app above this package sees.

    Every field is decided -- `widget`, `title` and `nullable` can never be `None`, which
    is exactly the guarantee the Overlay completeness test enforces at build time.
    """

    name: str
    lua_key: str
    section: str
    path: tuple[str, ...]
    order: int
    """Hyprland's own declaration order, which orders Sections and the Config view."""

    type: OptionType
    widget: Widget
    title: str
    description: str
    """The Row subtitle: curated `help` when present, else the generated description."""

    default: Any
    default_raw: Any
    nullable: bool
    null_label: str | None
    null_value: Any
    getoption_key: GetOptionKey
    visibility: Visibility
    range: Range | None = None
    map: dict[str, int] | None = None
    labels: dict[str, str] | None = None
    known_values: KnownValues | None = None
    vec2_range: Vec2Range | None = None
    unit: str | None = None
    depends_on: Dependency | None = None
    restart: Restart | None = None
    help_url: str | None = None
    group: str | None = None
    group_order: int | None = None
    """Position within `group`, kept separate from `order` so curating a Page's shape can
    never reorder a Section."""

    device_overridable: bool = False
    refresh: tuple[str, ...] = ()
    curation_flags: tuple[CurationFlag, ...] = field(default=())

    @property
    def dotted_key(self) -> str:
        """The key shown in the Help popover and indexed by Search (ADR-0013)."""
        return self.lua_key
