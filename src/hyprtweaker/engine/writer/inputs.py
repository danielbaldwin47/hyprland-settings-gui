"""The canonical `gestures.lua` and `devices.lua` Modules (#70).

Both are input surfaces the compositor resets on every reload and rebuilds from the file
(`lua-api-surface.md` §0), so omission really is deletion here and the renderers can stay
as simple as the rule ones: whatever the model holds, one call per line, in order.

They differ in identity. A gesture has none -- two three-finger horizontal swipes are two
gestures, keyed internally by their own trigger, and the model keeps them by position. A
device *is* its name, and `hl.device` merges per name (`EntitySet.add_device`), so what is
written is one call per device carrying every field the user set on it.
"""

from __future__ import annotations

from ..entities_catalog import is_scripted
from ..model.entities import Device, DispatcherCall, Gesture
from ..model.values import lua_string
from .binds import lua_value, render_dispatcher
from .lua import ordered_fields, render_entity_module, table_key

_GESTURE_KEY_ORDER = ("fingers", "direction", "action")
"""The three required keys, in the order the legacy `gesture =` keyword took them.

Everything after them is optional and sorts alphabetically, so a gesture line always opens
with what it *is* -- three fingers, horizontal, workspace -- before how it is tuned.
"""


DISPATCH_FIELD = "dispatch"
"""Where the hyprlang importer parks a `gesture = …, dispatcher, NAME, ARGS` action.

Not a key `hl.gesture` accepts -- there is no string dispatcher action in Lua at all
(`hyprlang-to-lua.md` §2.10.2, loss code L12). The importer keeps the parsed
`DispatcherCall` under this name and flags the loss; turning it into config is this
renderer's job, and the only spelling Hyprland has for it is a callback.
"""


def render_gesture(gesture: Gesture) -> str:
    """One `hl.gesture({...})` line: the trigger, then the tuning.

    A gesture carrying a `DispatcherCall` renders its action as a callback --
    `action = function() hl.dispatch(hl.dsp.…()) end` -- because that is the only form the
    parser takes. Emitting the `dispatch` key itself would put a field name Hyprland has
    never heard of into the file, which is not a degraded gesture but a broken Module.
    """
    fields = {key: value for key, value in gesture.fields.items() if key != DISPATCH_FIELD}
    parts = [
        f"{table_key(str(key))}{lua_value(value)}"
        for key, value in ordered_fields(fields, first=_GESTURE_KEY_ORDER)
    ]
    call = gesture.fields.get(DISPATCH_FIELD)
    if isinstance(call, DispatcherCall):
        parts.append(f"action = function() hl.dispatch({render_dispatcher(call)}) end")
    return f"hl.gesture({{ {', '.join(parts)} }})"


def render_device(device: Device) -> str:
    """One `hl.device({...})` line: the name first, then the per-device overrides."""
    parts = [f"name = {lua_string(device.name)}"]
    parts.extend(
        f"{table_key(str(key))}{lua_value(value)}"
        for key, value in ordered_fields(device.fields)
    )
    return f"hl.device({{ {', '.join(parts)} }})"


def render_gestures_module(gestures: list[Gesture], *, app_version: str) -> str | None:
    """The whole `gestures.lua`, or `None` when there is nothing to write.

    A gesture whose action is a Lua function is skipped rather than mangled: the model
    holds it so the Page can list it read-only, but its action is code the app never
    authored and cannot spell back out (`entities_catalog.is_scripted`, the rule ADR-0007
    set for function-valued bind actions). Such a gesture lives in `user.lua`, which is
    where it came from and where it keeps working.
    """
    return render_entity_module(
        [render_gesture(gesture) for gesture in gestures if not is_scripted(gesture)],
        comment="Touchpad and touchscreen gestures.",
        app_version=app_version,
    )


def render_devices_module(devices: list[Device], *, app_version: str) -> str | None:
    """The whole `devices.lua`, or `None` when there is nothing to write.

    A device with no fields is still written. It is how a user says "this device, default
    everything" while they experiment, and a row that vanished on its own the moment its
    last override was cleared would be the app deleting something the user is holding.
    """
    return render_entity_module(
        [render_device(device) for device in devices],
        comment="Per-device input settings. These win over the matching Input options.",
        app_version=app_version,
    )


__all__ = [
    "render_device",
    "render_devices_module",
    "render_gesture",
    "render_gestures_module",
]
