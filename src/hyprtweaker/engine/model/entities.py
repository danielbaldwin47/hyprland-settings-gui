"""Entities: the non-option half of the model.

An Option is one `hl.config` value; an **Entity** is a config object with its own `hl.*`
constructor -- a Bind, a Rule, a monitor rule, an animation (CONTEXT.md). Options live in
`options.py` against the Schema; Entities have no Schema, so their shapes are declared here.

The shapes mirror the **Lua** call they render to, not the hyprlang line they came from,
because Lua is what the app writes: `Bind.keys` is already the `"SUPER + Q"` string
`hl.bind` takes, and a `DispatcherCall` is already an `hl.dsp.*` path plus its table. The
hyprlang spellings (`SUPER_SHIFT`, bare keycodes, `bindm`) are the Importer's problem and
are gone by the time an Entity exists -- see `engine/importer/mapping.py`.

Identity is list position for Binds and Rules (ADR-0007, ADR-0008): duplicates are legal
and order is meaning. The three kinds Hyprland itself merges -- workspace rules by
selector, monitor rules by output, named window/layer rules by name -- are merged by the
Importer as it maps, so an `EntitySet` holds what Hyprland would end up with.

`origin` is a plain `"file:line"` string rather than an importer `Origin`, so the model
stays free of any dependency on the Importer that fills it in.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

__all__ = [
    "Animation",
    "Bind",
    "BindDevice",
    "BindOptions",
    "Curve",
    "Device",
    "DispatcherCall",
    "EntitySet",
    "EnvVar",
    "Gesture",
    "LayerRule",
    "MonitorRule",
    "Permission",
    "PluginLoad",
    "StartupCommand",
    "Submap",
    "Unbind",
    "WindowRule",
    "WorkspaceRule",
]


# --- shared pieces ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DispatcherCall:
    """One `hl.dsp.*` call: a dotted path, plus how it is called.

    `hl.dsp` factories come in two shapes and the difference is not cosmetic --
    `hl.dsp.submap("resize")` takes a bare string while `hl.dsp.focus{workspace="e+1"}`
    takes a table, and passing one form where the other is expected is a Lua error. So the
    two are kept apart: `args` renders as a table, `positional` as bare arguments.
    """

    path: str
    args: Mapping[str, Any] = field(default_factory=dict)
    positional: tuple[Any, ...] = ()

    def __str__(self) -> str:
        if self.positional:
            inner = ", ".join(repr(arg) for arg in self.positional)
            return f"hl.dsp.{self.path}({inner})"
        if self.args:
            inner = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
            return f"hl.dsp.{self.path}{{{inner}}}"
        return f"hl.dsp.{self.path}()"


@dataclass(frozen=True, slots=True)
class BindDevice:
    """The `k` flag's device list: `bindk = MODS, key, [!]dev1 dev2, ...`.

    A leading `!` in the legacy field means *exclusive* -- fire on everything except these
    devices -- which Lua spells `inclusive = false`.
    """

    inclusive: bool
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BindOptions:
    """The `HL.BindOptions` table: every legacy bind flag that survives into Lua.

    The names are Lua's, not hyprlang's, and several differ from the flag letter's legacy
    name -- `e` is `repeating`, `p` is `dont_inhibit`, `i` is `ignore_mods`. The one flag
    with no field here is `m` (mouse): v0.56.2's `hlBind` never reads a `mouse` key, so a
    `bindm` becomes a drag/resize *dispatcher* instead (L5).
    """

    locked: bool = False
    release: bool = False
    repeating: bool = False
    non_consuming: bool = False
    auto_consuming: bool = False
    transparent: bool = False
    ignore_mods: bool = False
    long_press: bool = False
    submap_universal: bool = False
    dont_inhibit: bool = False
    click: bool = False
    drag: bool = False
    allow_input_capture: bool = False
    description: str = ""
    device: BindDevice | None = None

    def as_table(self) -> dict[str, Any]:
        """The non-default fields, in declaration order -- what Lua would be given."""
        table: dict[str, Any] = {}
        for name in (
            "locked",
            "release",
            "repeating",
            "non_consuming",
            "auto_consuming",
            "transparent",
            "ignore_mods",
            "long_press",
            "submap_universal",
            "dont_inhibit",
            "click",
            "drag",
            "allow_input_capture",
        ):
            if getattr(self, name):
                table[name] = True
        if self.description:
            table["description"] = self.description
        if self.device is not None:
            table["device"] = {
                "inclusive": self.device.inclusive,
                "list": list(self.device.names),
            }
        return table


# --- entities --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bind:
    """One keybind: `hl.bind(keys, dispatcher, opts)`.

    `keys` is the whole `"SUPER + SHIFT + Q"` string, mods and key together, because that is
    `hl.bind`'s first argument -- there is no mods/key table form. `submap` names the
    Submap the bind belongs to (`None` = root), which decides which `hl.define_submap`
    callback it is emitted inside.
    """

    keys: str
    dispatcher: DispatcherCall | None = None
    options: BindOptions = field(default_factory=BindOptions)
    submap: str | None = None
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Unbind:
    """`hl.unbind(keys)`. Matches by key *string*, not by modmask (L6)."""

    keys: str
    all: bool = False
    submap: str | None = None
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Submap:
    """A `hl.define_submap(name, [reset_target,] fn)` declaration."""

    name: str
    reset_target: str = ""
    origin: str = ""


@dataclass(frozen=True, slots=True)
class WindowRule:
    """`hl.window_rule{...}`: a Match plus Effects, ordered, later wins per Effect.

    `named` records whether the source was a named (special-category) rule, because legacy
    evaluated *all* named rules before anonymous ones while Lua is pure call order -- so
    preserving legacy precedence means emitting the named group first (L15).
    """

    match: Mapping[str, Any]
    effects: Mapping[str, Any] = field(default_factory=dict)
    name: str = ""
    enabled: bool = True
    origin: str = ""

    @property
    def named(self) -> bool:
        return bool(self.name)


@dataclass(frozen=True, slots=True)
class LayerRule:
    """`hl.layer_rule{...}`. The only Match prop with any effect is `namespace`."""

    match: Mapping[str, Any]
    effects: Mapping[str, Any] = field(default_factory=dict)
    name: str = ""
    enabled: bool = True
    origin: str = ""

    @property
    def named(self) -> bool:
        return bool(self.name)


@dataclass(frozen=True, slots=True)
class WorkspaceRule:
    """`hl.workspace_rule{...}`. Identity is the selector -- Hyprland merges duplicates."""

    workspace: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class MonitorRule:
    """`hl.monitor{...}`. Identity is `output`; `""` is the catch-all ("Any other display")."""

    output: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Curve:
    """`hl.curve(name, spec)` -- a bezier (or, Lua-only, a spring)."""

    name: str
    spec: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Animation:
    """`hl.animation{leaf=..., ...}`. Identity is the leaf; a later call wins."""

    leaf: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Gesture:
    """`hl.gesture{...}`."""

    fields: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Device:
    """`hl.device{name=..., ...}`. Identity is the name, spaces already dashed."""

    name: str
    fields: Mapping[str, Any] = field(default_factory=dict)
    origin: str = ""


@dataclass(frozen=True, slots=True)
class EnvVar:
    """`hl.env(name, value[, dbus])`. `dbus` is what `envd` meant."""

    name: str
    value: str
    dbus: bool = False
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Permission:
    """`hl.permission(binary, type, mode)`. Applied on first launch only."""

    binary: str
    kind: str
    mode: str
    origin: str = ""


@dataclass(frozen=True, slots=True)
class PluginLoad:
    """`hl.plugin.load(path)`."""

    path: str
    origin: str = ""


@dataclass(frozen=True, slots=True)
class StartupCommand:
    """One `exec*` keyword.

    Legacy has five spellings and they differ in *when* they run, which is the whole
    content of the entity: `event` is the `hl.on` event to wrap the command in (`""` means
    top level, re-run on every reload), `raw` selects `hl.dsp.exec_raw` over `hl.exec_cmd`
    (no `[rules]` prefix parsing).
    """

    command: str
    event: str = "hyprland.start"
    raw: bool = False
    origin: str = ""


# --- container -------------------------------------------------------------------------

_LISTS: tuple[str, ...] = (
    "submaps",
    "binds",
    "unbinds",
    "window_rules",
    "layer_rules",
    "workspace_rules",
    "monitors",
    "curves",
    "animations",
    "gestures",
    "devices",
    "env",
    "permissions",
    "plugins",
    "startup",
)


@dataclass(slots=True)
class EntitySet:
    """Every Entity in one config, each kind in source order.

    Mutable and list-shaped on purpose: the Importer appends as it walks the Keyword stream,
    and the editors of #64 will reorder in place, since for Binds and Rules position *is*
    identity (ADR-0007, ADR-0008).

    The `add_*` helpers implement the three merges Hyprland performs itself, so that what
    this holds is what the compositor would end up with rather than a transcript of the
    calls: workspace rules merge by selector, monitor rules by output, and named window and
    layer rules update in place keeping their original position.
    """

    submaps: list[Submap] = field(default_factory=list)
    binds: list[Bind] = field(default_factory=list)
    unbinds: list[Unbind] = field(default_factory=list)
    window_rules: list[WindowRule] = field(default_factory=list)
    layer_rules: list[LayerRule] = field(default_factory=list)
    workspace_rules: list[WorkspaceRule] = field(default_factory=list)
    monitors: list[MonitorRule] = field(default_factory=list)
    curves: list[Curve] = field(default_factory=list)
    animations: list[Animation] = field(default_factory=list)
    gestures: list[Gesture] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    env: list[EnvVar] = field(default_factory=list)
    permissions: list[Permission] = field(default_factory=list)
    plugins: list[PluginLoad] = field(default_factory=list)
    startup: list[StartupCommand] = field(default_factory=list)

    def __len__(self) -> int:
        return sum(len(getattr(self, name)) for name in _LISTS)

    def __bool__(self) -> bool:
        return len(self) > 0

    def counts(self) -> dict[str, int]:
        """Non-empty kinds and how many of each -- the wizard's "what came across"."""
        return {name: len(getattr(self, name)) for name in _LISTS if getattr(self, name)}

    def kinds(self) -> Iterator[tuple[str, Sequence[Any]]]:
        for name in _LISTS:
            yield name, getattr(self, name)

    # -- the merging adders --

    def add_workspace_rule(self, rule: WorkspaceRule) -> None:
        """Merge by selector: Hyprland's `replaceOrAdd` keeps one rule per workspace."""
        for index, existing in enumerate(self.workspace_rules):
            if existing.workspace == rule.workspace:
                merged = {**existing.fields, **rule.fields}
                self.workspace_rules[index] = replace(existing, fields=merged)
                return
        self.workspace_rules.append(rule)

    def add_monitor_rule(self, rule: MonitorRule, *, merge: bool) -> None:
        """Add or replace by output.

        `merge` is the difference between the two legacy spellings: a full `monitor =` line
        builds a fresh rule and replaces, while the `transform` / `addreserved` shorthands
        edit the existing rule in place. `hl.monitor` itself always merges, so the Importer
        has to decide which one it meant here rather than leaving it to Lua.
        """
        for index, existing in enumerate(self.monitors):
            if existing.output == rule.output:
                fields = {**existing.fields, **rule.fields} if merge else dict(rule.fields)
                self.monitors[index] = replace(existing, fields=fields)
                return
        self.monitors.append(rule)

    def add_window_rule(self, rule: WindowRule) -> None:
        """Named rules update in place (keeping position); anonymous ones append."""
        if rule.name:
            for index, existing in enumerate(self.window_rules):
                if existing.name == rule.name:
                    self.window_rules[index] = replace(
                        existing,
                        match={**existing.match, **rule.match},
                        effects={**existing.effects, **rule.effects},
                        enabled=rule.enabled,
                    )
                    return
        self.window_rules.append(rule)

    def add_layer_rule(self, rule: LayerRule) -> None:
        if rule.name:
            for index, existing in enumerate(self.layer_rules):
                if existing.name == rule.name:
                    self.layer_rules[index] = replace(
                        existing,
                        match={**existing.match, **rule.match},
                        effects={**existing.effects, **rule.effects},
                        enabled=rule.enabled,
                    )
                    return
        self.layer_rules.append(rule)

    def add_animation(self, animation: Animation) -> None:
        """Last call for a leaf wins -- the animation tree holds one node per leaf."""
        for index, existing in enumerate(self.animations):
            if existing.leaf == animation.leaf:
                self.animations[index] = animation
                return
        self.animations.append(animation)

    def add_device(self, device: Device) -> None:
        """One config per device name; repeated blocks merge field-wise."""
        for index, existing in enumerate(self.devices):
            if existing.name == device.name:
                self.devices[index] = replace(
                    existing, fields={**existing.fields, **device.fields}
                )
                return
        self.devices.append(device)

    def ordered_window_rules(self) -> list[WindowRule]:
        """Named rules first, then anonymous, each group in source order.

        Legacy registered every named rule before any anonymous one, while Lua registers in
        pure call order -- so emitting this order is what preserves legacy precedence (L15).
        """
        return [r for r in self.window_rules if r.named] + [
            r for r in self.window_rules if not r.named
        ]

    def ordered_layer_rules(self) -> list[LayerRule]:
        return [r for r in self.layer_rules if r.named] + [
            r for r in self.layer_rules if not r.named
        ]
