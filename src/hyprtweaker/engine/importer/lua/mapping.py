"""Recorded `hl.*` calls -> the model, the Entities, `legacy.lua`, and the Loss report.

The counterpart of `importer/mapping.py`, one seam over: where that module interprets a
keyword stream a parser produced, this one interprets a call stream an *evaluation*
produced. The rule it inherits is the important one -- **nothing raises**. A call this
mapper cannot place becomes a finding and, where it is real Lua worth keeping, a block in
`legacy.lua`; the wizard always gets a model to preview and a report to show.

The three-way split ADR-0009 describes falls out here:

* **Declarative** -- `hl.config`, `hl.monitor`, rules, binds with a dispatcher Action:
  these land in the model and the `EntitySet`.
* **Script** -- `hl.on` handlers, `hl.layout.register` providers, timers: whole constructs
  the GUI cannot author. Kept verbatim, listed read-only.
* **Hybrid** -- a declarative call carrying a closure, e.g. a bind whose Action is a
  function. The call is real config, but the model has nowhere to put a function, so the
  whole call is preserved verbatim rather than half-imported.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...model.entities import (
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
from ...model.options import ConfigModel
from ...model.values import parse_lua
from ...schema.resolve import Schema
from ...schema.types import ResolvedOption
from ..loss import LossClass, LossCode, LossContext, LossReport
from ..mapping import ImportResult
from .sandbox import DEFAULT_TIMEOUT, Call, Consent, Policy, Recording, evaluate
from .scripts import ScriptSource, lua_value, luac_binary, render_legacy

#: Calls that are always a script construct, whatever they were handed.
SCRIPT_CALLS = frozenset({"on", "layout_register", "timer"})

#: Calls the model has no home for. Real config, so they are kept, but not modelled.
UNMODELLED_CALLS = frozenset({"dispatch_immediate", "plugin_set", "plugin_call"})

#: `BindOptions` fields that are plain flags, by their Lua spelling.
BIND_FLAGS = frozenset(
    {
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
    }
)


def _positional(call: Call) -> list[Any]:
    """A call's arguments as a list, whatever shape the recorder stored them in."""
    if call.argc == 0:
        return []
    if call.argc == 1 or not isinstance(call.args, list):
        return [call.args]
    return list(call.args)


def _table(value: Any) -> dict[str, Any]:
    return {str(key): item for key, item in value.items()} if isinstance(value, Mapping) else {}


def _has_function(value: Any) -> bool:
    """Whether a captured value carries a closure anywhere inside it."""
    if isinstance(value, Mapping):
        return "__fn" in value or any(_has_function(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_function(item) for item in value)
    return False


def _script_ids(value: Any) -> list[int]:
    if isinstance(value, Mapping):
        if "__fn" in value:
            return [int(value["__fn"])]
        return [found for item in value.values() for found in _script_ids(item)]
    if isinstance(value, list):
        return [found for item in value for found in _script_ids(item)]
    return []


def _option_paths(schema: Schema) -> dict[tuple[str, ...], ResolvedOption]:
    """Every Option by the nested-table path it is written at.

    `ResolvedOption.path` is what the writer nests by, so this is exactly the inverse of
    the writer's `insert(tree, option.path, ...)` -- which is why `general:col.active_border`
    and `decoration:shadow:offset`, spelled with different separators, both resolve without
    this module knowing anything about separators.
    """
    return {option.path: option for option in schema}


class _Mapper:
    def __init__(self, recording: Recording, schema: Schema, *, source: Path | None) -> None:
        self.recording = recording
        self.schema = schema
        self.source = source
        self.model = ConfigModel(schema)
        self.entities = EntitySet()
        self.report = LossReport(source=str(source) if source else "")
        self.scripts = ScriptSource(recording)
        self.legacy: list[str] = []
        self._paths = _option_paths(schema)

    # ---------- the walk ----------

    def run(self) -> ImportResult:
        self._note_evaluation()
        for call in self.recording.calls:
            self._dispatch(call)
        self._note_script_globals()
        for origin, note in self.scripts.notes:
            self.report.add(LossCode.UNEXTRACTABLE_SCRIPT, note, origin=origin)
        return ImportResult(
            model=self.model,
            entities=self.entities,
            loss=self.report,
            files=[self.recording.basedir / name for name in self.recording.requires],
            source=self.source,
            legacy=render_legacy(
                self.legacy,
                source=str(self.source) if self.source else "a foreign hyprland.lua",
            ),
        )

    def _context(self, call: Call) -> LossContext:
        return LossContext(report=self.report, origin=call.origin, source=call.src)

    def _dispatch(self, call: Call) -> None:
        if call.name in SCRIPT_CALLS:
            self._keep_script(call)
            return
        if call.name in UNMODELLED_CALLS or call.name.startswith("notification_"):
            self.report.add(
                LossCode.UNMODELLED_CALL,
                f"{self._spelling(call)} does something the settings app cannot show; "
                "it was kept verbatim in legacy.lua",
                origin=call.origin,
            )
            self._keep_unmodelled(call)
            return
        if call.name != "config" and _has_function(call.args):
            # Hybrid: real declarative config carrying a closure. Half of it would fit the
            # model, but importing half would silently drop the behaviour the closure is.
            self._keep_hybrid(call)
            return
        handler = getattr(self, f"_map_{call.name}", None)
        if handler is None:
            self.report.add(
                LossCode.UNMODELLED_CALL,
                f"hl.{call.name} has no model representation",
                origin=call.origin,
            )
            self._keep_unmodelled(call)
            return
        handler(call)

    # ---------- evaluation-level findings ----------

    def _note_evaluation(self) -> None:
        recording = self.recording
        if recording.policy is Policy.PASSTHROUGH:
            self.report.add(
                LossCode.PASSTHROUGH_RUN,
                "Config was run with its side effects allowed, at your request",
            )
        for message in recording.errors:
            self.report.add(LossCode.EVAL_ERROR, message)
        for use in recording.shell:
            if use.kind == "importer.listdir":
                # The importer's own directory listing, resolving a wildcard `require`.
                # Recorded because an unrecorded process start would be a hole in the
                # sandbox's account of itself -- but it is not the config's doing, so it
                # is not that config's Breakage.
                self.report.add(
                    LossCode.EXTERNAL_STATE,
                    f"Listed {use.cmd}/ to resolve a wildcard require",
                    origin=use.src,
                    loss_class=LossClass.INFO,
                )
                continue
            self.report.add(
                LossCode.EXTERNAL_STATE,
                f"Config ran a command while loading ({use.kind}): {use.cmd}",
                origin=use.src,
            )
        for write in recording.writes:
            self.report.add(
                LossCode.EXTERNAL_STATE,
                f"Config wrote to {write.path} while loading",
            )
        for read in self._foreign_reads():
            self.report.add(
                LossCode.EXTERNAL_STATE,
                f"Config read {read.path} while loading; the imported copy holds whatever "
                "it said at import and will not read it again",
                origin=read.src,
            )
        for query in recording.queries:
            self.report.add(
                LossCode.CONFIG_TIME_QUERY,
                f"Config asked hl.{query.fn}() while loading; a stand-in answer was used, "
                "so whatever it decided from that is fixed as imported",
                origin=query.origin,
            )

    def _foreign_reads(self) -> list[Any]:
        """Files the config read from outside its own tree.

        Reading its own modules is how a config is written; reading a theme cache or a
        generated colour file is state the import bakes. Only the second is a finding, and
        the config directory is the line between them.
        """
        root = self.recording.basedir.resolve()
        foreign = []
        for read in self.recording.reads:
            try:
                candidate = Path(read.path)
                candidate = candidate if candidate.is_absolute() else root / candidate
                candidate.resolve().relative_to(root)
            except (ValueError, OSError):
                foreign.append(read)
        return foreign

    def _note_script_globals(self) -> None:
        """Closures reading names nothing will define for them once lifted."""
        if self.recording.scripts and luac_binary() is None:
            # Say so rather than reporting a clean bill: "no findings" and "nobody looked"
            # must not read the same on a report the user decides from.
            self.report.add(
                LossCode.FOREIGN_GLOBAL,
                f"{len(self.recording.scripts)} preserved script(s) could not be checked "
                "for globals they read: no luac is installed. Review legacy.lua by hand",
            )
            return
        for script in self.recording.scripts:
            for name in self.scripts.foreign_globals(script):
                self.report.add(
                    LossCode.FOREIGN_GLOBAL,
                    f"Preserved script reads `{name}`, which this config defined elsewhere; "
                    "check it still resolves",
                    origin=f"{script.source}:{script.start}",
                )

    # ---------- keeping what the model cannot hold ----------

    def _block(self, call: Call, body: str, label: str) -> None:
        definitions: list[str] = []
        seen: set[str] = set()
        for script_id in _script_ids(call.args):
            script = self.recording.script(script_id)
            if script is not None:
                definitions += self.scripts.upvalue_definitions(script, seen)
        header = f"-- {label} from {call.origin or 'the imported config'}"
        if definitions:
            indented = "\n  ".join([*definitions, body.replace("\n", "\n  ")])
            self.legacy.append(f"{header}\ndo\n  {indented}\nend\n")
        else:
            self.legacy.append(f"{header}\n{body}\n")

    def _keep_script(self, call: Call) -> None:
        args = _table(call.args)
        if call.name == "on":
            handler = args.get("handler")
            body = f"hl.on({lua_value(args.get('event'))}, {lua_value(handler, self.scripts)})"
            what = f"hl.on({args.get('event')!r}) handler"
        elif call.name == "layout_register":
            body = (
                f"hl.layout.register({lua_value(args.get('name'))}, "
                f"{lua_value(args.get('provider'), self.scripts)})"
            )
            what = f"custom layout {args.get('name')!r}"
        else:
            body = f"hl.timer({lua_value(call.args, self.scripts)})"
            what = "timer"
        self._block(call, body, "script")
        self.report.add(
            LossCode.SCRIPT_TO_LEGACY,
            f"{what} kept verbatim in legacy.lua and shown read-only",
            origin=call.origin,
        )

    def _keep_hybrid(self, call: Call) -> None:
        rendered = ", ".join(lua_value(arg, self.scripts) for arg in _positional(call))
        self._block(call, f"hl.{call.name}({rendered})", "inline function")
        self.report.add(
            LossCode.SCRIPT_TO_LEGACY,
            f"hl.{call.name} takes a function here, which the settings app cannot show; "
            "the whole call was kept verbatim in legacy.lua",
            origin=call.origin,
        )

    @staticmethod
    def _spelling(call: Call) -> str:
        """How the call was written, which is not always how it was recorded."""
        if call.name == "dispatch_immediate":
            return "hl.dispatch"
        if call.name.startswith("notification_"):
            return f"hl.notification.{call.name.removeprefix('notification_')}"
        if call.name in ("plugin_set", "plugin_call"):
            return "hl.plugin"
        return f"hl.{call.name}"

    def _keep_unmodelled(self, call: Call) -> None:
        rendered = ", ".join(lua_value(arg, self.scripts) for arg in _positional(call))
        self._block(call, f"{self._spelling(call)}({rendered})", "not modelled")

    # ---------- hl.config ----------

    def _map_config(self, call: Call) -> None:
        self._flatten(_table(call.args), (), self._context(call))

    def _flatten(
        self, table: Mapping[str, Any], prefix: tuple[str, ...], ctx: LossContext
    ) -> None:
        for key, value in table.items():
            path = (*prefix, str(key))
            option = self._paths.get(path)
            if isinstance(value, Mapping) and "__fn" in value:
                # A function inside `hl.config`. The model has nowhere to put one, and
                # walking into it would report the literal key `__fn` as a misspelled
                # setting -- a script construct silently dropped and blamed on a typo.
                self._keep_config_closure(path, value, ctx)
            elif option is not None:
                self._set(option, value, ctx)
            elif isinstance(value, Mapping):
                # Either a real subcategory or a typo; recursing reports the leaves, which
                # names the setting the user actually got wrong instead of its parent.
                self._flatten(value, path, ctx)
            else:
                ctx.note(
                    LossCode.UNSUPPORTED_KEYWORD,
                    f"{':'.join(path)} is not a setting this Hyprland has",
                )

    def _keep_config_closure(
        self, path: tuple[str, ...], value: Mapping[str, Any], ctx: LossContext
    ) -> None:
        """A function found inside an `hl.config` table, kept whole rather than dropped."""
        key = ":".join(path)
        rendered = lua_value(value, self.scripts)
        nested: dict[str, Any] = {}
        cursor = nested
        for step in path[:-1]:
            cursor[step] = {}
            cursor = cursor[step]
        cursor[path[-1]] = "@@FN@@"
        body = f"hl.config({lua_value(nested)})".replace('"@@FN@@"', rendered)
        self.legacy.append(f"-- script from {ctx.origin or 'the imported config'}\n{body}\n")
        ctx.note(
            LossCode.SCRIPT_TO_LEGACY,
            f"{key} is set to a function, which the settings app cannot show; the call was "
            "kept verbatim in legacy.lua",
        )

    def _set(self, option: ResolvedOption, value: Any, ctx: LossContext) -> None:
        if value is None:
            if option.nullable:
                self.model.set_null(option.name)
            else:
                ctx.note(LossCode.VALUE_NORMALISED, f"{option.name} was nil; left unset")
            return
        try:
            self.model.set(option.name, parse_lua(option, value))
        except (ValueError, TypeError) as error:
            ctx.note(
                LossCode.RULE_VALUE_TYPE,
                f"{option.name} had a value this build will not take ({error})",
            )

    # ---------- entities ----------

    def _map_bind(self, call: Call) -> None:
        args = _positional(call)
        if not args or not isinstance(args[0], str):
            self.report.add(
                LossCode.UNMODELLED_CALL, "hl.bind without a trigger", origin=call.origin
            )
            return
        dispatcher = self._dispatcher(args[1] if len(args) > 1 else None)
        options = self._bind_options(args[2] if len(args) > 2 else None)
        self.entities.binds.append(
            Bind(
                keys=args[0],
                dispatcher=dispatcher,
                options=options,
                submap=call.submap,
                origin=call.origin,
            )
        )

    def _dispatcher(self, value: Any) -> DispatcherCall | None:
        if not isinstance(value, Mapping) or not isinstance(value.get("__dsp"), str):
            return None
        raw = value.get("args")
        if isinstance(raw, Mapping):
            return DispatcherCall(path=str(value["__dsp"]), args=_table(raw))
        if isinstance(raw, list):
            if len(raw) == 1 and isinstance(raw[0], Mapping):
                return DispatcherCall(path=str(value["__dsp"]), args=_table(raw[0]))
            return DispatcherCall(path=str(value["__dsp"]), positional=tuple(raw))
        return DispatcherCall(path=str(value["__dsp"]))

    def _bind_options(self, value: Any) -> BindOptions:
        table = _table(value)
        flags = {name: bool(table[name]) for name in BIND_FLAGS if name in table}
        description = table.get("description")
        device = table.get("device")
        bind_device = None
        if isinstance(device, Mapping):
            names = device.get("names")
            bind_device = BindDevice(
                inclusive=bool(device.get("inclusive", True)),
                names=tuple(str(name) for name in names) if isinstance(names, list) else (),
            )
        return BindOptions(
            description=str(description) if isinstance(description, str) else "",
            device=bind_device,
            **flags,
        )

    def _map_unbind(self, call: Call) -> None:
        args = _positional(call)
        keys = args[0] if args and isinstance(args[0], str) else ""
        self.entities.unbinds.append(
            Unbind(keys=keys, all=not keys, submap=call.submap, origin=call.origin)
        )

    def _map_define_submap(self, call: Call) -> None:
        args = _table(call.args)
        name = args.get("name")
        if isinstance(name, str) and name:
            self.entities.submaps.append(
                Submap(name=name, reset_target=str(args.get("reset") or ""), origin=call.origin)
            )

    def _map_monitor(self, call: Call) -> None:
        fields = _table(call.args)
        output = fields.pop("output", "")
        rule = MonitorRule(output=str(output), fields=fields, origin=call.origin)
        self.entities.add_monitor_rule(rule, merge=True)

    def _rule_parts(self, call: Call) -> tuple[dict[str, Any], dict[str, Any], str, bool]:
        fields = _table(call.args)
        match = _table(fields.pop("match", {}))
        name = str(fields.pop("name", "") or "")
        enabled = bool(fields.pop("enabled", True))
        return match, fields, name, enabled

    def _map_window_rule(self, call: Call) -> None:
        match, effects, name, enabled = self._rule_parts(call)
        self.entities.add_window_rule(
            WindowRule(
                match=match, effects=effects, name=name, enabled=enabled, origin=call.origin
            )
        )

    def _map_layer_rule(self, call: Call) -> None:
        match, effects, name, enabled = self._rule_parts(call)
        self.entities.add_layer_rule(
            LayerRule(
                match=match, effects=effects, name=name, enabled=enabled, origin=call.origin
            )
        )

    def _map_workspace_rule(self, call: Call) -> None:
        fields = _table(call.args)
        workspace = str(fields.pop("workspace", "") or "")
        if not workspace:
            self.report.add(
                LossCode.UNMODELLED_CALL,
                "hl.workspace_rule without a workspace selector",
                origin=call.origin,
            )
            return
        self.entities.add_workspace_rule(
            WorkspaceRule(workspace=workspace, fields=fields, origin=call.origin)
        )

    def _map_animation(self, call: Call) -> None:
        fields = _table(call.args)
        leaf = str(fields.pop("leaf", "") or "")
        if not leaf:
            self.report.add(
                LossCode.UNMODELLED_CALL, "hl.animation without a leaf", origin=call.origin
            )
            return
        self.entities.add_animation(Animation(leaf=leaf, fields=fields, origin=call.origin))

    def _map_curve(self, call: Call) -> None:
        args = _positional(call)
        if len(args) < 2 or not isinstance(args[0], str):
            self.report.add(
                LossCode.UNMODELLED_CALL,
                "hl.curve without a name and a spec",
                origin=call.origin,
            )
            return
        self.entities.curves.append(
            Curve(name=args[0], spec=_table(args[1]), origin=call.origin)
        )

    def _map_device(self, call: Call) -> None:
        fields = _table(call.args)
        name = str(fields.pop("name", "") or "")
        if not name:
            self.report.add(
                LossCode.UNMODELLED_CALL, "hl.device without a name", origin=call.origin
            )
            return
        self.entities.add_device(Device(name=name, fields=fields, origin=call.origin))

    def _map_gesture(self, call: Call) -> None:
        self.entities.gestures.append(Gesture(fields=_table(call.args), origin=call.origin))

    def _map_env(self, call: Call) -> None:
        args = _positional(call)
        if len(args) < 2 or not isinstance(args[0], str):
            self.report.add(
                LossCode.UNMODELLED_CALL,
                "hl.env without a name and a value",
                origin=call.origin,
            )
            return
        self.entities.env.append(
            EnvVar(
                name=args[0],
                value=str(args[1]),
                dbus=bool(args[2]) if len(args) > 2 else False,
                origin=call.origin,
            )
        )

    def _map_permission(self, call: Call) -> None:
        args = _positional(call)
        if len(args) >= 3:
            binary, kind, mode = str(args[0]), str(args[1]), str(args[2])
        else:
            fields = _table(args[0] if args else None)
            binary = str(fields.get("binary") or fields.get("target") or "")
            kind, mode = str(fields.get("type") or ""), str(fields.get("mode") or "")
        if not (binary and kind and mode):
            self.report.add(
                LossCode.UNMODELLED_CALL, "hl.permission is missing a field", origin=call.origin
            )
            return
        self.entities.permissions.append(
            Permission(binary=binary, kind=kind, mode=mode, origin=call.origin)
        )

    def _map_plugin_load(self, call: Call) -> None:
        args = _positional(call)
        if args and isinstance(args[0], str):
            self.entities.plugins.append(PluginLoad(path=args[0], origin=call.origin))

    def _map_exec_cmd(self, call: Call) -> None:
        args = _positional(call)
        if args and isinstance(args[0], str):
            self.entities.startup.append(StartupCommand(command=args[0], origin=call.origin))


def map_recording(
    recording: Recording, schema: Schema, *, source: Path | None = None
) -> ImportResult:
    """A `Recording` -> model, Entities, `legacy.lua` and the Loss report."""
    return _Mapper(recording, schema, source=source).run()


def import_lua(
    path: Path,
    schema: Schema,
    *,
    consent: Consent,
    env: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ImportResult:
    """Evaluate a foreign `hyprland.lua` and map what it declared -- the wizard's entry.

    Mirrors `importer.import_config` for the hyprlang path, and reports through the same
    `LossReport`, so the wizard has one flow whichever kind of config it was handed.
    """
    recording = evaluate(path, consent=consent, env=env, timeout=timeout)
    return map_recording(recording, schema, source=path)


__all__ = ["import_lua", "map_recording"]
