"""The typed field catalogue for the remaining declarative Entity kinds (#70).

Curves, animations, gestures, per-device overrides, environment variables, autostart
commands and permissions are the half of the config surface that is neither an Option nor
an ordered rule list. They have no Generated schema and never will: `hyprctl descriptions`
describes `hl.config` values, and none of these are one. Their shapes come from the Lua
API instead, so they are declared here by hand exactly the way `rules_catalog` and
`dispatchers` declare theirs -- a catalogue is the honest home for "what the compositor's
C++ parser accepts", and putting it beside them keeps one answer to "where do entity field
names live" (`model/entities.py` docstring makes the same point for the dataclasses).

Every constant below cites the research doc that established it
(`docs/research/lua-api-surface.md` §10-15, `docs/research/hyprlang-to-lua.md` §2.10), so a
Release check has one file to re-verify rather than a grep across the writer and the UI.

The catalogue is deliberately *descriptive, not prescriptive*: `FieldSpec` says what a
field is for so a widget can be picked and a value coerced, and `validate_*` says what
Hyprland would reject. Neither is a gate. An unknown key or an out-of-range number is
surfaced (the Page badges it, the Loss report names it) and still written, because the
alternative -- dropping what the app does not recognise -- is the failure mode ADR-0008
forbids for rule effects and this surface has no better claim to omniscience.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .model.entities import Animation, Curve, Device, EntitySet, EnvVar, Gesture

# --- field descriptions ------------------------------------------------------------------


class FieldType(enum.Enum):
    """What a field holds -- enough for a widget choice and a coercion, no more."""

    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    STRING = "string"
    ENUM = "enum"
    VEC2 = "vec2"
    CURVE_REF = "curve_ref"
    """A curve *name*: rendered as a string, but picked from the declared curves."""


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One editable field on an Entity: its Lua key, its type, and its bounds."""

    name: str
    type: FieldType
    title: str = ""
    required: bool = False
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    default: float | None = None
    """What a numeric field opens at, when its lower bound is not a legal value.

    A spin row must hold *some* number, and taking the bound is wrong wherever the bound is
    exclusive: `speed` is `0 < x <= 100`, so a row that opened at 0 wrote a value the
    compositor refuses ("speed must be greater than 0") for anyone who accepted the
    default. Only set where the bound would lie.
    """

    help: str = ""

    @property
    def label(self) -> str:
        """The title if one was curated, else the Lua key made readable."""
        return self.title or self.name.replace("_", " ").capitalize()


def _spec_map(specs: Iterable[FieldSpec]) -> dict[str, FieldSpec]:
    return {spec.name: spec for spec in specs}


# --- curves (`lua-api-surface.md` §10) ----------------------------------------------------

BUILTIN_CURVES: tuple[str, ...] = ("default", "linear")
"""Curves Hyprland registers itself, referenceable without an `hl.curve` declaration.

`lua-api-surface.md`:153 -- the upstream example config references `bezier = "default"`
before declaring any curve of that name. A reference to one of these is not dangling.
"""

CURVE_TYPES: tuple[str, ...] = ("bezier", "spring")

CURVE_POINT_MIN = -1.0
CURVE_POINT_MAX = 2.0
"""Bezier control-point coordinates are clamped to -1..2 by the Lua parser (CR:323).

Legacy hyprlang accepted any float, which is why an imported rice can arrive holding a
point this range rejects -- an L19-shaped loss the Page has to be able to show.
"""

SPRING_MIN = 0.5
"""`mass`, `stiffness` and `dampening` must each exceed this (CR:342-386)."""

SPRING_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("mass", FieldType.FLOAT, "Mass", required=True, minimum=SPRING_MIN, default=1.0),
    FieldSpec(
        "stiffness",
        FieldType.FLOAT,
        "Stiffness",
        required=True,
        minimum=SPRING_MIN,
        default=100.0,
    ),
    FieldSpec(
        "dampening",
        FieldType.FLOAT,
        "Dampening",
        required=True,
        minimum=SPRING_MIN,
        default=10.0,
    ),
)


# --- animations (`lua-api-surface.md` §10) ------------------------------------------------

ANIMATION_LEAVES: tuple[str, ...] = (
    "global",
    "windows",
    "windowsIn",
    "windowsOut",
    "windowsMove",
    "layers",
    "layersIn",
    "layersOut",
    "fade",
    "fadeIn",
    "fadeOut",
    "fadeSwitch",
    "fadeShadow",
    "fadeDim",
    "fadeGlow",
    "fadeDpms",
    "fadeLayers",
    "fadeLayersIn",
    "fadeLayersOut",
    "fadePopups",
    "fadePopupsIn",
    "fadePopupsOut",
    "border",
    "borderangle",
    "glowangle",
    "shadowangle",
    "workspaces",
    "workspacesIn",
    "workspacesOut",
    "specialWorkspace",
    "specialWorkspaceIn",
    "specialWorkspaceOut",
    "monitorAdded",
    "zoomFactor",
)
"""The animation tree's leaf names, as `hyprctl -j animations` reports them on 0.56.2.

Version-dependent like the Generated schema, and shipped statically for the same reason
the schemas are: the app has to render the Page before it has spoken to a compositor, and
on a machine that is not running Hyprland at all. Unknown leaves are accepted rather than
rejected (`unknown_leaves`), so a newer release adding one degrades to "shown, flagged"
instead of "silently unwritable" -- the ADR-0012 rule for Options, applied here.

`__internal_fadeCTM` is deliberately absent: it is an implementation detail the compositor
reports but no config should name.
"""

ANIMATION_SPEED_MIN = 0.0
ANIMATION_SPEED_MAX = 100.0
"""`speed` is `0 < x <= 100` (CR:417-426); legacy hyprlang was unbounded.

The lower bound is **exclusive** -- probed: `speed = 0` is "speed must be greater than 0".
That is why the field carries a `default`, and why `animation_findings` tests `<` rather
than `<=` at this end.
"""

ANIMATION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "enabled",
        FieldType.BOOL,
        "Enabled",
        required=True,
        help="Off is the same as the legacy `animation = leaf, 0`.",
    ),
    FieldSpec(
        "speed",
        FieldType.FLOAT,
        "Speed",
        minimum=ANIMATION_SPEED_MIN,
        maximum=ANIMATION_SPEED_MAX,
        default=1.0,
        help="In tenths of a second: higher is slower.",
    ),
    FieldSpec("bezier", FieldType.CURVE_REF, "Bezier curve"),
    FieldSpec("spring", FieldType.CURVE_REF, "Spring curve"),
    FieldSpec("style", FieldType.STRING, "Style"),
)

ANIMATION_CURVE_KEYS: tuple[str, ...] = ("bezier", "spring")
"""The two spellings of "which curve". Exactly one is required unless `enabled` is false.

`curve =` is *not* among them: the wiki's examples use it and the parser does not accept
it (`lua-api-surface.md`:250, Q4), which is the single most likely way a hand-written
animation fails to load.
"""

ANIMATION_FIELD_SPECS: dict[str, FieldSpec] = _spec_map(ANIMATION_FIELDS)


# --- gestures (`lua-api-surface.md` §11) --------------------------------------------------

GESTURE_DIRECTIONS: tuple[str, ...] = (
    "swipe",
    "horizontal",
    "vertical",
    "left",
    "right",
    "up",
    "down",
    "pinch",
    "pinchin",
    "pinchout",
)

UNSET_ACTION = "unset"
"""The delete primitive `hl.gesture` offers, and the one action the app never writes.

Probed: `hl.gesture{direction="horizontal", action="unset"}` on its own is an error --
"Can't remove a non-existent gesture". A generated Module is replayed from empty on every
reload (`lua-api-surface.md` §0, Implication 1), so there is never a previous gesture for
it to remove, and offering it as a choice would let someone build a row whose only effect
is to break the file. Accepted on *import*, because a hand-written config may use it
against a gesture the app cannot see; never offered as a choice.
"""

GESTURE_ACTIONS: tuple[str, ...] = (
    "workspace",
    "resize",
    "move",
    "special",
    "close",
    "float",
    "fullscreen",
    "cursor_zoom",
    "scroll_move",
)
"""The string actions the app offers. A Lua-function action is script, not config.

Such a gesture is listed read-only rather than edited, the way a function-valued Bind
action is (ADR-0007) -- `is_scripted` is the test.
"""

GESTURE_FINGERS_MIN = 2
GESTURE_FINGERS_MAX = 9
GESTURE_SCALE_MIN = 0.1
GESTURE_SCALE_MAX = 10.0

GESTURE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "fingers",
        FieldType.INT,
        "Fingers",
        required=True,
        minimum=GESTURE_FINGERS_MIN,
        maximum=GESTURE_FINGERS_MAX,
    ),
    FieldSpec(
        "direction",
        FieldType.ENUM,
        "Direction",
        required=True,
        choices=GESTURE_DIRECTIONS,
    ),
    FieldSpec("action", FieldType.ENUM, "Action", required=True, choices=GESTURE_ACTIONS),
    FieldSpec("mods", FieldType.STRING, "Modifiers", help="For example SUPER or ALT SHIFT."),
    FieldSpec(
        "scale",
        FieldType.FLOAT,
        "Scale",
        minimum=GESTURE_SCALE_MIN,
        maximum=GESTURE_SCALE_MAX,
    ),
    FieldSpec(
        "workspace_name",
        FieldType.STRING,
        "Workspace",
        help="Which special workspace the “special” action targets.",
    ),
    FieldSpec(
        "mode",
        FieldType.STRING,
        "Mode",
        help="The action's variant: tile, maximize, mult, live.",
    ),
    FieldSpec("zoom_level", FieldType.FLOAT, "Zoom level"),
    FieldSpec("disable_inhibit", FieldType.BOOL, "Ignore inhibitors"),
)

GESTURE_FIELD_SPECS: dict[str, FieldSpec] = _spec_map(GESTURE_FIELDS)


GESTURE_DIRECTION_COVERS: dict[str, frozenset[str]] = {
    "swipe": frozenset({"swipe", "horizontal", "vertical", "left", "right", "up", "down"}),
    "horizontal": frozenset({"horizontal", "left", "right"}),
    "vertical": frozenset({"vertical", "up", "down"}),
    "pinch": frozenset({"pinch", "pinchin", "pinchout"}),
    "left": frozenset({"left"}),
    "right": frozenset({"right"}),
    "up": frozenset({"up"}),
    "down": frozenset({"down"}),
    "pinchin": frozenset({"pinchin"}),
    "pinchout": frozenset({"pinchout"}),
}
"""Which directions each direction *shadows*, mapped out of the compositor itself.

Established by probing `Hyprland --verify-config` over all 100 direction pairs, because
the research doc is wrong about this: `lua-api-surface.md` §11 describes the gesture key as
the five-tuple `(fingers, direction, mods, scale, disable_inhibit)`, and the binary keys on
`(fingers, direction, mods)` with *containment* on direction. A `swipe` shadows every
directional swipe under it; `horizontal` shadows `left` and `right`; `pinch` shadows
`pinchin` and `pinchout`. Neither `scale` nor `disable_inhibit` distinguishes anything.

This matters more than a docs footnote, because a shadowed gesture is not a warning:
`hl.gesture` *raises* -- "Gesture will be overshadowed by a previous gesture" -- and takes
the whole Module down. Unlike Binds, where duplicate Triggers are legal and fire in order
(ADR-0007), a second gesture on a covered trigger is a config that will not load.
"""

GESTURE_IDENTITY_FIELDS: tuple[str, ...] = ("fingers", "direction", "mods")
"""The fields the compositor keys a gesture by -- see `GESTURE_DIRECTION_COVERS`."""


def _gesture_key(gesture: Gesture) -> tuple[Any, ...]:
    fields = gesture.fields
    return (fields.get("fingers"), str(fields.get("mods") or ""))


def gesture_conflicts(gestures: Sequence[Gesture]) -> tuple[Finding, ...]:
    """Gestures an earlier one shadows, which Hyprland refuses to load at all.

    Reported against the *later* gesture, because that is the one the compositor names and
    the one whose trigger has to change. Order matters: "previous shadows new" means the
    first declaration wins, so the row to fix is always the lower one.
    """
    findings: list[Finding] = []
    seen: list[tuple[tuple[Any, ...], str, str]] = []
    for gesture in gestures:
        if is_scripted(gesture):
            continue
        direction = str(gesture.fields.get("direction") or "")
        key = _gesture_key(gesture)
        for other_key, other_direction, other_title in seen:
            covers = GESTURE_DIRECTION_COVERS.get(other_direction, frozenset())
            if other_key == key and direction in covers:
                findings.append(
                    Finding(
                        gesture_title(gesture),
                        f"“{other_title}” already covers this, so Hyprland refuses the "
                        f"whole gestures file. Change the fingers, direction or modifiers.",
                    )
                )
                break
        seen.append((key, direction, gesture_title(gesture)))
    return tuple(findings)


def gesture_title(gesture: Gesture) -> str:
    """A gesture's trigger as one short phrase -- how a row and a finding both name it."""
    fields = gesture.fields
    fingers = fields.get("fingers")
    parts = [f"{fingers} fingers" if fingers else "", str(fields.get("direction") or "")]
    if fields.get("mods"):
        parts.insert(0, str(fields["mods"]))
    return " · ".join(part for part in parts if part) or "Gesture"


def is_scripted(gesture: Gesture) -> bool:
    """Whether a gesture's action is Lua rather than one of the string actions.

    A recorded function arrives from the Lua importer as a marker the mapper could not
    reduce to data, so anything that is not a string is script by definition.
    """
    action = gesture.fields.get("action")
    return action is not None and not isinstance(action, str)


# --- devices (`lua-api-surface.md` §12) ---------------------------------------------------

_BOOL = FieldType.BOOL
_INT = FieldType.INT
_FLOAT = FieldType.FLOAT
_STR = FieldType.STRING
_VEC2 = FieldType.VEC2

DEVICE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("enabled", _BOOL, "Enabled"),
    FieldSpec("sensitivity", _FLOAT, "Sensitivity", minimum=-1.0, maximum=1.0),
    FieldSpec("accel_profile", _STR, "Acceleration profile"),
    FieldSpec("left_handed", _BOOL, "Left handed"),
    FieldSpec("natural_scroll", _BOOL, "Natural scroll"),
    FieldSpec("scroll_method", _STR, "Scroll method"),
    FieldSpec("scroll_button", _INT, "Scroll button"),
    FieldSpec("scroll_button_lock", _BOOL, "Scroll button lock"),
    FieldSpec("scroll_points", _STR, "Scroll points"),
    FieldSpec("scroll_factor", _FLOAT, "Scroll factor"),
    FieldSpec("clickfinger_behavior", _BOOL, "Clickfinger behaviour"),
    FieldSpec("middle_button_emulation", _BOOL, "Middle-button emulation"),
    FieldSpec("tap_to_click", _BOOL, "Tap to click"),
    FieldSpec("tap_and_drag", _BOOL, "Tap and drag"),
    FieldSpec("tap_button_map", _STR, "Tap button map"),
    FieldSpec("drag_lock", _INT, "Drag lock"),
    FieldSpec("drag_3fg", _INT, "Three-finger drag"),
    FieldSpec("disable_while_typing", _BOOL, "Disable while typing"),
    FieldSpec("kb_layout", _STR, "Keyboard layout"),
    FieldSpec("kb_variant", _STR, "Keyboard variant"),
    FieldSpec("kb_model", _STR, "Keyboard model"),
    FieldSpec("kb_options", _STR, "Keyboard options"),
    FieldSpec("kb_rules", _STR, "Keyboard rules"),
    FieldSpec("kb_file", _STR, "Keymap file"),
    FieldSpec("numlock_by_default", _BOOL, "Num Lock on by default"),
    FieldSpec("resolve_binds_by_sym", _BOOL, "Resolve binds by symbol"),
    FieldSpec("repeat_rate", _INT, "Repeat rate"),
    FieldSpec("repeat_delay", _INT, "Repeat delay"),
    FieldSpec("keybinds", _BOOL, "Keybinds fire from this device"),
    FieldSpec("release_pressed_on_close", _BOOL, "Release keys on close"),
    FieldSpec("share_states", _INT, "Share states"),
    FieldSpec("transform", _INT, "Transform"),
    FieldSpec("rotation", _INT, "Rotation"),
    FieldSpec("output", _STR, "Bound output"),
    FieldSpec("region_position", _VEC2, "Region position"),
    FieldSpec("region_size", _VEC2, "Region size"),
    FieldSpec("absolute_region_position", _BOOL, "Absolute region position"),
    FieldSpec("active_area_position", _VEC2, "Active area position"),
    FieldSpec("active_area_size", _VEC2, "Active area size"),
    FieldSpec("relative_input", _BOOL, "Relative input"),
    FieldSpec("flip_x", _BOOL, "Flip X"),
    FieldSpec("flip_y", _BOOL, "Flip Y"),
    FieldSpec("tags", _STR, "Tags"),
)
"""The closed 43-key `DEVICE_FIELDS` set the Lua parser accepts (CR:230-274).

Closed in the strong sense: `hl.device` raises "unknown field" on anything else and takes
the whole Module down with it, which is why this list is the one place in the catalogue a
value *not* in it is worth warning about before the write.

Four legacy per-device keys have no Lua field at all -- `eraser_button_mode`,
`eraser_button_override`, `pressure_range_min`, `pressure_range_max` -- and the importer
already records their loss as L20.
"""

DEVICE_FIELD_SPECS: dict[str, FieldSpec] = _spec_map(DEVICE_FIELDS)

DEVICE_ONLY_FIELDS: frozenset[str] = frozenset({"keybinds", "tags", "name"})
"""Device fields that shadow no `hl.config` Option, so no Row can carry their badge.

`keybinds` and `tags` are per-device concepts with no global counterpart, and `name` is
the identity rather than a setting. Named so `device_override_options` can assert that
everything else *did* find a home instead of failing quietly on a rename.
"""

_DEVICE_OPTION_ALIASES: dict[str, str] = {
    "tap_to_click": "tap-to-click",
    "tap_and_drag": "tap-and-drag",
}
"""The two device fields whose Option spells the same word with dashes (CR:249-250)."""


def device_override_options(option_names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Map each device field to the Option names a per-device value shadows.

    Derived from the Schema's own Option names rather than hand-listed, because the two
    lists move together on a Hyprland release and a hand-written table would rot into
    badges that point at Options that no longer exist. The rule is the compositor's own:
    a device field shadows every `input:...` Option whose last colon-segment is the same
    word, so `natural_scroll` covers both the pointer and the touchpad spelling -- which
    of the two applies depends on the device's *class*, and the app cannot know that
    without the compositor. Naming both is the honest answer; naming one would be a guess
    presented as a fact.
    """
    by_leaf: dict[str, list[str]] = {}
    for name in option_names:
        if not name.startswith("input:"):
            continue
        by_leaf.setdefault(name.rsplit(":", 1)[-1], []).append(name)

    mapping: dict[str, tuple[str, ...]] = {}
    for spec in DEVICE_FIELDS:
        leaf = _DEVICE_OPTION_ALIASES.get(spec.name, spec.name)
        hits = by_leaf.get(leaf)
        if hits:
            mapping[spec.name] = tuple(hits)
    return mapping


def overridden_options(
    devices: Sequence[Device], option_names: Iterable[str]
) -> dict[str, tuple[str, ...]]:
    """Which Options a per-device override touches, and which devices touch them.

    The Row badge's whole input (ADR-0013's `device-override` state): keyed by Option name
    so a Row can ask about itself in constant time, valued with the device names so the
    badge can say *which* device rather than only that one exists.
    """
    fields_to_options = device_override_options(option_names)
    hits: dict[str, list[str]] = {}
    for device in devices:
        for key in device.fields:
            for option in fields_to_options.get(str(key), ()):
                names = hits.setdefault(option, [])
                if device.name not in names:
                    names.append(device.name)
    return {option: tuple(names) for option, names in hits.items()}


def device_field_bounds(
    ranges: Mapping[str, tuple[float | None, float | None]],
) -> dict[str, tuple[float | None, float | None]]:
    """The min/max for each device field, taken from the Options it shadows.

    Derived rather than hand-listed for the reason `device_override_options` is: the
    numbers move on a Hyprland release, and a copy here would go quietly stale. It also
    turns out to be the *more accurate* route -- the research doc gives `scroll_factor` as
    0..100 and the shipped Schema says 0..2.

    Where a field shadows two Options the widest pair wins. Which of the two applies
    depends on the device's class, which the app cannot know without the compositor, and a
    bound that refuses a value the user's hardware accepts is worse than one that permits a
    value it does not: the first is unusable, the second is a finding.
    """
    bounds: dict[str, tuple[float | None, float | None]] = {}
    for field_name, options in device_override_options(ranges).items():
        lows = [ranges[name][0] for name in options if ranges[name][0] is not None]
        highs = [ranges[name][1] for name in options if ranges[name][1] is not None]
        if lows or highs:
            bounds[field_name] = (
                min(lows) if lows else None,  # type: ignore[type-var]
                max(highs) if highs else None,  # type: ignore[type-var]
            )
    return bounds


def unknown_device_fields(device: Device) -> tuple[str, ...]:
    """Keys on a device that `hl.device` would reject outright, in the order held."""
    return tuple(str(key) for key in device.fields if str(key) not in DEVICE_FIELD_SPECS)


def device_findings(device: Device) -> tuple[Finding, ...]:
    """What Hyprland would say about one per-device override.

    Here rather than in the Page for the reason every other kind's findings are here: the
    sentence describing what the compositor rejects belongs beside the rule that says it
    would, so a Release check has one file to re-verify.
    """
    return tuple(
        Finding(
            device.name,
            f"Hyprland has no per-device setting called “{key}”, so it will refuse "
            f"this device.",
        )
        for key in unknown_device_fields(device)
    )


def env_findings(variable: EnvVar) -> tuple[Finding, ...]:
    """What would go wrong with one environment variable.

    A name `setenv` will not take is the whole check: the *value* is free text by design
    (it routinely holds commas, colons and paths), and Lua quoting handles it.
    """
    if not ENV_NAME.match(variable.name):
        return (
            Finding(
                variable.name,
                "A variable name may only hold letters, digits and underscores, and may "
                "not start with a digit.",
            ),
        )
    return ()


# --- environment variables (`lua-api-surface.md` §13) -------------------------------------

IDENTITY_FIELD: dict[str, str] = {
    "curves": "name",
    "animations": "leaf",
    "devices": "name",
    "env": "name",
}
"""Which declarative kinds have an identity, and the attribute that holds it.

The four Hyprland itself keys: a second `hl.curve("easy", ...)` overwrites the first, a
second `hl.animation{leaf="fade"}` wins, `hl.device` merges per name, and the last
`hl.env` for a name is the value the session gets. Two rows sharing one of these describes
a config the compositor will not produce, which is the reason ADR-0008 keys workspace
rules by selector.

Gestures are absent on purpose even though they *are* keyed: their key is a tuple with
containment on one member, so `gesture_conflicts` answers for them instead of a field
name. Permissions and autostart commands take genuine duplicates.

One map, read by both the session's write gate and the editor's up-front refusal, so the
two cannot come to disagree about what counts as the same row.
"""

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
"""What a POSIX environment variable may be named -- `setenv` rejects the rest."""


# --- permissions (`lua-api-surface.md` §15) -----------------------------------------------

PERMISSION_TYPES: tuple[str, ...] = (
    "screencopy",
    "cursorpos",
    "plugin",
    "keyboard",
    "input-capture",
)
"""`keeb` is accepted as an alias of `keyboard` (CR:591-606); the app emits the long one."""

PERMISSION_MODES: tuple[str, ...] = ("ask", "allow", "deny")

PERMISSION_ENFORCE_OPTION = "ecosystem:enforce_permissions"
"""Permissions do nothing unless this Option is on (WIKI Permissions.md:21-23).

The Page states it, because a permission list that is quietly inert is exactly the kind of
falsehood ADR-0013 forbids a Row from telling.
"""


# --- autostart (`lua-api-surface.md` §14) -------------------------------------------------

STARTUP_EVENT = "hyprland.start"
SHUTDOWN_EVENT = "hyprland.shutdown"
EVERY_RELOAD = ""
"""`StartupCommand.event`'s three values, and what each one means to a user.

`""` is the old `exec`: a top-level `hl.exec_cmd`, re-run on every reload because the whole
file is re-executed (`lua-api-surface.md` §0). The other two ride `hl.on`.
"""

STARTUP_EVENTS: tuple[tuple[str, str], ...] = (
    (STARTUP_EVENT, "Once, when Hyprland starts"),
    (EVERY_RELOAD, "Every time the config reloads"),
    (SHUTDOWN_EVENT, "When Hyprland shuts down"),
)
"""Event value to the sentence a user picks it by, in the order the picker shows them."""


# --- validation --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing about an Entity that Hyprland would reject or ignore.

    Advisory: the writer emits the entity anyway (see the module docstring). `subject`
    names the entity the way its Page titles it, so a finding can be shown on the row it
    belongs to without the Page re-deriving the title.
    """

    subject: str
    message: str


def dangling_curve_references(entities: EntitySet) -> tuple[Finding, ...]:
    """Animations naming a curve that nothing declares (and Hyprland would refuse).

    `hl.animation{bezier=...}` errors when the curve does not exist (CR:430-441), and an
    error in an Entity Module is a config error at load: the animation is lost and the
    Banner lights. Worth its own check rather than leaving it to Read-back because the
    reference is *inside the app's own model* -- deleting a curve that three animations use
    is a thing the user does in this app, and finding out from the compositor afterwards is
    the worse of the two ways to learn it.

    An animation with `enabled = false` is exempt: the parser short-circuits before it
    looks for a curve (CR:412-415), and so does a config that only ever turns things off.
    """
    declared = {curve.name for curve in entities.curves}
    declared.update(BUILTIN_CURVES)

    findings: list[Finding] = []
    for animation in entities.animations:
        if animation.fields.get("enabled") is False:
            continue
        for key in ANIMATION_CURVE_KEYS:
            reference = animation.fields.get(key)
            if isinstance(reference, str) and reference and reference not in declared:
                findings.append(
                    Finding(
                        animation.leaf,
                        f"No curve named “{reference}” is declared, "
                        f"so Hyprland will refuse this animation.",
                    )
                )
    return tuple(findings)


def missing_curve_references(entities: EntitySet) -> tuple[Finding, ...]:
    """Enabled animations that name no curve at all -- the other way the parser refuses.

    "bezier or spring is required" (CR:453-454). Separate from the dangling case because
    the fix is different: this one needs a curve *chosen*, not declared.

    A bare `hl.animation{leaf = "fade"}` used to be exempted here on the theory that it
    carried no intent to animate. Probing the binary killed that theory: it is not a
    harmless no-op, it is an error ("missing required field \"enabled\"") that takes the
    Module down -- and it is exactly what the Add flow produces when the form is saved
    without touching anything. See `animation_findings` for the required-field checks.
    """
    findings: list[Finding] = []
    for animation in entities.animations:
        if animation.fields.get("enabled") is False:
            continue
        if any(animation.fields.get(key) for key in ANIMATION_CURVE_KEYS):
            continue
        if "enabled" not in animation.fields:
            # Already reported as a missing required field; saying "pick a curve" as well
            # would put two findings on one row when there is only one thing to do first.
            continue
        findings.append(
            Finding(animation.leaf, "Pick a curve: Hyprland requires one to animate.")
        )
    return tuple(findings)


def curve_findings(curve: Curve) -> tuple[Finding, ...]:
    """What Hyprland would say about one curve's own numbers."""
    findings: list[Finding] = []
    kind = curve.spec.get("type")
    if kind == "spring":
        for spec in SPRING_FIELDS:
            value = curve.spec.get(spec.name)
            if not isinstance(value, int | float):
                findings.append(Finding(curve.name, f"{spec.label} is required."))
            elif value <= SPRING_MIN:
                findings.append(
                    Finding(curve.name, f"{spec.label} must be greater than {SPRING_MIN}.")
                )
        return tuple(findings)

    points = curve.spec.get("points")
    if not isinstance(points, Sequence) or isinstance(points, str) or len(points) != 2:
        return (Finding(curve.name, "A bezier needs exactly two control points."),)
    for point in points:
        if not isinstance(point, Sequence) or isinstance(point, str) or len(point) != 2:
            return (Finding(curve.name, "Each control point is an x and a y."),)
        for axis in point:
            if not isinstance(axis, int | float):
                return (Finding(curve.name, "Control points must be numbers."),)
            if not CURVE_POINT_MIN <= axis <= CURVE_POINT_MAX:
                findings.append(
                    Finding(
                        curve.name,
                        f"{axis} is outside {CURVE_POINT_MIN}..{CURVE_POINT_MAX}; "
                        f"Hyprland clamps control points to that range.",
                    )
                )
    return tuple(findings)


def animation_findings(animation: Animation) -> tuple[Finding, ...]:
    """What Hyprland would say about one animation, curve references aside.

    The required-field rules are the binary's, not the wiki's -- probed with
    `--verify-config`, which rejects each of these by name:

    * `enabled` is required on *every* animation, even one that only turns a leaf off.
    * `speed` is required whenever `enabled` is true, and ignored when it is false.

    Both were missing from the research doc's account of `hl.animation`, and both are
    reachable from the Add form in two clicks.
    """
    findings: list[Finding] = []
    enabled = animation.fields.get("enabled")
    if "enabled" not in animation.fields:
        findings.append(
            Finding(
                animation.leaf,
                "Say whether this animation is on: Hyprland requires it, and refuses the "
                "whole animations file without it.",
            )
        )
    elif enabled is not False and animation.fields.get("speed") is None:
        findings.append(
            Finding(animation.leaf, "Set a speed: Hyprland requires one to animate.")
        )
    if animation.leaf not in ANIMATION_LEAVES:
        findings.append(
            Finding(
                animation.leaf,
                "This Hyprland's animation tree has no such leaf; it may be from a newer "
                "release.",
            )
        )
    speed = animation.fields.get("speed")
    if isinstance(speed, int | float) and not (
        ANIMATION_SPEED_MIN < speed <= ANIMATION_SPEED_MAX
    ):
        findings.append(
            Finding(
                animation.leaf,
                f"Speed must be above {ANIMATION_SPEED_MIN:g} and at most "
                f"{ANIMATION_SPEED_MAX:g}.",
            )
        )
    if all(animation.fields.get(key) for key in ANIMATION_CURVE_KEYS):
        findings.append(Finding(animation.leaf, "Set a bezier or a spring, not both."))
    return tuple(findings)


def unknown_leaves(entities: EntitySet) -> tuple[str, ...]:
    """Animation leaves this Hyprland's tree does not have, deduplicated in model order."""
    seen: list[str] = []
    for animation in entities.animations:
        if animation.leaf not in ANIMATION_LEAVES and animation.leaf not in seen:
            seen.append(animation.leaf)
    return tuple(seen)


@dataclass(frozen=True, slots=True)
class CurveUsage:
    """Which animations reference a curve -- what "delete this curve" has to warn about."""

    name: str
    leaves: tuple[str, ...] = field(default_factory=tuple)

    @property
    def used(self) -> bool:
        return bool(self.leaves)


def curve_usage(entities: EntitySet, name: str) -> CurveUsage:
    """The animations that would break if the curve called `name` went away."""
    leaves = [
        animation.leaf
        for animation in entities.animations
        if any(animation.fields.get(key) == name for key in ANIMATION_CURVE_KEYS)
    ]
    return CurveUsage(name=name, leaves=tuple(leaves))


def coerce(spec: FieldSpec, text: str) -> Any:
    """One editor field's text as the Lua value its spec calls for.

    Empty text is `None`, which every caller reads as "leave the key out" rather than as a
    value: an entity table with `speed = nil` and one without it are the same table, and
    the model holds the second.
    """
    text = text.strip()
    if not text:
        return None
    if spec.type is FieldType.BOOL:
        return text.lower() in ("1", "true", "yes", "on")
    if spec.type is FieldType.INT:
        try:
            return int(text, 10)
        except ValueError:
            return text
    if spec.type is FieldType.FLOAT:
        try:
            return float(text)
        except ValueError:
            return text
    if spec.type is FieldType.VEC2:
        parts = [part for part in re.split(r"[,\s]+", text) if part]
        try:
            return [float(part) for part in parts] if len(parts) == 2 else text
        except ValueError:
            return text
    return text


def field_text(value: Any) -> str:
    """The inverse of `coerce` for display: a held value as editable text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, Mapping):
        return ", ".join(f"{key}={field_text(item)}" for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, str):
        return ", ".join(field_text(item) for item in value)
    return str(value)


__all__ = [
    "ANIMATION_CURVE_KEYS",
    "ANIMATION_FIELDS",
    "ANIMATION_FIELD_SPECS",
    "ANIMATION_LEAVES",
    "ANIMATION_SPEED_MAX",
    "ANIMATION_SPEED_MIN",
    "BUILTIN_CURVES",
    "CURVE_POINT_MAX",
    "CURVE_POINT_MIN",
    "CURVE_TYPES",
    "DEVICE_FIELDS",
    "DEVICE_FIELD_SPECS",
    "DEVICE_ONLY_FIELDS",
    "ENV_NAME",
    "EVERY_RELOAD",
    "GESTURE_ACTIONS",
    "GESTURE_DIRECTIONS",
    "GESTURE_DIRECTION_COVERS",
    "GESTURE_FIELDS",
    "GESTURE_FIELD_SPECS",
    "GESTURE_FINGERS_MAX",
    "GESTURE_FINGERS_MIN",
    "GESTURE_IDENTITY_FIELDS",
    "GESTURE_SCALE_MAX",
    "GESTURE_SCALE_MIN",
    "IDENTITY_FIELD",
    "PERMISSION_ENFORCE_OPTION",
    "PERMISSION_MODES",
    "PERMISSION_TYPES",
    "SHUTDOWN_EVENT",
    "SPRING_FIELDS",
    "SPRING_MIN",
    "STARTUP_EVENT",
    "STARTUP_EVENTS",
    "UNSET_ACTION",
    "CurveUsage",
    "FieldSpec",
    "FieldType",
    "Finding",
    "animation_findings",
    "coerce",
    "curve_findings",
    "curve_usage",
    "dangling_curve_references",
    "device_field_bounds",
    "device_findings",
    "device_override_options",
    "env_findings",
    "field_text",
    "gesture_conflicts",
    "gesture_title",
    "is_scripted",
    "missing_curve_references",
    "overridden_options",
    "unknown_device_fields",
    "unknown_leaves",
]
