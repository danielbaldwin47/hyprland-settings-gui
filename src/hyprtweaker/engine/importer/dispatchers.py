"""Legacy dispatcher name + argument string -> `hl.dsp.*` call.

The legacy engine had one flat namespace of 71 dispatcher names, each with its own
hand-written argument grammar -- some comma-split, some space-split, some split at the
*last* comma rather than the first. The Lua engine has a nested namespace of factories
taking typed tables. This module is that translation, one entry per legacy name.

Two things make it more than a rename table:

- **Argument grammars differ per dispatcher and are not guessable.** `signalwindow` splits
  at the first comma, `movetoworkspace` at the last, `setprop` on spaces with the window
  first. Getting one wrong produces a bind that runs the wrong thing rather than an error,
  so each grammar is transcribed from `DispatcherTranslator.cpp` rather than inferred.
- **Empty arguments mean opposite things on the two sides.** hyprlang read a missing
  `dpms`/`lockactivegroup` argument as *off*; Lua reads a missing action as *toggle*
  (L10). Every such default is written out explicitly here, because the faithful
  conversion is the one that says `"off"` where the user said nothing.

Unknown or dead names do not raise: they return `None` with a Loss finding, because a
config with one bad bind should still import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model.entities import DispatcherCall
from .loss import LossClass, LossCode, LossReport

__all__ = [
    "DEAD_DISPATCHERS",
    "LEGACY_DISPATCHERS",
    "LEGACY_ENGINE_CALLS",
    "scan_legacy_dispatch",
    "translate_dispatcher",
]


@dataclass(slots=True)
class _Ctx:
    """What a grammar needs besides its argument string: where to file findings."""

    origin: str
    report: LossReport
    source: str

    def note(
        self,
        code: LossCode,
        message: str,
        *,
        replacement: str = "",
        loss_class: LossClass | None = None,
    ) -> None:
        self.report.add(
            code,
            message,
            origin=self.origin,
            source=self.source,
            replacement=replacement,
            loss_class=loss_class,
        )


Grammar = Callable[[str, _Ctx], DispatcherCall | None]

#: Dispatchers that exist in neither engine at 0.56.2 -- the legacy line was already an
#: error before conversion, so there is nothing to translate (L11).
DEAD_DISPATCHERS: dict[str, str] = {
    "workspaceopt": "deprecated in hyprlang and absent from Lua",
    "setignoregrouplock": "a no-op in hyprlang and absent from Lua",
    "splitratio": "removed; the dwindle/scrolling layoutmsg strings replace it",
}


# --- small shared parsers ---------------------------------------------------------------


def _truthy(text: str) -> bool:
    """hyprlang's `truthy()`: `1`, or a `true`/`yes`/`on` prefix, case-insensitively."""
    lowered = text.strip().lower()
    return lowered == "1" or lowered.startswith(("true", "yes", "on"))


def _direction(text: str) -> str:
    """Legacy kept only the first character; Lua accepts that same letter."""
    stripped = text.strip().lower()
    return stripped[0] if stripped else ""


def _window(text: str) -> dict[str, str]:
    """A window selector field, omitted when it means "the focused window".

    Selector *grammar* is identical on both sides -- the string is passed through to the
    same `query().selector()` -- so the only work is recognising the two spellings of
    "no selector".
    """
    selector = text.strip()
    if not selector or selector == "active":
        return {}
    return {"window": selector}


def _number(text: str) -> float | int | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return int(stripped, 10)
    except ValueError:
        pass
    try:
        return float(stripped)
    except ValueError:
        return None


def _action(text: str, ctx: _Ctx, *, empty: str) -> str:
    """A toggle-style action word, with hyprlang's empty-means-`off` made explicit.

    `unlock` gets the same treatment: Lua maps any unrecognised action word to *toggle*, so
    passing `unlock` through would silently invert what the user asked for (L10).
    """
    word = text.strip().lower()
    if not word:
        ctx.note(
            LossCode.TOGGLE_DEFAULT,
            f"no action given, which meant {empty!r} in hyprlang but toggle in Lua",
            replacement=f'action = "{empty}"',
        )
        return empty
    if word in ("lock", "on", "enable"):
        return "on"
    if word == "toggle":
        return "toggle"
    if word in ("unlock", "off", "disable"):
        if word == "unlock":
            ctx.note(
                LossCode.TOGGLE_DEFAULT,
                "'unlock' is not an action word in Lua and would be read as toggle",
                replacement='action = "off"',
            )
        return "off"
    return word


def _resize_params(text: str, ctx: _Ctx) -> dict[str, object]:
    """`[exact] X Y` -- the shared grammar of `resizeactive`/`moveactive`/`*pixel`.

    `exact` is Lua's `relative = false`; a bare pair is a delta. Percentages have no Lua
    field at all: the numbers are kept as pixels and the finding is Breakage, because a
    window that moves by 20 pixels where the user asked for 20% is wrong in a way no
    warning-level message covers (L8).
    """
    tokens = text.split()
    relative = True
    if tokens and tokens[0].lower() == "exact":
        relative = False
        tokens = tokens[1:]
    if len(tokens) < 2:
        return {}
    if any("%" in token for token in tokens):
        ctx.note(
            LossCode.RESIZE_PERCENT,
            "percentage arguments have no Lua equivalent; kept as plain pixel numbers",
            replacement=" ".join(token.replace("%", "") for token in tokens[:2]),
        )
    x = _number(tokens[0].replace("%", ""))
    y = _number(tokens[1].replace("%", ""))
    if x is None or y is None:
        return {}
    return {"x": x, "y": y, "relative": relative}


def _split_last(text: str) -> tuple[str, str]:
    """Split at the last comma -- `movetoworkspace`'s grammar, so that a selector
    containing a comma still works."""
    head, sep, tail = text.rpartition(",")
    return (head, tail) if sep else (text, "")


# --- per-dispatcher grammars -------------------------------------------------------------


def _none(path: str) -> Grammar:
    def grammar(_: str, __: _Ctx) -> DispatcherCall:
        return DispatcherCall(path)

    return grammar


def _fixed(path: str, fields: dict[str, object]) -> Grammar:
    """A dispatcher whose legacy name *is* its argument -- `focusurgentorlast` takes no
    arguments but selects a flag on the shared `hl.dsp.focus` factory."""

    def grammar(_: str, __: _Ctx) -> DispatcherCall:
        return DispatcherCall(path, dict(fields))

    return grammar


def _window_only(path: str) -> Grammar:
    def grammar(args: str, __: _Ctx) -> DispatcherCall:
        return DispatcherCall(path, _window(args))

    return grammar


def _string_arg(path: str) -> Grammar:
    """A dispatcher taking one bare string argument, not a table."""

    def grammar(args: str, __: _Ctx) -> DispatcherCall:
        return DispatcherCall(path, positional=(args.strip(),))

    return grammar


def _exec(args: str, ctx: _Ctx) -> DispatcherCall | None:
    command = args.strip()
    if not command:
        ctx.note(LossCode.DEAD_DISPATCHER, "exec with an empty command is an error in Lua")
        return None
    _check_legacy_dispatch(command, ctx)
    return DispatcherCall("exec_cmd", positional=(command,))


def _exec_raw(args: str, ctx: _Ctx) -> DispatcherCall | None:
    command = args.strip()
    if not command:
        return None
    _check_legacy_dispatch(command, ctx)
    return DispatcherCall("exec_raw", positional=(command,))


LEGACY_ENGINE_CALLS: tuple[str, ...] = (
    "hyprctl dispatch",
    "hyprctl keyword",
    "hyprctl --batch",
    "hyprctl -j keyword",
)
"""Command fragments that drive the *legacy* config engine from outside the config.

Read as data about the user's config, never issued: the Engine's own route to Hyprland is
the socket (ADR-0010), and nothing here is ever run.
"""


def scan_legacy_dispatch(
    command: str,
    *,
    origin: str,
    source: str,
    report: LossReport,
) -> bool:
    """Flag a command that drives the old config engine. True when one was found.

    These fragments speak the legacy engine's vocabulary. Swapping the engine leaves them
    syntactically fine and semantically dead, and no rewrite of the config can reach inside
    a shell script to fix them -- which is exactly what the Breakage class is for
    (ADR-0009). It is also the one breakage class a syntax check can never find, because
    the resulting config is perfectly valid.
    """
    lowered = command.lower()
    for needle in LEGACY_ENGINE_CALLS:
        if needle in lowered:
            report.add(
                LossCode.LEGACY_DISPATCH_CALL,
                f"command runs `{needle}`, which drives the legacy config engine and "
                "stops working once the config is Lua",
                origin=origin,
                source=source,
            )
            return True
    return False


def _check_legacy_dispatch(command: str, ctx: _Ctx) -> None:
    scan_legacy_dispatch(command, origin=ctx.origin, source=ctx.source, report=ctx.report)


def _signal_window(args: str, __: _Ctx) -> DispatcherCall | None:
    window, _, signal = args.partition(",")
    number = _number(signal)
    if number is None:
        return None
    return DispatcherCall("window.signal", {"signal": number, **_window(window)})


def _signal(args: str, __: _Ctx) -> DispatcherCall | None:
    number = _number(args)
    if number is None:
        return None
    return DispatcherCall("window.signal", {"signal": number})


def _float_action(action: str) -> Grammar:
    def grammar(args: str, __: _Ctx) -> DispatcherCall:
        fields: dict[str, object] = {}
        if action:
            fields["action"] = action
        fields.update(_window(args))
        return DispatcherCall("window.float", fields)

    return grammar


def _workspace_focus(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("focus", {"workspace": args.strip()})


def _move_to_workspace(*, follow: bool) -> Grammar:
    def grammar(args: str, __: _Ctx) -> DispatcherCall:
        workspace, window = _split_last(args)
        fields: dict[str, object] = {"workspace": workspace.strip()}
        if not follow:
            fields["follow"] = False
        fields.update(_window(window))
        return DispatcherCall("window.move", fields)

    return grammar


def _rename_workspace(args: str, __: _Ctx) -> DispatcherCall:
    identifier, _, name = args.strip().partition(" ")
    fields: dict[str, object] = {"workspace": identifier}
    if name.strip():
        fields["name"] = name.strip()
    return DispatcherCall("workspace.rename", fields)


def _fullscreen(args: str, __: _Ctx) -> DispatcherCall:
    tokens = args.split()
    fields: dict[str, object] = {}
    if tokens:
        fields["mode"] = tokens[0]
    if len(tokens) > 1 and tokens[1] in ("toggle", "set", "unset"):
        fields["action"] = tokens[1]
    return DispatcherCall("window.fullscreen", fields)


def _fullscreen_state(args: str, ctx: _Ctx) -> DispatcherCall | None:
    tokens = args.split()
    if not tokens:
        return None
    if any(token == "-1" for token in tokens[:2]):
        ctx.note(
            LossCode.FULLSCREEN_STATE,
            "-1 means 'keep the current state', which the Lua dispatcher has no field for",
        )
        return None
    internal = _number(tokens[0])
    client = _number(tokens[1]) if len(tokens) > 1 else None
    if internal is None or client is None:
        ctx.note(
            LossCode.FULLSCREEN_STATE,
            "Lua requires both the internal and the client number",
        )
        return None
    fields: dict[str, object] = {"internal": internal, "client": client}
    if len(tokens) > 2:
        ctx.note(
            LossCode.FULLSCREEN_STATE,
            f"trailing action token {tokens[2]!r} was already ignored by hyprlang",
        )
    return DispatcherCall("window.fullscreen_state", fields)


def _move_focus(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("focus", {"direction": _direction(args)})


def _move_window(args: str, __: _Ctx) -> DispatcherCall:
    stripped = args.strip()
    if stripped.lower().startswith("mon:"):
        rest = stripped[4:].strip()
        silent = rest.endswith(" silent")
        monitor = rest[: -len(" silent")].strip() if silent else rest
        fields: dict[str, object] = {"monitor": monitor}
        if silent:
            fields["follow"] = False
        return DispatcherCall("window.move", fields)
    return DispatcherCall("window.move", {"direction": _direction(stripped)})


def _swap_window(args: str, __: _Ctx) -> DispatcherCall:
    stripped = args.strip()
    if stripped.lower() in ("l", "r", "u", "d", "left", "right", "up", "down"):
        return DispatcherCall("window.swap", {"direction": _direction(stripped)})
    return DispatcherCall("window.swap", {"target": stripped})


def _center_window(args: str, ctx: _Ctx) -> DispatcherCall:
    if args.strip():
        ctx.note(
            LossCode.DEAD_DISPATCHER,
            "the 'respect reserved area' argument was already ignored by hyprlang",
            loss_class=LossClass.INFO,
        )
    return DispatcherCall("window.center")


def _change_group_active(args: str, __: _Ctx) -> DispatcherCall:
    word = args.strip().lower()
    if word in ("b", "prev"):
        return DispatcherCall("group.prev")
    if not word or word in ("f", "next"):
        return DispatcherCall("group.next")
    index = _number(word)
    if index is None:
        return DispatcherCall("group.next")
    return DispatcherCall("group.active", {"index": index})


def _move_group_window(args: str, __: _Ctx) -> DispatcherCall:
    word = args.strip().lower()
    return DispatcherCall("group.move_window", {"forward": word not in ("b", "prev")})


def _focus_monitor(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("focus", {"monitor": args.strip()})


def _cursor_corner(args: str, __: _Ctx) -> DispatcherCall | None:
    corner = _number(args)
    if corner is None:
        return None
    return DispatcherCall("cursor.move_to_corner", {"corner": corner})


def _move_cursor(args: str, __: _Ctx) -> DispatcherCall | None:
    tokens = args.split()
    if len(tokens) < 2:
        return None
    x, y = _number(tokens[0]), _number(tokens[1])
    if x is None or y is None:
        return None
    return DispatcherCall("cursor.move", {"x": x, "y": y})


def _workspace_move_monitor(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("workspace.move", {"monitor": args.strip()})


def _focus_workspace_current_monitor(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("focus", {"workspace": args.strip(), "on_current_monitor": True})


def _move_workspace_to_monitor(args: str, __: _Ctx) -> DispatcherCall:
    workspace, _, monitor = args.strip().partition(" ")
    return DispatcherCall(
        "workspace.move", {"workspace": workspace, "monitor": monitor.strip()}
    )


def _toggle_special(args: str, __: _Ctx) -> DispatcherCall:
    name = args.strip()
    return DispatcherCall("workspace.toggle_special", positional=(name,) if name else ())


def _resize_active(path: str) -> Grammar:
    def grammar(args: str, ctx: _Ctx) -> DispatcherCall | None:
        fields = _resize_params(args, ctx)
        if not fields:
            return None
        return DispatcherCall(path, fields)

    return grammar


def _pixel(path: str) -> Grammar:
    def grammar(args: str, ctx: _Ctx) -> DispatcherCall | None:
        params, _, window = args.partition(",")
        fields = _resize_params(params, ctx)
        if not fields:
            return None
        fields.update(_window(window))
        return DispatcherCall(path, fields)

    return grammar


def _cycle_next(args: str, ctx: _Ctx) -> DispatcherCall:
    fields: dict[str, object] = {}
    for token in args.lower().split():
        if token in ("prev", "p", "last", "l"):
            fields["next"] = False
        elif token in ("next", "n"):
            fields["next"] = True
        elif token in ("tile", "tiled"):
            fields["tiled"] = True
        elif token in ("float", "floating"):
            fields["floating"] = True
        elif token in ("visible", "hist"):
            ctx.note(
                LossCode.DEAD_DISPATCHER,
                f"cyclenext argument {token!r} was already dropped by hyprlang",
                loss_class=LossClass.INFO,
            )
    return DispatcherCall("window.cycle_next", fields)


def _tag_window(args: str, __: _Ctx) -> DispatcherCall:
    tag, _, window = args.strip().partition(" ")
    return DispatcherCall("window.tag", {"tag": tag, **_window(window)})


def _send_shortcut(args: str, __: _Ctx) -> DispatcherCall | None:
    parts = args.split(",")
    if len(parts) < 2:
        return None
    fields: dict[str, object] = {"mods": parts[0].strip(), "key": parts[1].strip()}
    if len(parts) > 2:
        fields.update(_window(parts[2]))
    return DispatcherCall("send_shortcut", fields)


def _send_key_state(args: str, __: _Ctx) -> DispatcherCall | None:
    parts = args.split(",")
    if len(parts) < 3:
        return None
    fields: dict[str, object] = {
        "mods": parts[0].strip(),
        "key": parts[1].strip(),
        "state": parts[2].strip(),
    }
    if len(parts) > 3:
        fields.update(_window(parts[3]))
    return DispatcherCall("send_key_state", fields)


def _dpms(args: str, ctx: _Ctx) -> DispatcherCall:
    tokens = args.split()
    fields: dict[str, object] = {}
    if not tokens:
        ctx.note(
            LossCode.TOGGLE_DEFAULT,
            "dpms without an argument meant off in hyprlang but toggle in Lua",
            replacement='action = "off"',
        )
        fields["action"] = "off"
    else:
        word = tokens[0].lower()
        for known in ("on", "off", "toggle"):
            if word.startswith(known):
                fields["action"] = known
                break
        else:
            fields["action"] = "off"
        if len(tokens) > 1:
            fields["monitor"] = tokens[1]
    return DispatcherCall("dpms", fields)


def _swap_next(args: str, __: _Ctx) -> DispatcherCall:
    word = args.strip().lower()
    if word in ("l", "last", "prev", "b", "back"):
        return DispatcherCall("window.swap", {"prev": True})
    return DispatcherCall("window.swap", {"next": True})


def _swap_active_workspaces(args: str, __: _Ctx) -> DispatcherCall | None:
    tokens = args.split()
    if len(tokens) < 2:
        return None
    return DispatcherCall(
        "workspace.swap_monitors", {"monitor1": tokens[0], "monitor2": tokens[1]}
    )


def _alter_zorder(args: str, __: _Ctx) -> DispatcherCall:
    mode, _, window = args.partition(",")
    return DispatcherCall("window.alter_zorder", {"mode": mode.strip(), **_window(window)})


def _group_lock(path: str) -> Grammar:
    def grammar(args: str, ctx: _Ctx) -> DispatcherCall:
        return DispatcherCall(path, {"action": _action(args, ctx, empty="on")})

    return grammar


def _lock_active_group(args: str, ctx: _Ctx) -> DispatcherCall:
    return DispatcherCall("group.lock_active", {"action": _action(args, ctx, empty="off")})


def _move_into_group(field: str) -> Grammar:
    def grammar(args: str, __: _Ctx) -> DispatcherCall:
        return DispatcherCall("window.move", {field: _direction(args)})

    return grammar


def _move_out_of_group(args: str, __: _Ctx) -> DispatcherCall:
    fields: dict[str, object] = {"out_of_group": True}
    fields.update(_window(args))
    return DispatcherCall("window.move", fields)


def _move_window_or_group(args: str, __: _Ctx) -> DispatcherCall:
    return DispatcherCall("window.move", {"direction": _direction(args), "group_aware": True})


def _deny_from_group(args: str, ctx: _Ctx) -> DispatcherCall:
    word = args.strip().lower()
    if not word:
        action = "off"
    elif _truthy(word):
        action = "on"
    elif word == "toggle":
        action = "toggle"
    else:
        action = "off"
    return DispatcherCall("window.deny_from_group", {"action": action})


def _set_prop(args: str, __: _Ctx) -> DispatcherCall | None:
    tokens = args.strip().split(None, 2)
    if len(tokens) < 3:
        return None
    window, prop, value = tokens
    return DispatcherCall("window.set_prop", {"prop": prop, "value": value, **_window(window)})


def _force_idle(args: str, __: _Ctx) -> DispatcherCall | None:
    seconds = _number(args)
    if seconds is None:
        return None
    return DispatcherCall("force_idle", positional=(seconds,))


def _pass(args: str, ctx: _Ctx) -> DispatcherCall | None:
    fields = _window(args)
    if not fields:
        ctx.note(
            LossCode.DEAD_DISPATCHER,
            "pass requires a window selector in Lua; hyprlang allowed none",
        )
        return None
    return DispatcherCall("pass", fields)


def _mouse(args: str, ctx: _Ctx) -> DispatcherCall | None:
    """`bindm`'s internal dispatcher: `movewindow` / `resizewindow [1|2]` (L5)."""
    tokens = args.split()
    if not tokens:
        return None
    what = tokens[0].lower()
    if what == "movewindow":
        return DispatcherCall("window.drag")
    if what == "resizewindow":
        if len(tokens) > 1 and tokens[1] in ("1", "2"):
            return DispatcherCall("window.resize", {"keep_aspect_ratio": tokens[1] == "1"})
        return DispatcherCall("window.resize")
    ctx.note(LossCode.MOUSE_BIND, f"unknown mouse bind action {what!r}")
    return None


#: Every legacy dispatcher name, mapped to the grammar that reads its arguments.
LEGACY_DISPATCHERS: dict[str, Grammar] = {
    "exec": _exec,
    "execr": _exec_raw,
    "killactive": _none("window.close"),
    "forcekillactive": _none("window.kill"),
    "closewindow": _window_only("window.close"),
    "killwindow": _window_only("window.kill"),
    "signal": _signal,
    "signalwindow": _signal_window,
    "togglefloating": _float_action(""),
    "setfloating": _float_action("on"),
    "settiled": _float_action("off"),
    "pseudo": _window_only("window.pseudo"),
    "workspace": _workspace_focus,
    "movetoworkspace": _move_to_workspace(follow=True),
    "movetoworkspacesilent": _move_to_workspace(follow=False),
    "renameworkspace": _rename_workspace,
    "fullscreen": _fullscreen,
    "fullscreenstate": _fullscreen_state,
    "movefocus": _move_focus,
    "movewindow": _move_window,
    "swapwindow": _swap_window,
    "centerwindow": _center_window,
    "togglegroup": _none("group.toggle"),
    "changegroupactive": _change_group_active,
    "movegroupwindow": _move_group_window,
    "focusmonitor": _focus_monitor,
    "movecursortocorner": _cursor_corner,
    "movecursor": _move_cursor,
    "exit": _none("exit"),
    "movecurrentworkspacetomonitor": _workspace_move_monitor,
    "focusworkspaceoncurrentmonitor": _focus_workspace_current_monitor,
    "moveworkspacetomonitor": _move_workspace_to_monitor,
    "togglespecialworkspace": _toggle_special,
    "forcerendererreload": _none("force_renderer_reload"),
    "resizeactive": _resize_active("window.resize"),
    "moveactive": _resize_active("window.move"),
    "resizewindowpixel": _pixel("window.resize"),
    "movewindowpixel": _pixel("window.move"),
    "cyclenext": _cycle_next,
    "focuswindow": _window_only("focus"),
    "focuswindowbyclass": _window_only("focus"),
    "tagwindow": _tag_window,
    "toggleswallow": _none("window.toggle_swallow"),
    "submap": _string_arg("submap"),
    "pass": _pass,
    "sendshortcut": _send_shortcut,
    "sendkeystate": _send_key_state,
    "layoutmsg": _string_arg("layout"),
    "dpms": _dpms,
    "swapnext": _swap_next,
    "swapactiveworkspaces": _swap_active_workspaces,
    "pin": _window_only("window.pin"),
    "mouse": _mouse,
    "bringactivetotop": _none("window.bring_to_top"),
    "alterzorder": _alter_zorder,
    "focusurgentorlast": _fixed("focus", {"urgent_or_last": True}),
    "focuscurrentorlast": _fixed("focus", {"last": True}),
    "lockgroups": _group_lock("group.lock"),
    "lockactivegroup": _lock_active_group,
    "moveintogroup": _move_into_group("into_group"),
    "moveintoorcreategroup": _move_into_group("into_or_create_group"),
    "moveoutofgroup": _move_out_of_group,
    "movewindoworgroup": _move_window_or_group,
    "denywindowfromgroup": _deny_from_group,
    "event": _string_arg("event"),
    "global": _string_arg("global"),
    "setprop": _set_prop,
    "forceidle": _force_idle,
    "releaseinputcapture": _none("release_input_capture"),
}


def translate_dispatcher(
    name: str,
    args: str,
    *,
    origin: str,
    report: LossReport,
    source: str = "",
) -> DispatcherCall | None:
    """Translate one legacy dispatcher call, filing any loss against `report`.

    Returns `None` when there is nothing to emit -- a dead dispatcher, an unknown name, or
    arguments the Lua side cannot express. The caller decides what that means; for a bind
    it means the bind is dropped, and the Loss report already says why.
    """
    lowered = name.strip().lower()
    ctx = _Ctx(origin=origin, report=report, source=source or f"{name} {args}".strip())
    if lowered in DEAD_DISPATCHERS:
        ctx.note(
            LossCode.DEAD_DISPATCHER,
            f"{lowered!r} is {DEAD_DISPATCHERS[lowered]}",
        )
        return None
    grammar = LEGACY_DISPATCHERS.get(lowered)
    if grammar is None:
        ctx.note(LossCode.DEAD_DISPATCHER, f"unknown dispatcher {lowered!r}")
        return None
    return grammar(args, ctx)
