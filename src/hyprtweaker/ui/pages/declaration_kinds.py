"""What each declarative Entity kind looks like as a Page and as a form (#70).

Seven kinds -- curves, animations, gestures, devices, environment variables, autostart
commands, permissions -- served by one Page class and one editor dialog, the way the two
rule kinds share theirs (ADR-0008: "same list model and editor shell"). This module is the
data that parameterises them: the Page's copy, the form's field list, and the two functions
that turn an entity into a form and a form back into an entity.

Toolkit-free on purpose. Every decision about what a row *says* -- its title, its summary
line, whether the form is complete enough to save -- is testable without a display, which
is the same seam `ui/rows/state.py` keeps for Option rows. The dialog and the Page below it
only turn these answers into widgets.

Field *types and bounds* are not restated here: they come from `engine.entities_catalog`,
which is where the compositor's own rules live. What is added here is the presentation the
engine has no business holding -- titles, hints, and the order a form asks in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hyprtweaker.engine.entities_catalog import (
    ANIMATION_CURVE_KEYS,
    ANIMATION_FIELD_SPECS,
    ANIMATION_FIELDS,
    ANIMATION_LEAVES,
    CURVE_TYPES,
    DEVICE_FIELDS,
    EVERY_RELOAD,
    GESTURE_FIELDS,
    PERMISSION_ENFORCE_OPTION,
    PERMISSION_MODES,
    PERMISSION_TYPES,
    SPRING_FIELDS,
    STARTUP_EVENTS,
    FieldSpec,
    FieldType,
    Finding,
    animation_findings,
    coerce,
    curve_findings,
    dangling_curve_references,
    device_findings,
    env_findings,
    field_text,
    gesture_conflicts,
    gesture_title,
    is_scripted,
    missing_curve_references,
)
from hyprtweaker.engine.model.entities import (
    Animation,
    Curve,
    Device,
    EntitySet,
    EnvVar,
    Gesture,
    Permission,
    StartupCommand,
)


@dataclass(frozen=True, slots=True)
class DeclarationKind:
    """One Entity kind's Page copy, form shape, and entity/form conversions."""

    kind: str
    """`EntitySet`'s own attribute name -- what `Session.declarations` takes."""

    section: str
    """The sidebar id, namespaced `entity:` because a Section and an Entity kind collide.

    Hyprland has an `animations` Section *and* an animation tree, a `gestures` Section
    *and* gesture bindings. The Config view puts one stack page per Section beside one per
    Entity kind, so a bare `animations` is two different pages under one name -- GTK keeps
    the first and drops the second, silently, with only a warning on stderr. The same
    collision `paths.py` solves for Module filenames by putting Option Modules under
    `options/`, solved the same way: give the entity side a namespace.

    The titles are kept distinct for the reader's sake as well as the widget tree's -- two
    sidebar rows both saying "Animations" is a puzzle even when both of them work.
    """

    title: str
    singular: str
    description: str
    empty_hint: str

    fields: tuple[FieldSpec, ...] = ()
    """Rows the form always shows, in the order it asks for them."""

    optional: tuple[FieldSpec, ...] = ()
    """Rows shown only once set, plus an "Add a setting" picker offering the rest.

    Devices are why this tier exists: 43 fields is a form nobody reads, and every one of
    them is optional. Splitting them out keeps the common case -- name a device, set one
    thing -- to two rows, without making the other 42 unreachable.
    """

    note: str = ""
    """A sentence the Page states above the list, when the kind needs a caveat."""

    to_form: Callable[[Any], dict[str, Any]] = field(repr=False, default=lambda _: {})
    from_form: Callable[[Mapping[str, Any], Any], Any] = field(
        repr=False, default=lambda _values, original: original
    )

    title_of: Callable[[Any], str] = field(repr=False, default=str)
    """One entity's row title. A callable per kind, not a `kind ==` cascade.

    The cascade is the shape this whole module exists to avoid: seven kinds times four
    questions is twenty-eight branches spread over the Page, the editor and the window,
    each of which has to be kept in step by hand. Every per-kind answer hangs off the
    descriptor instead, so adding a kind is one entry here and nothing anywhere else.
    """

    subtitle_of: Callable[[Any], str] = field(repr=False, default=lambda _: "")
    scripted: Callable[[Any], bool] = field(repr=False, default=lambda _: False)
    """Whether one entity is Lua the GUI lists but never edits. Only gestures can be."""

    findings_for: Callable[[EntitySet], list[tuple[int, Finding]]] = field(
        repr=False, default=lambda _entities: []
    )
    """Everything wrong with this kind's entities, as `(row index, finding)`.

    Takes the whole `EntitySet`, not one entity, because three of the checks are
    cross-entity: a dangling curve reference and a missing one are properties of the
    *pair* of lists, and a shadowed gesture is a property of the list's order. Indexed
    rather than keyed by title because gesture titles are not unique -- two rows sharing a
    trigger is exactly what `gesture_conflicts` reports, and keying by title badged both.
    """

    @property
    def all_fields(self) -> tuple[FieldSpec, ...]:
        return self.fields + self.optional


# --- curves --------------------------------------------------------------------------------

_BEZIER_POINT_FIELDS: tuple[tuple[str, str], ...] = (
    ("x0", "First point X"),
    ("y0", "First point Y"),
    ("x1", "Second point X"),
    ("y1", "Second point Y"),
)
"""The four numbers a bezier's two control points are asked for as.

Listed once, and read twice -- by the form that shows them and by the completeness check
that knows they are required only for a bezier. Two lists would drift the moment a label
was reworded, and the drift would show up as a form that asks for "First point X" and
complains that "Point 1 X" is missing.
"""

_CURVE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", FieldType.STRING, "Name", required=True),
    FieldSpec("type", FieldType.ENUM, "Kind", required=True, choices=CURVE_TYPES),
    *(FieldSpec(name, FieldType.FLOAT, label) for name, label in _BEZIER_POINT_FIELDS),
    *SPRING_FIELDS,
)
"""A bezier's two control points asked for as four numbers, not as a nested list.

`points = {{x0,y0},{x1,y1}}` is the shape Hyprland takes, and it is a shape no form can
ask for in one row. Four labelled numbers is the same information in the vocabulary
cubic-bezier.com uses, which is where a user gets these values.
"""


def _curve_to_form(curve: Curve) -> dict[str, Any]:
    values: dict[str, Any] = {"name": curve.name, "type": curve.spec.get("type", "bezier")}
    points = curve.spec.get("points")
    if isinstance(points, Sequence) and not isinstance(points, str) and len(points) == 2:
        for index, point in enumerate(points):
            if isinstance(point, Sequence) and not isinstance(point, str) and len(point) == 2:
                values[f"x{index}"] = point[0]
                values[f"y{index}"] = point[1]
    for spec in SPRING_FIELDS:
        if spec.name in curve.spec:
            values[spec.name] = curve.spec[spec.name]
    return values


def _curve_from_form(values: Mapping[str, Any], original: Curve | None) -> Curve:
    kind = str(values.get("type") or "bezier")
    spec: dict[str, Any] = {"type": kind}
    if kind == "spring":
        for item in SPRING_FIELDS:
            if values.get(item.name) is not None:
                spec[item.name] = values[item.name]
    else:
        spec["points"] = [
            [_number(values.get("x0")), _number(values.get("y0"))],
            [_number(values.get("x1")), _number(values.get("y1"))],
        ]
    return Curve(
        name=str(values.get("name") or ""),
        spec=spec,
        origin=original.origin if original is not None else "",
    )


def _number(value: Any) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


# --- animations ----------------------------------------------------------------------------

_ANIMATION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "leaf",
        FieldType.ENUM,
        "Animates",
        required=True,
        choices=ANIMATION_LEAVES,
        help="Which part of the animation tree this entry controls.",
    ),
    *ANIMATION_FIELDS,
)


def _animation_to_form(animation: Animation) -> dict[str, Any]:
    return {"leaf": animation.leaf, **dict(animation.fields)}


def _animation_from_form(values: Mapping[str, Any], original: Animation | None) -> Animation:
    fields = {
        key: value for key, value in values.items() if key != "leaf" and value is not None
    }
    # A curve is a bezier or a spring, never both -- the parser refuses a table carrying
    # each (`animation_findings`). Enforced here *as well as* in the editor's
    # `_clear_sibling_curve`, and the pair is deliberate rather than duplicated: that one
    # keeps the two dropdowns honest while the user is looking at them, and this one is the
    # invariant every save passes through, including saves from a form state no dropdown
    # produced -- an entity read back from a hand-edited Module, or one an importer built.
    # Dropping either leaves a hole: no live feedback, or a rule the UI can route around.
    if fields.get("bezier") and fields.get("spring"):
        fields.pop("spring", None)
    return Animation(
        leaf=str(values.get("leaf") or ""),
        fields=fields,
        origin=original.origin if original is not None else "",
    )


# --- gestures ------------------------------------------------------------------------------


def _gesture_to_form(gesture: Gesture) -> dict[str, Any]:
    return dict(gesture.fields)


def _gesture_from_form(values: Mapping[str, Any], original: Gesture | None) -> Gesture:
    return Gesture(
        fields={key: value for key, value in values.items() if value is not None},
        origin=original.origin if original is not None else "",
    )


# --- devices -------------------------------------------------------------------------------

_DEVICE_NAME = FieldSpec(
    "name",
    FieldType.STRING,
    "Device",
    required=True,
    help="The name from hyprctl devices, with spaces written as dashes.",
)


def _device_to_form(device: Device) -> dict[str, Any]:
    return {"name": device.name, **dict(device.fields)}


def _device_from_form(values: Mapping[str, Any], original: Device | None) -> Device:
    return Device(
        name=str(values.get("name") or ""),
        fields={
            key: value for key, value in values.items() if key != "name" and value is not None
        },
        origin=original.origin if original is not None else "",
    )


# --- environment variables -------------------------------------------------------------------

_ENV_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", FieldType.STRING, "Name", required=True),
    FieldSpec("value", FieldType.STRING, "Value", required=True),
    FieldSpec(
        "dbus",
        FieldType.BOOL,
        "Export to systemd and D-Bus",
        help="Also runs systemctl --user import-environment for this variable.",
    ),
)


def _env_to_form(variable: EnvVar) -> dict[str, Any]:
    return {"name": variable.name, "value": variable.value, "dbus": variable.dbus}


def _env_from_form(values: Mapping[str, Any], original: EnvVar | None) -> EnvVar:
    return EnvVar(
        name=str(values.get("name") or ""),
        value=str(values.get("value") or ""),
        dbus=bool(values.get("dbus")),
        origin=original.origin if original is not None else "",
    )


# --- permissions ---------------------------------------------------------------------------

_PERMISSION_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        "binary",
        FieldType.STRING,
        "Program",
        required=True,
        help="A regular expression matched against the program's full path.",
    ),
    FieldSpec("kind", FieldType.ENUM, "May use", required=True, choices=PERMISSION_TYPES),
    FieldSpec("mode", FieldType.ENUM, "Decision", required=True, choices=PERMISSION_MODES),
)


def _permission_to_form(permission: Permission) -> dict[str, Any]:
    return {
        "binary": permission.binary,
        "kind": permission.kind,
        "mode": permission.mode,
    }


def _permission_from_form(values: Mapping[str, Any], original: Permission | None) -> Permission:
    return Permission(
        binary=str(values.get("binary") or ""),
        kind=str(values.get("kind") or ""),
        mode=str(values.get("mode") or ""),
        origin=original.origin if original is not None else "",
    )


# --- autostart -----------------------------------------------------------------------------

_STARTUP_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("command", FieldType.STRING, "Command", required=True),
    FieldSpec(
        "event",
        FieldType.ENUM,
        "Runs",
        required=True,
        choices=tuple(event for event, _ in STARTUP_EVENTS),
    ),
    FieldSpec(
        "raw",
        FieldType.BOOL,
        "Run without rule parsing",
        help="Use for a command that starts with a [ that is not a window rule.",
    ),
)


def _startup_to_form(command: StartupCommand) -> dict[str, Any]:
    return {"command": command.command, "event": command.event, "raw": command.raw}


def _startup_from_form(
    values: Mapping[str, Any], original: StartupCommand | None
) -> StartupCommand:
    event = values.get("event")
    return StartupCommand(
        command=str(values.get("command") or ""),
        event=EVERY_RELOAD if event is None else str(event),
        raw=bool(values.get("raw")),
        origin=original.origin if original is not None else "",
    )


# --- row text ------------------------------------------------------------------------------

_EVENT_TEXT: dict[str, str] = dict(STARTUP_EVENTS)
"""Autostart event value to the sentence a user picks it by."""


def _device_subtitle(device: Device) -> str:
    if not device.fields:
        return "No settings yet"
    return ", ".join(
        f"{key} {field_text(value)}" for key, value in sorted(device.fields.items())
    )


def _env_subtitle(variable: EnvVar) -> str:
    return f"{variable.value} · exported to D-Bus" if variable.dbus else variable.value


def _startup_subtitle(command: StartupCommand) -> str:
    when = _EVENT_TEXT.get(command.event, command.event or "Every time the config reloads")
    return f"{when} · without rule parsing" if command.raw else when


def _curve_subtitle(curve: Curve) -> str:
    if curve.spec.get("type") == "spring":
        return "Spring · " + ", ".join(
            f"{spec.label.lower()} {field_text(curve.spec.get(spec.name))}"
            for spec in SPRING_FIELDS
            if curve.spec.get(spec.name) is not None
        )
    points = curve.spec.get("points")
    if isinstance(points, Sequence) and not isinstance(points, str):
        flat = [field_text(axis) for point in points for axis in point]
        if len(flat) == 4:
            return f"Bezier · {', '.join(flat)}"
    return "Bezier"


def _animation_subtitle(animation: Animation) -> str:
    fields = animation.fields
    if fields.get("enabled") is False:
        return "Off"
    parts = []
    curve = fields.get("bezier") or fields.get("spring")
    if curve:
        parts.append(str(curve))
    if fields.get("speed") is not None:
        parts.append(f"speed {field_text(fields['speed'])}")
    if fields.get("style"):
        parts.append(str(fields["style"]))
    return " · ".join(parts) or "On"


def _gesture_subtitle(gesture: Gesture) -> str:
    if is_scripted(gesture):
        return "Runs Lua from your own config"
    fields = gesture.fields
    parts = [str(fields.get("action") or "")]
    for key in ("workspace_name", "mode", "scale", "zoom_level"):
        if fields.get(key) is not None:
            parts.append(f"{key.replace('_', ' ')} {field_text(fields[key])}")
    return " · ".join(part for part in parts if part) or "Gesture"


def _permission_subtitle(permission: Permission) -> str:
    return f"{permission.mode} {permission.kind}"


def _animation_findings(entities: EntitySet) -> list[tuple[int, Finding]]:
    index_of = {animation.leaf: index for index, animation in enumerate(entities.animations)}
    found: list[tuple[int, Finding]] = []
    for finding in (*dangling_curve_references(entities), *missing_curve_references(entities)):
        # Safe to resolve by leaf: an animation's leaf *is* its identity, enforced on write.
        if finding.subject in index_of:
            found.append((index_of[finding.subject], finding))
    for index, animation in enumerate(entities.animations):
        found += [(index, finding) for finding in animation_findings(animation)]
    return found


def _curve_findings(entities: EntitySet) -> list[tuple[int, Finding]]:
    return [
        (index, finding)
        for index, curve in enumerate(entities.curves)
        for finding in curve_findings(curve)
    ]


def _device_findings(entities: EntitySet) -> list[tuple[int, Finding]]:
    return [
        (index, finding)
        for index, device in enumerate(entities.devices)
        for finding in device_findings(device)
    ]


def _env_findings(entities: EntitySet) -> list[tuple[int, Finding]]:
    return [
        (index, finding)
        for index, variable in enumerate(entities.env)
        for finding in env_findings(variable)
    ]


def _gesture_findings(entities: EntitySet) -> list[tuple[int, Finding]]:
    return list(gesture_conflicts(entities.gestures))


# --- the catalogue -------------------------------------------------------------------------

KINDS: tuple[DeclarationKind, ...] = (
    DeclarationKind(
        kind="animations",
        findings_for=_animation_findings,
        title_of=lambda entity: entity.leaf,
        subtitle_of=_animation_subtitle,
        section="entity:animations",
        title="Animation tree",
        singular="animation",
        description="One entry per part of the animation tree. Each needs a curve.",
        empty_hint="Add one to override Hyprland's default animation for that part.",
        fields=_ANIMATION_FIELDS,
        to_form=_animation_to_form,
        from_form=_animation_from_form,
    ),
    DeclarationKind(
        kind="curves",
        findings_for=_curve_findings,
        title_of=lambda entity: entity.name,
        subtitle_of=_curve_subtitle,
        section="entity:curves",
        title="Animation curves",
        singular="curve",
        description="Named easing curves the animations above refer to by name.",
        empty_hint="Hyprland's built-in default and linear curves are always available.",
        fields=_CURVE_FIELDS,
        to_form=_curve_to_form,
        from_form=_curve_from_form,
    ),
    DeclarationKind(
        kind="gestures",
        findings_for=_gesture_findings,
        title_of=gesture_title,
        subtitle_of=_gesture_subtitle,
        scripted=is_scripted,
        section="entity:gestures",
        title="Gesture bindings",
        singular="gesture",
        description="Touchpad and touchscreen gestures.",
        empty_hint="Add one to swipe between workspaces or resize a window.",
        fields=GESTURE_FIELDS[:3],
        optional=GESTURE_FIELDS[3:],
        to_form=_gesture_to_form,
        from_form=_gesture_from_form,
    ),
    DeclarationKind(
        kind="devices",
        findings_for=_device_findings,
        title_of=lambda entity: entity.name,
        subtitle_of=_device_subtitle,
        section="entity:devices",
        title="Devices",
        singular="device",
        description="Per-device input settings. These win over the matching Input settings.",
        empty_hint="Add one to give a single mouse, keyboard or tablet its own settings.",
        fields=(_DEVICE_NAME,),
        optional=DEVICE_FIELDS,
        to_form=_device_to_form,
        from_form=_device_from_form,
    ),
    DeclarationKind(
        kind="env",
        findings_for=_env_findings,
        title_of=lambda entity: entity.name,
        subtitle_of=_env_subtitle,
        section="entity:env",
        title="Environment",
        singular="variable",
        description="Variables exported into the session Hyprland starts.",
        empty_hint="Add one to set something like XCURSOR_SIZE for every program.",
        note=(
            "Removing a variable here takes it out of the config, but Hyprland cannot "
            "unset it in a running session -- that needs a restart."
        ),
        fields=_ENV_FIELDS,
        to_form=_env_to_form,
        from_form=_env_from_form,
    ),
    DeclarationKind(
        kind="startup",
        title_of=lambda entity: entity.command,
        subtitle_of=_startup_subtitle,
        section="entity:autostart",
        title="Autostart",
        singular="command",
        description="Commands Hyprland runs for you, in the order listed.",
        empty_hint="Add one to start your bar, notification daemon or wallpaper tool.",
        note=(
            "Startup commands are handed to Hyprland when it starts, so one added here "
            "runs from your next login rather than now."
        ),
        fields=_STARTUP_FIELDS,
        to_form=_startup_to_form,
        from_form=_startup_from_form,
    ),
    DeclarationKind(
        kind="permissions",
        title_of=lambda entity: entity.binary,
        subtitle_of=_permission_subtitle,
        section="entity:permissions",
        title="Permissions",
        singular="permission",
        description="Which programs may record the screen, read the cursor, or grab input.",
        empty_hint="Without any entries, Hyprland asks about every request.",
        note=(
            f"Permissions only apply when “{PERMISSION_ENFORCE_OPTION}” is on, and they "
            f"are read once when Hyprland starts, so changes here take effect after a "
            f"restart."
        ),
        fields=_PERMISSION_FIELDS,
        to_form=_permission_to_form,
        from_form=_permission_from_form,
    ),
)

BY_KIND: dict[str, DeclarationKind] = {item.kind: item for item in KINDS}
BY_SECTION: dict[str, DeclarationKind] = {item.section: item for item in KINDS}


# --- row text ------------------------------------------------------------------------------


def row_title(kind: str, entity: Any) -> str:
    """The bold line of one entity's row -- what it *is*, in as few words as carry it."""
    return BY_KIND[kind].title_of(entity)


def row_subtitle(kind: str, entity: Any) -> str:
    """The dim line: how this entity is configured, summarised."""
    return BY_KIND[kind].subtitle_of(entity)


def choice_label(spec: FieldSpec, value: str) -> str:
    """What a dropdown shows for one enum value.

    Almost always the value itself: `screencopy` and `pinchin` are the words the wiki uses
    and the words a user will search for, and renaming them in the UI would make the docs
    stop matching the app. The exception is autostart's event, whose values are `hl.on`
    event names -- and `""` for "no event at all", which has no reading as a label.
    """
    if spec.name == "event":
        return _EVENT_TEXT.get(value, value)
    return value


def filter_haystack(kind: str, entity: Any) -> str:
    """Everything a row's filter should match, lowercased."""
    return f"{row_title(kind, entity)} {row_subtitle(kind, entity)}".lower()


def read_only(kind: str, entity: Any) -> bool:
    """Whether this entity is script the GUI lists but must not pretend to edit.

    Only gestures can be: an `action` that is a Lua function came from `user.lua` and is
    code the app never authored (ADR-0007's rule for function-valued bind actions).
    """
    return BY_KIND[kind].scripted(entity)


_CURVE_SHAPE_FIELDS: frozenset[str] = frozenset(
    {name for name, _ in _BEZIER_POINT_FIELDS} | {spec.name for spec in SPRING_FIELDS}
)
"""The curve fields whose requiredness depends on which shape the curve is."""


def missing_required(kind: str, values: Mapping[str, Any]) -> tuple[str, ...]:
    """The labels of required fields the form has not filled in.

    Two kinds have fields whose requiredness depends on another field: a curve's points
    matter only for a bezier and its constants only for a spring, and an animation needs a
    speed and a curve only when it is enabled. So this cannot read `required` alone.
    """
    descriptor = BY_KIND[kind]
    # The curve shape fields are required *conditionally* -- a bezier needs four numbers
    # and no spring constants, a spring the reverse -- so they are held out of the flat
    # pass and decided below. Leaving them in would tell someone filling in a bezier that
    # they still owe a mass.
    conditional = _CURVE_SHAPE_FIELDS if kind == "curves" else frozenset()
    missing = [
        spec.label
        for spec in descriptor.all_fields
        if spec.required
        and spec.name not in conditional
        and _blank(values.get(spec.name))
        and values.get(spec.name) not in spec.choices
    ]
    if kind == "animations" and values.get("enabled") is True:
        # Probed against the binary, not read off the wiki: an enabled animation is
        # rejected without a speed ("missing required field \"speed\"") and again without a
        # curve ("bezier or spring is required"). A form that let either through produced
        # an `animations.lua` that would not load, from two clicks in the Add flow.
        if values.get("speed") is None:
            missing.append(ANIMATION_FIELD_SPECS["speed"].label)
        if not any(values.get(key) for key in ANIMATION_CURVE_KEYS):
            missing.append("Bezier curve or Spring curve")

    if kind == "curves":
        if values.get("type") == "spring":
            missing += [spec.label for spec in SPRING_FIELDS if _blank(values.get(spec.name))]
        else:
            missing += [
                label for name, label in _BEZIER_POINT_FIELDS if _blank(values.get(name))
            ]
    return tuple(dict.fromkeys(missing))


def _blank(value: Any) -> bool:
    """Whether a form value counts as unfilled.

    Guarded above by a choices check, because one legal value *is* blank: autostart's
    "every time the config reloads" is the empty event name, so treating `""` as missing
    made an existing every-reload command impossible to save at all.
    """
    return value is None or (isinstance(value, str) and not value.strip())


__all__ = [
    "BY_KIND",
    "BY_SECTION",
    "KINDS",
    "DeclarationKind",
    "choice_label",
    "coerce",
    "field_text",
    "filter_haystack",
    "missing_required",
    "read_only",
    "row_subtitle",
    "row_title",
]
