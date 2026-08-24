"""Reading the seven declarative Entity Modules of #70 back into Entities.

One reader for all seven files rather than one per file, which is the same call
`parse_rules_module` and `parse_monitors_module` each make for their pair and for the same
reason: **a misfiled entity has to come back as what it is.** A user who pastes an
`hl.gesture` into `devices.lua` has written a config that works -- the Entrypoint requires
both -- and a reader that only looked for devices in `devices.lua` would report the gesture
as absent, at which point the next write would delete it. Collecting every kind from every
file makes that impossible by construction, and the writer puts each one back in its own
Module on the next render.

Everything else here follows the established shape: evaluation happens in the importer's
sandbox with consent granted, defensible because these are files this app wrote into its
own App dir which the Entrypoint already requires on every reload.

The one departure is `run_handlers`. Autostart's run-once commands live inside an
`hl.on("hyprland.start", ...)` block -- the only spelling Hyprland has for "once"
(`lua-api-surface.md` §14) -- and the recording stub captures handlers without entering
them, so those commands would be invisible to a plain read. Invisible means gone: the
model would hold no startup commands, the writer would render no Module, and the prune
would delete a user's autostart. So this reader asks the stub to enter handlers and reads
the `on_enter`/`on_leave` brackets it emits.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..entities_catalog import EVERY_RELOAD
from ..importer.lua.mapping import dispatcher_from_value, positional_args, table_fields
from ..importer.lua.sandbox import Call, Consent, LuaUnavailable, evaluate
from ..model.entities import (
    Animation,
    Curve,
    Device,
    EntitySet,
    EnvVar,
    Gesture,
    Permission,
    StartupCommand,
)
from ..paths import ANIMATIONS_MODULE
from .inputs import DISPATCH_FIELD


@dataclass(frozen=True, slots=True)
class ParsedDeclarations:
    """What one read of a declarative Entity Module found, by kind."""

    curves: tuple[Curve, ...] = ()
    animations: tuple[Animation, ...] = ()
    gestures: tuple[Gesture, ...] = ()
    devices: tuple[Device, ...] = ()
    env: tuple[EnvVar, ...] = ()
    permissions: tuple[Permission, ...] = ()
    startup: tuple[StartupCommand, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_declarations_module(
    source: str | Path, *, module: str = ANIMATIONS_MODULE, timeout: float = 5.0
) -> ParsedDeclarations:
    """Read one declarative Entity Module back into Entities.

    Takes text or a path. Every kind is collected regardless of which Module this is, so a
    hand edit that put an entity in the wrong file still comes back as what it is.
    """
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source

    # Evaluated from a scratch copy keeping the module's basename, so origins read as
    # `gestures.lua:N` with `N` the line in the real file.
    with tempfile.TemporaryDirectory(prefix="hyprtweaker-declarations-") as scratch:
        path = Path(scratch) / module
        path.write_text(text, encoding="utf-8")
        return _parse_path(path, timeout=timeout)


def _parse_path(path: Path, *, timeout: float) -> ParsedDeclarations:
    try:
        recording = evaluate(
            path, consent=Consent(evaluate=True), timeout=timeout, run_handlers=True
        )
    except LuaUnavailable as error:
        # No interpreter is an installation problem, not a config one; this must not look
        # like "the file declares nothing", which is what would license the prune.
        return ParsedDeclarations(errors=(str(error),))

    # Collected through EntitySet's adders so the merges Hyprland performs itself happen
    # here too: a hand edit declaring one leaf or one device twice comes back as the single
    # thing the compositor would see, not as two the next write would re-emit.
    entities = EntitySet()
    event = EVERY_RELOAD
    in_gesture_action = False

    for call in recording.calls:
        if call.name == "on_enter":
            event = str(table_fields(call.args).get("event", "") or "")
            continue
        if call.name == "on_leave":
            event = EVERY_RELOAD
            continue
        if call.name == "gesture_action_enter":
            in_gesture_action = True
            continue
        if call.name == "gesture_action_leave":
            in_gesture_action = False
            continue
        if in_gesture_action:
            # Inside the callback the app writes for an imported dispatcher gesture. The
            # dispatcher belongs to the gesture just recorded, not to the file at large --
            # collecting it as a top-level startup command would invent an autostart entry
            # out of a swipe.
            _attach_dispatch(entities, call)
            continue
        _collect(entities, call, event=event)

    return ParsedDeclarations(
        curves=tuple(entities.curves),
        animations=tuple(entities.animations),
        gestures=tuple(entities.gestures),
        devices=tuple(entities.devices),
        env=tuple(entities.env),
        permissions=tuple(entities.permissions),
        startup=tuple(entities.startup),
        errors=tuple(recording.errors),
    )


def _attach_dispatch(entities: EntitySet, call: Call) -> None:
    """Put a dispatcher recorded inside a gesture callback back on that gesture.

    The inverse of `writer/inputs.render_gesture`'s callback form, and the reason the
    round trip is exact rather than one-way: without it, an imported dispatcher gesture
    would come back as an opaque function on the first restart and stop being editable.
    """
    if call.name != "dispatch_immediate" or not entities.gestures:
        return
    dispatcher = dispatcher_from_value(call.args)
    if dispatcher is None:
        return
    gesture = entities.gestures[-1]
    fields = dict(gesture.fields)
    fields.pop("action", None)
    fields[DISPATCH_FIELD] = dispatcher
    entities.gestures[-1] = replace(gesture, fields=fields)


def _collect(entities: EntitySet, call: Call, *, event: str) -> None:
    """Put one recorded call on the right list, or ignore it.

    Ignoring is the right answer for most calls: these Modules are read with the same
    recorder `binds.lua` and `monitors.lua` use, so an `hl.bind` in the stream is simply
    another reader's business rather than an error.
    """
    if call.name == "curve":
        positional = positional_args(call)
        if positional and isinstance(positional[0], str):
            spec = table_fields(positional[1]) if len(positional) > 1 else {}
            entities.curves.append(Curve(name=positional[0], spec=spec, origin=call.origin))
        return

    if call.name == "animation":
        table = table_fields(call.args)
        leaf = table.pop("leaf", None)
        if isinstance(leaf, str) and leaf:
            # `add_animation` is last-write-wins per leaf, which is what the compositor
            # does; a file declaring one leaf twice comes back as the winner.
            entities.add_animation(Animation(leaf=leaf, fields=table, origin=call.origin))
        return

    if call.name == "gesture":
        entities.gestures.append(Gesture(fields=table_fields(call.args), origin=call.origin))
        return

    if call.name == "device":
        table = table_fields(call.args)
        name = table.pop("name", None)
        if isinstance(name, str) and name:
            entities.add_device(Device(name=name, fields=table, origin=call.origin))
        return

    if call.name == "env":
        positional = positional_args(call)
        if len(positional) >= 2 and isinstance(positional[0], str):
            entities.env.append(
                EnvVar(
                    name=positional[0],
                    value=str(positional[1]),
                    dbus=bool(positional[2]) if len(positional) > 2 else False,
                    origin=call.origin,
                )
            )
        return

    if call.name == "permission":
        positional = positional_args(call)
        # Both call shapes, because both are legal and a hand edit may use either: the
        # table form the app writes, and the positional form the upstream example uses.
        if len(positional) >= 3 and all(isinstance(item, str) for item in positional[:3]):
            table = {
                "binary": positional[0],
                "type": positional[1],
                "mode": positional[2],
            }
        else:
            table = table_fields(call.args)
        binary = table.get("binary") or table.get("target")
        if isinstance(binary, str) and binary:
            entities.permissions.append(
                Permission(
                    binary=binary,
                    kind=str(table.get("type", "")),
                    mode=str(table.get("mode", "")),
                    origin=call.origin,
                )
            )
        return

    if call.name == "exec_cmd":
        positional = positional_args(call)
        if positional and isinstance(positional[0], str):
            entities.startup.append(
                StartupCommand(command=positional[0], event=event, origin=call.origin)
            )
        return

    if call.name == "dispatch_immediate":
        # `hl.dispatch(hl.dsp.exec_raw("..."))` -- what the app writes for the legacy
        # `execr` family, whose point is that the `[rules] cmd` prefix is *not* parsed.
        command = _exec_raw_command(call.args)
        if command is not None:
            entities.startup.append(
                StartupCommand(command=command, event=event, raw=True, origin=call.origin)
            )


def _exec_raw_command(value: Any) -> str | None:
    """The command inside a recorded `hl.dsp.exec_raw(...)` marker, or `None`."""
    if not isinstance(value, dict) or value.get("__dsp") != "exec_raw":
        return None
    raw = value.get("args")
    if isinstance(raw, dict):
        raw = [item for _, item in sorted(raw.items())]
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        return raw[0]
    return None


__all__ = ["ParsedDeclarations", "parse_declarations_module"]
