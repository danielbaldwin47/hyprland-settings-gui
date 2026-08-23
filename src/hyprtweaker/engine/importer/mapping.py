"""Keyword stream -> model. The Importer's second stage.

The Grammar core knows what a keyword *is*; this knows what it *means*. It walks the flat,
ordered stream once and lands every record somewhere: an Assignment becomes an Option on
the `ConfigModel`, a handler invocation becomes an Entity, and anything with no
representation becomes a Loss finding rather than a silence.

One walk, in source order, is not an implementation convenience -- it is what makes
`submap` blocks work (a Bind belongs to whichever `submap` line preceded it) and what makes
last-wins correct (a later Assignment overwrites an earlier one because it is applied
later). The Grammar core already inlined `source=` where it appeared, so a submap or a
variable spanning files needs nothing extra here.

Nothing raises. A config with one unmappable line still imports; the line becomes a
finding. That is the whole contract with the Migration wizard: it always has a model to
preview and a report to show, and the user decides whether the report is acceptable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..model.entities import (
    Animation,
    Curve,
    Device,
    EntitySet,
    EnvVar,
    Gesture,
    Permission,
    PluginLoad,
    StartupCommand,
    entity_summary,
)
from ..model.options import ConfigModel, UnknownOption
from ..model.values import display_text
from ..schema.resolve import Schema
from ..schema.types import OptionType
from .binds import map_bind, map_submap, map_unbind
from .dispatchers import ScriptLookup, scan_legacy_dispatch, translate_dispatcher
from .hyprlang import ParseResult, parse
from .keywords import (
    Assignment,
    Diagnostic,
    Handler,
    Keyword,
    SourceEnter,
    SpecialCategory,
    UnparsedLine,
    VariableDefinition,
)
from .loss import LossClass, LossCode, LossReport
from .monitors import map_monitor, map_monitor_block
from .rules import map_layer_rule, map_rule_block, map_window_rule, map_workspace_rule
from .scalars import bool_prefix as _bool_prefix
from .scalars import number as _number
from .scalars import truthy as _truthy


def _as_float(text: str) -> float | None:
    """A continuous quantity: always a float, never an int that happens to parse.

    Bezier coordinates and animation speeds are continuous, so `0` reading back as an int
    while `0.5` reads as a float would make two spellings of one value -- the kind of
    difference that surfaces as snapshot churn rather than as a bug, and so goes unnoticed.
    """
    value = _number(text)
    return None if value is None else float(value)


__all__ = ["OPTION_RENAMES", "REMOVED_OPTIONS", "ImportResult", "import_config", "map_keywords"]

#: Options this Hyprland moved rather than dropped. Applying the rename keeps a setting the
#: user chose instead of reporting it as lost (L25).
OPTION_RENAMES: dict[str, str] = {
    "misc:vfr": "debug:vfr",
    "misc:disable_hyprland_qtutils_check": "misc:disable_hyprland_guiutils_check",
}

#: Options that exist in no 0.56.2 engine. Named explicitly so the finding can say "removed
#: in this version" rather than the vaguer "unknown key" every typo also gets.
REMOVED_OPTIONS: dict[str, str] = {
    "debug:watchdog_timeout": "removed after 0.54",
    "render:cm_fs_passthrough": "removed after 0.54",
    "decoration:shadow:ignore_window": "removed after 0.54",
    "dwindle:pseudotile": "removed in 0.55",
}

_EXEC_EVENTS: dict[str, tuple[str, bool, bool]] = {
    # keyword -> (hl.on event, raw, runs on every reload)
    "exec-once": ("hyprland.start", False, False),
    "execr-once": ("hyprland.start", True, False),
    "exec": ("", False, True),
    "execr": ("", True, True),
    "exec-shutdown": ("hyprland.shutdown", False, False),
}

_GESTURE_ACTIONS: frozenset[str] = frozenset(
    [
        "workspace",
        "resize",
        "move",
        "special",
        "close",
        "float",
        "fullscreen",
        "cursorzoom",
        "scrollmove",
        "unset",
    ]
)

_DEVICE_RENAMES: dict[str, str] = {
    "tap-to-click": "tap_to_click",
    "tap-and-drag": "tap_and_drag",
}

#: Per-device settings this Hyprland's Lua side has no field for (L20).
_DEVICE_DROPPED: frozenset[str] = frozenset(
    ["eraser_button_mode", "eraser_button_override", "pressure_range_min", "pressure_range_max"]
)

_BOOL_WORDS: frozenset[str] = frozenset({"yes", "no", "on", "off"})


@dataclass(slots=True)
class ImportResult:
    """Everything one import produced: the model, the Entities, and the account of it."""

    model: ConfigModel
    entities: EntitySet
    loss: LossReport
    diagnostics: list[Diagnostic] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    source: Path | None = None

    @property
    def root(self) -> Path:
        """The directory origins are reported relative to."""
        return self.source.parent if self.source is not None else Path()

    def provenance(self, *, now: datetime | None = None) -> dict[str, Any]:
        """The Manifest's `migration` record: when, from what, at what hash (ADR-0009).

        The hash covers every file the parse actually read, so a later run can tell whether
        the tree it would import from is the one already imported.
        """
        stamp = (now or datetime.now(UTC)).replace(microsecond=0).isoformat()
        return {
            "imported": stamp,
            "source": str(self.source) if self.source else "",
            "files": [str(path) for path in self.files],
            "tree_hash": self.tree_hash(),
            "loss": {str(k): v for k, v in self.loss.counts().items()},
        }

    def tree_hash(self) -> str:
        """One digest over the contents of every file the import read, in read order."""
        digest = hashlib.sha256()
        for path in self.files:
            digest.update(str(path).encode("utf-8"))
            try:
                digest.update(path.read_bytes())
            except OSError:  # pragma: no cover -- a file that vanished mid-import
                digest.update(b"<unreadable>")
        return digest.hexdigest()

    def snapshot(self) -> str:
        """A stable text rendering: Options, Entities, then findings.

        Deterministic across runs and machines -- origins are relative to the tree root --
        so it can be a golden file and the fixpoint check can compare two of them.
        """
        lines = ["# options"]
        for option, value in self.model.set_options():
            lines.append(f"{option.name} = {display_text(value)}")
        lines.append("")
        lines.append("# entities")
        for kind, items in self.entities.kinds():
            for item in items:
                lines.append(f"{kind} | {entity_summary(item)}")
        lines.append("")
        lines.append("# loss")
        for item in self.loss:
            lines.append(f"{item.code} {item.severity} | {item.message}")
        return "\n".join(lines) + "\n"


class _Mapper:
    """One pass over one Keyword stream. Holds only the walk's own state."""

    def __init__(
        self,
        model: ConfigModel,
        report: LossReport,
        root: Path,
        lookup: ScriptLookup | None = None,
    ) -> None:
        self.model = model
        self.report = report
        self.entities = EntitySet()
        self._root = root
        self._lookup = lookup
        self._submap: str | None = None
        self._seen_sources: set[Path] = set()

    # -- helpers --

    def _where(self, keyword: Any) -> str:
        origin = getattr(keyword, "origin", None)
        if origin is None:
            return ""
        try:
            relative = origin.file.relative_to(self._root)
        except ValueError:
            relative = origin.file
        return f"{relative}:{origin.line}"

    # -- the walk --

    def run(self, keywords: Sequence[Keyword]) -> None:
        for keyword in keywords:
            match keyword:
                case Assignment():
                    self._assignment(keyword)
                case Handler():
                    self._handler(keyword)
                case SpecialCategory():
                    self._special(keyword)
                case UnparsedLine():
                    self.report.add(
                        LossCode.UNPARSED_LINE,
                        "line could not be parsed and has no model representation",
                        origin=self._where(keyword),
                        source=keyword.text,
                    )
                case VariableDefinition():
                    self._variable(keyword)
                case SourceEnter():
                    self._source(keyword)
                case _:
                    pass

    def _variable(self, keyword: VariableDefinition) -> None:
        if "$" in keyword.value:
            self.report.add(
                LossCode.VARIABLE_UNRESOLVED,
                f"variable ${keyword.name} still contains an unresolved reference",
                origin=self._where(keyword),
                source=f"${keyword.name} = {keyword.value}",
            )

    def _source(self, keyword: SourceEnter) -> None:
        # The root file enters the stream as a SourceEnter with no origin -- nothing
        # `source`d it, so there is no `source =` line to convert and nothing to report.
        if keyword.origin is None or keyword.file in self._seen_sources:
            return
        self._seen_sources.add(keyword.file)
        try:
            relative = keyword.file.relative_to(self._root)
        except ValueError:
            relative = keyword.file
        self.report.add(
            LossCode.SOURCE_REQUIRE,
            f"{relative} was inlined; the Lua config requires a converted module instead",
            origin=self._where(keyword),
            source=f"source = {relative}",
        )

    # -- options --

    def _assignment(self, keyword: Assignment) -> None:
        origin = self._where(keyword)
        key = keyword.key
        source = f"{key} = {keyword.value}"
        if key.startswith("plugin:"):
            self.report.add(
                LossCode.PLUGIN_GUARD,
                f"{key} belongs to a plugin; Lua errors on the keys of a plugin that is "
                "not loaded, so it needs a guard",
                origin=origin,
                source=source,
            )
            return
        renamed = OPTION_RENAMES.get(key)
        if renamed is not None:
            self.report.add(
                LossCode.REMOVED_OPTION,
                f"{key} is now {renamed} in this Hyprland",
                origin=origin,
                source=source,
                replacement=f"{renamed} = {keyword.value}",
                loss_class=LossClass.INFO,
            )
            key = renamed
        if key in REMOVED_OPTIONS:
            self.report.add(
                LossCode.REMOVED_OPTION,
                f"{key} was {REMOVED_OPTIONS[key]} and is dropped",
                origin=origin,
                source=source,
            )
            return
        try:
            self.model.set(key, keyword.value)
        except UnknownOption:
            self.report.add(
                LossCode.REMOVED_OPTION,
                f"{key} is not an option in this Hyprland and was dropped",
                origin=origin,
                source=source,
            )
            return
        except (ValueError, TypeError) as error:
            self._retype(key, keyword.value, origin=origin, source=source, error=error)
            return
        self._normalisation(key, keyword.value, origin=origin, source=source)

    def _retype(
        self, key: str, raw: str, *, origin: str, source: str, error: Exception
    ) -> None:
        """Second chance for a value the typed model refused.

        hyprlang's number parser was far looser than the model's: it read a *prefix*, so
        `animations:enabled = yes, please :)` was a perfectly good `1` and shipped rices
        rely on it. Rather than loosen the model -- which would let the same sloppiness in
        from the UI -- the looseness lives here, where it belongs to the format being read.

        An unresolved `$var` is a different failure with the same symptom, and reporting it
        as a type error would send the user looking at the value instead of at the
        variable that never got defined.
        """
        if "$" in raw:
            self.report.add(
                LossCode.VARIABLE_UNRESOLVED,
                f"{key} was left with an unresolved variable, so the value could not be "
                "read; the file defining it was probably not found",
                origin=origin,
                source=source,
            )
            return
        option = self.model.option(key)
        truth = _bool_prefix(raw)
        if truth is not None and option.type in (
            OptionType.BOOL,
            OptionType.INT,
            OptionType.FLOAT,
        ):
            value: Any = truth if option.type is OptionType.BOOL else int(truth)
            try:
                self.model.set(key, value)
            except (ValueError, TypeError):  # pragma: no cover -- the type just matched
                pass
            else:
                self.report.add(
                    LossCode.VALUE_NORMALISED,
                    f"{key} = {raw.strip()!r} was read by hyprlang's prefix rule as "
                    f"{str(value).lower()}",
                    origin=origin,
                    source=source,
                    replacement=str(value).lower(),
                )
                return
        self.report.add(
            LossCode.RULE_VALUE_TYPE,
            f"{key} could not take the value {raw.strip()!r}: {error}",
            origin=origin,
            source=source,
            loss_class=LossClass.BREAKAGE,
        )

    def _normalisation(self, key: str, raw: str, *, origin: str, source: str) -> None:
        """Say so when the model's typed value is spelled differently from the config.

        Restricted to the three cases §2.11 lists (L24) -- bool words, CSS-gap shorthand
        and numeric strings. A general "display text differs" check would fire on every
        colour in the file and bury the findings that matter.
        """
        option = self.model.option(key)
        stripped = raw.strip().lower()
        if option.type is OptionType.BOOL and stripped in _BOOL_WORDS:
            self.report.add(
                LossCode.VALUE_NORMALISED,
                f"{key} = {raw.strip()} normalised to a Lua boolean",
                origin=origin,
                source=source,
                replacement=str(self.model.get(key)).lower(),
            )
        elif option.type is OptionType.CSS_GAPS and 1 < len(raw.split(",")) < 4:
            self.report.add(
                LossCode.VALUE_NORMALISED,
                f"{key} CSS shorthand expanded to all four sides",
                origin=origin,
                source=source,
                replacement=self.model.display(key) or "",
            )

    # -- handlers --

    def _handler(self, keyword: Handler) -> None:
        origin = self._where(keyword)
        name = keyword.name
        value = keyword.value
        match name:
            case "bind":
                bind = map_bind(
                    keyword.flags,
                    value,
                    origin=origin,
                    report=self.report,
                    submap=self._submap,
                    lookup=self._lookup,
                )
                if bind is not None:
                    self.entities.binds.append(bind)
            case "unbind":
                self.entities.unbinds.append(
                    map_unbind(value, origin=origin, report=self.report, submap=self._submap)
                )
            case "submap":
                self._handle_submap(value, origin)
            case "monitor":
                mapped = map_monitor(value, origin=origin, report=self.report)
                if mapped is not None:
                    monitor_rule, merge = mapped
                    self.entities.add_monitor_rule(monitor_rule, merge=merge)
            case "workspace":
                workspace_rule = map_workspace_rule(value, origin=origin, report=self.report)
                if workspace_rule is not None:
                    self.entities.add_workspace_rule(workspace_rule)
            case "windowrule":
                window_rule = map_window_rule(value, origin=origin, report=self.report)
                if window_rule is not None:
                    self.entities.add_window_rule(window_rule)
            case "layerrule":
                layer_rule = map_layer_rule(value, origin=origin, report=self.report)
                if layer_rule is not None:
                    self.entities.add_layer_rule(layer_rule)
            case "windowrulev2" | "layerrulev2":
                self.report.add(
                    LossCode.OLD_WINDOWRULE_SYNTAX,
                    f"{name} is refused outright by this Hyprland; rewrite it as a "
                    f"{name[:-2]} with match: props",
                    origin=origin,
                    source=f"{name} = {value}",
                )
            case "bezier":
                self._bezier(value, origin)
            case "animation":
                self._animation(value, origin)
            case "gesture":
                self._gesture(keyword.flags, value, origin)
            case "env":
                self._env(keyword.flags, value, origin)
            case "permission":
                self._permission(value, origin)
            case "plugin":
                self.entities.plugins.append(PluginLoad(path=value.strip(), origin=origin))
            case "exec" | "execr" | "exec-once" | "execr-once" | "exec-shutdown":
                self._exec(name, value, origin)
            case "source":
                pass  # already inlined by the Grammar core; reported at SourceEnter
            case _:
                self.report.add(
                    LossCode.UNSUPPORTED_KEYWORD,
                    f"keyword {name!r} has no model representation",
                    origin=origin,
                    source=f"{name} = {value}",
                )

    def _handle_submap(self, value: str, origin: str) -> None:
        submap = map_submap(value, origin=origin)
        if submap is None:
            self._submap = None
            return
        self._submap = submap.name
        if not any(existing.name == submap.name for existing in self.entities.submaps):
            self.entities.submaps.append(submap)

    def _bezier(self, value: str, origin: str) -> None:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) < 5:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "bezier needs a name and four coordinates",
                origin=origin,
                source=f"bezier = {value}",
            )
            return
        name = parts[0]
        coords: list[float] = []
        for token in parts[1:5]:
            number = _as_float(token)
            if number is None:
                self.report.add(
                    LossCode.ANIMATION_RANGE,
                    f"bezier {name!r} has a non-numeric coordinate {token!r}",
                    origin=origin,
                    source=f"bezier = {value}",
                )
                return
            coords.append(number)
        outside = [c for c in coords if not -1.0 <= c <= 2.0]
        if outside:
            self.report.add(
                LossCode.ANIMATION_RANGE,
                f"bezier {name!r} has coordinates outside the -1..2 range Lua accepts "
                f"({', '.join(str(c) for c in outside)}); Hyprland will reject the curve",
                origin=origin,
                source=f"bezier = {value}",
            )
        spec = {
            "type": "bezier",
            "points": [[coords[0], coords[1]], [coords[2], coords[3]]],
        }
        self.entities.curves.append(Curve(name=name, spec=spec, origin=origin))

    def _animation(self, value: str, origin: str) -> None:
        parts = [part.strip() for part in value.split(",")]
        leaf = parts[0] if parts else ""
        if not leaf:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "animation has no leaf name",
                origin=origin,
                source=f"animation = {value}",
            )
            return
        enabled = _truthy(parts[1]) if len(parts) > 1 else True
        if not enabled:
            # Disabling a leaf is the whole statement; hyprlang ignored the rest too.
            self.entities.add_animation(
                Animation(leaf=leaf, fields={"enabled": False}, origin=origin)
            )
            return
        fields: dict[str, Any] = {"enabled": True}
        if len(parts) > 2 and parts[2]:
            speed = _as_float(parts[2])
            if speed is None:
                self.report.add(
                    LossCode.ANIMATION_RANGE,
                    f"animation {leaf!r} has a non-numeric speed {parts[2]!r}",
                    origin=origin,
                    source=f"animation = {value}",
                )
            else:
                if not 0 < speed <= 100:
                    self.report.add(
                        LossCode.ANIMATION_RANGE,
                        f"animation {leaf!r} speed {speed} is outside the 0..100 range Lua "
                        "accepts; Hyprland will reject it",
                        origin=origin,
                        source=f"animation = {value}",
                    )
                fields["speed"] = speed
        if len(parts) > 3 and parts[3]:
            fields["bezier"] = parts[3]
        if len(parts) > 4 and parts[4]:
            fields["style"] = ",".join(parts[4:])
        self.entities.add_animation(Animation(leaf=leaf, fields=fields, origin=origin))

    def _gesture(self, flags: str, value: str, origin: str) -> None:
        source = f"gesture{flags} = {value}"
        parts = [part.strip() for part in value.split(",")]
        if len(parts) < 3:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "gesture needs fingers, a direction and an action",
                origin=origin,
                source=source,
            )
            return
        fields: dict[str, Any] = {}
        fingers = _number(parts[0])
        fields["fingers"] = int(fingers) if fingers is not None else parts[0]
        fields["direction"] = parts[1]
        if "p" in flags.lower():
            fields["disable_inhibit"] = True

        rest = parts[2:]
        while rest and (rest[0].lower().startswith(("mod:", "scale:"))):
            token = rest.pop(0)
            key, _, raw = token.partition(":")
            if key.lower() == "mod":
                fields["mods"] = raw.strip()
            else:
                scale = _as_float(raw)
                if scale is not None:
                    fields["scale"] = scale
        if not rest:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "gesture has no action",
                origin=origin,
                source=source,
            )
            return

        action = rest[0]
        lowered = action.lower()
        if lowered == "dispatcher":
            # Lua has no string dispatcher action; it becomes a callback (L12).
            name = rest[1] if len(rest) > 1 else ""
            args = ",".join(rest[2:])
            call = translate_dispatcher(
                name,
                args,
                origin=origin,
                report=self.report,
                source=source,
                lookup=self._lookup,
            )
            self.report.add(
                LossCode.GESTURE_DISPATCHER,
                "gesture dispatcher actions have no string form in Lua and become a "
                "callback that runs the dispatcher",
                origin=origin,
                source=source,
                replacement=str(call) if call else "",
            )
            if call is not None:
                fields["dispatch"] = call
        elif lowered in _GESTURE_ACTIONS:
            fields["action"] = _GESTURE_ACTION_NAMES.get(lowered, lowered)
            extra = rest[1].strip() if len(rest) > 1 else ""
            if lowered == "special" and extra:
                fields["workspace_name"] = extra
            elif lowered in ("float", "fullscreen") and extra:
                fields["mode"] = extra
            elif lowered == "cursorzoom" and extra:
                zoom = _as_float(extra)
                fields["zoom_level"] = zoom if zoom is not None else extra
                if len(rest) > 2 and rest[2].strip():
                    fields["mode"] = rest[2].strip()
        else:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                f"gesture action {action!r} is not one this Hyprland knows",
                origin=origin,
                source=source,
            )
            return
        self.entities.gestures.append(Gesture(fields=fields, origin=origin))

    def _env(self, flags: str, value: str, origin: str) -> None:
        name, sep, raw = value.partition(",")
        if not sep or not name.strip():
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "env needs a name and a value",
                origin=origin,
                source=f"env = {value}",
            )
            return
        self.entities.env.append(
            EnvVar(
                name=name.strip(),
                value=raw.strip(),
                dbus="d" in flags.lower(),
                origin=origin,
            )
        )

    def _permission(self, value: str, origin: str) -> None:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) < 3:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD,
                "permission needs a binary, a type and a mode",
                origin=origin,
                source=f"permission = {value}",
            )
            return
        self.entities.permissions.append(
            Permission(binary=parts[0], kind=parts[1], mode=parts[2], origin=origin)
        )

    def _exec(self, name: str, value: str, origin: str) -> None:
        command = value.strip()
        if not command:
            return
        event, raw, every_reload = _EXEC_EVENTS[name]
        source = f"{name} = {command}"
        scan_legacy_dispatch(
            command,
            origin=origin,
            source=source,
            report=self.report,
            lookup=self._lookup,
        )
        if every_reload:
            self.report.add(
                LossCode.EXEC_TIMING,
                f"{name} ran on every reload and, on the first launch, only once "
                "Hyprland had started; the Lua form spawns while the config is parsed",
                origin=origin,
                source=source,
            )
        self.entities.startup.append(
            StartupCommand(command=command, event=event, raw=raw, origin=origin)
        )

    # -- keyed categories --

    def _special(self, keyword: SpecialCategory) -> None:
        origin = self._where(keyword)
        fields = {entry.key: entry.value for entry in keyword.fields}
        match keyword.category:
            case "device":
                self._device(keyword.key_value or fields.get("name", ""), fields, origin)
            case "monitorv2":
                rule = map_monitor_block(fields, origin=origin, report=self.report)
                if rule is not None and rule.output:
                    self.entities.add_monitor_rule(rule, merge=True)
            case "windowrule":
                self.entities.add_window_rule(
                    map_rule_block(fields, origin=origin, report=self.report, layer=False)  # type: ignore[arg-type]
                )
            case "layerrule":
                self.entities.add_layer_rule(
                    map_rule_block(fields, origin=origin, report=self.report, layer=True)  # type: ignore[arg-type]
                )
            case "plugin":
                self.report.add(
                    LossCode.PLUGIN_GUARD,
                    "plugin options need a guard in Lua: unknown keys of a plugin that is "
                    "not loaded are an error, where hyprlang ignored them",
                    origin=origin,
                    source=f"plugin {{ {', '.join(fields)} }}",
                )
            case _:
                self.report.add(
                    LossCode.UNSUPPORTED_KEYWORD,
                    f"category {keyword.category!r} has no model representation",
                    origin=origin,
                )

    def _device(self, name: str, fields: Mapping[str, str], origin: str) -> None:
        if not name:
            self.report.add(
                LossCode.UNSUPPORTED_KEYWORD, "device block has no name", origin=origin
            )
            return
        mapped: dict[str, Any] = {}
        for key, raw in fields.items():
            if key == "name":
                continue
            if key in _DEVICE_DROPPED:
                self.report.add(
                    LossCode.DEVICE_FIELD,
                    f"per-device {key!r} has no field in this Hyprland's Lua API and was "
                    "dropped",
                    origin=origin,
                    source=f"{key} = {raw}",
                    loss_class=LossClass.BREAKAGE,
                )
                continue
            renamed = _DEVICE_RENAMES.get(key)
            if renamed is not None:
                self.report.add(
                    LossCode.DEVICE_FIELD,
                    f"per-device {key!r} is spelled {renamed!r} in Lua",
                    origin=origin,
                    source=f"{key} = {raw}",
                    replacement=f"{renamed} = {raw.strip()}",
                )
            mapped[renamed or key] = raw.strip()
        # Hyprland dashes the spaces out of device names itself; matching that here means
        # the entity's name is the one the compositor will look for.
        self.entities.add_device(
            Device(name=name.strip().replace(" ", "-"), fields=mapped, origin=origin)
        )


_GESTURE_ACTION_NAMES: dict[str, str] = {
    "cursorzoom": "cursor_zoom",
    "scrollmove": "scroll_move",
}


def map_keywords(
    result: ParseResult,
    schema: Schema,
    *,
    source: Path | None = None,
    home: Path | None = None,
) -> ImportResult:
    """Map one parsed tree into a fresh model, Entity set and Loss report.

    `home` is where `~/...` in an exec command resolves to. It is a parameter rather than
    `Path.home()` because the home the config was written for is not always the one running
    the import -- the Harness stages a rice under a throwaway home, and a wizard previewing
    someone else's dotfiles should not go reading the current user's scripts.
    """
    root = source.parent if source is not None else Path()
    model = ConfigModel(schema)
    report = LossReport(source=str(source) if source else "")
    lookup = ScriptLookup(home=home, config_dir=root)
    mapper = _Mapper(model, report, root, lookup)
    mapper.run(result.keywords)
    if any(rule.named for rule in mapper.entities.window_rules) and any(
        not rule.named for rule in mapper.entities.window_rules
    ):
        report.add(
            LossCode.RULE_PRECEDENCE,
            "named window rules are emitted before anonymous ones, because hyprlang "
            "evaluated them in that order while Lua uses plain call order",
        )
    return ImportResult(
        model=model,
        entities=mapper.entities,
        loss=report,
        diagnostics=list(result.diagnostics),
        files=list(result.files),
        variables=dict(result.variables),
        source=source,
    )


def import_config(
    path: Path,
    schema: Schema,
    *,
    env: Mapping[str, str] | None = None,
    follow_source: bool = True,
) -> ImportResult:
    """Parse and map a `hyprland.conf` tree in one call -- the wizard's entry point."""
    parsed = parse(path, env=env, follow_source=follow_source)
    home = Path(env["HOME"]) if env and env.get("HOME") else None
    return map_keywords(parsed, schema, source=path, home=home)
