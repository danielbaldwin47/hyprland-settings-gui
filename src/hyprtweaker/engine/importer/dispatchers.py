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
from pathlib import Path

from ..model.entities import DispatcherCall
from .loss import LossClass, LossCode, LossContext, LossReport
from .scalars import direction as _direction
from .scalars import number as _number

__all__ = [
    "DEAD_DISPATCHERS",
    "LEGACY_DISPATCHERS",
    "LEGACY_ENGINE_CALLS",
    "MAX_SCRIPT_BYTES",
    "ScriptLookup",
    "referenced_scripts",
    "scan_legacy_dispatch",
    "translate_dispatcher",
]


@dataclass(slots=True)
class _Ctx(LossContext):
    """A `LossContext` plus the one thing only dispatchers need: where scripts live.

    `exec` is the only grammar that reaches outside the config at all, and it needs the
    roots to resolve a script path against (ADR-0009's "referenced local scripts").
    """

    lookup: ScriptLookup | None = None


Grammar = Callable[[str, _Ctx], DispatcherCall | None]

#: Dispatchers that exist in neither engine at 0.56.2 -- the legacy line was already an
#: error before conversion, so there is nothing to translate (L11).
DEAD_DISPATCHERS: dict[str, str] = {
    "workspaceopt": "deprecated in hyprlang and absent from Lua",
    "setignoregrouplock": "a no-op in hyprlang and absent from Lua",
    "splitratio": "removed; the dwindle/scrolling layoutmsg strings replace it",
}


# --- small shared parsers ---------------------------------------------------------------


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


def _action(
    text: str,
    ctx: _Ctx,
    *,
    empty: str,
    enable_words: tuple[str, ...] = ("lock", "on", "enable"),
) -> str:
    """A toggle-style action word, resolved the way hyprlang resolved it.

    The trap this closes is that the two engines disagree about *unrecognised* words, not
    just missing ones. hyprlang's group dispatchers tested for their enable word and their
    toggle word and let everything else mean **disable**; Lua's parser maps everything it
    does not recognise to **toggle** (`LuaBindingsInternal.cpp:306-314`). So a passed-through
    `unlock` -- or `yes`, or the dispatcher's own name -- silently inverts.

    Every word is therefore resolved here to one of Lua's three, and anything unrecognised
    takes hyprlang's else-branch rather than travelling onward (L10).
    """
    word = text.strip().lower()
    if not word:
        ctx.note(
            LossCode.TOGGLE_DEFAULT,
            f"no action given, which meant {empty!r} in hyprlang but toggle in Lua",
            replacement=f'action = "{empty}"',
        )
        return empty
    if word in enable_words:
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
    ctx.note(
        LossCode.TOGGLE_DEFAULT,
        f"{word!r} is not an action word; hyprlang read anything unrecognised as "
        "disable, while Lua would read it as toggle",
        replacement='action = "off"',
    )
    return "off"


def _resize_params(text: str, ctx: _Ctx) -> dict[str, object]:
    """`[exact] X Y` -- the shared grammar of `resizeactive`/`moveactive`/`*pixel`.

    `exact` is Lua's `relative = false`; a bare pair is a delta.

    Percentages have no Lua field at all, and the bind is **dropped** rather than emitted
    with the bare number (L8). Emitting `20` for `20%` would produce a bind that silently
    does the wrong thing on every monitor -- which is worse than one that does nothing and
    says so in the report, and is the same call `fullscreenstate -1` gets a few functions
    below. Two equally unrepresentable arguments should not get two different fates.
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
            "percentage arguments have no Lua equivalent, and a plain number would mean "
            "pixels; the bind is dropped rather than silently resized",
        )
        return {}
    x = _number(tokens[0])
    y = _number(tokens[1])
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
        ctx.note(LossCode.UNSUPPORTED_KEYWORD, "exec with an empty command is an error in Lua")
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


_SCRIPT_SUFFIXES: frozenset[str] = frozenset({".sh", ".bash", ".zsh", ".fish", ".py", ""})

MAX_SCRIPT_BYTES = 256 * 1024
"""Cap on a scanned script. A shell script this big is not what the grep is for, and an
importer that reads an arbitrarily large file a config happens to name is a denial of
service with extra steps."""


@dataclass(frozen=True, slots=True)
class ScriptLookup:
    """Where to resolve a script path a command mentions.

    Both roots matter and neither is guessable from the command text: `~/bin/x.sh` needs
    the home the config was written for (the staged one under test, not the running
    user's), and `scripts/x.sh` is relative to the config directory.
    """

    home: Path | None = None
    config_dir: Path | None = None

    def resolve(self, token: str) -> Path | None:
        """A command token as a readable local file, or None if it is not one."""
        text = token.strip().strip("\"'`();&|")
        if not text or text.startswith("-"):
            return None
        if self.home is not None:
            if text.startswith("~/"):
                text = str(self.home / text[2:])
            elif text.startswith("$HOME/"):
                text = str(self.home / text[len("$HOME/") :])
            elif text.startswith("${HOME}/"):
                text = str(self.home / text[len("${HOME}/") :])
        if "$" in text:
            return None
        candidate = Path(text)
        if candidate.suffix.lower() not in _SCRIPT_SUFFIXES:
            return None
        options = [candidate] if candidate.is_absolute() else []
        if not candidate.is_absolute():
            for root in (self.config_dir, self.home):
                if root is not None:
                    options.append(root / candidate)
        for option in options:
            try:
                if option.is_file():
                    return option
            except OSError:  # pragma: no cover -- an unreadable path component
                continue
        return None


def _needle_in(text: str) -> str | None:
    lowered = text.lower()
    for needle in LEGACY_ENGINE_CALLS:
        if needle in lowered:
            return needle
    return None


def referenced_scripts(command: str, lookup: ScriptLookup) -> list[Path]:
    """Every local script file a command names, in the order it names them."""
    found: list[Path] = []
    for token in command.replace(";", " ").replace("&", " ").replace("|", " ").split():
        resolved = lookup.resolve(token)
        if resolved is not None and resolved not in found:
            found.append(resolved)
    return found


def scan_legacy_dispatch(
    command: str,
    *,
    origin: str,
    source: str,
    report: LossReport,
    lookup: ScriptLookup | None = None,
) -> bool:
    """Flag a command that drives the old config engine. True when one was found.

    These fragments speak the legacy engine's vocabulary. Swapping the engine leaves them
    syntactically fine and semantically dead, and no rewrite of the config can reach inside
    a shell script to fix them -- which is exactly what the Breakage class is for
    (ADR-0009). It is also the one breakage class a syntax check can never find, because
    the resulting config is perfectly valid.

    The scan follows the command into any local script it names, because ADR-0009 scopes it
    to "all exec strings *and referenced local scripts*" -- and a rice that keeps its
    dispatches in `~/.config/hypr/scripts/` is the common case, not the exotic one. Scripts
    are read, never run.
    """
    needle = _needle_in(command)
    if needle is not None:
        report.add(
            LossCode.LEGACY_DISPATCH_CALL,
            f"command runs `{needle}`, which drives the legacy config engine and "
            "stops working once the config is Lua",
            origin=origin,
            source=source,
        )
        return True
    if lookup is None:
        return False
    for script in referenced_scripts(command, lookup):
        found = _scan_script(script)
        if found is None:
            continue
        line, script_needle = found
        report.add(
            LossCode.LEGACY_DISPATCH_CALL,
            f"the script this command runs calls `{script_needle}` at {script.name}:{line}, "
            "which drives the legacy config engine and stops working once the config is Lua",
            origin=origin,
            source=source,
            replacement=str(script),
        )
        return True
    return False


def _scan_script(script: Path) -> tuple[int, str] | None:
    """The first line of a script that drives the legacy engine, if any."""
    try:
        if script.stat().st_size > MAX_SCRIPT_BYTES:
            return None
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        needle = _needle_in(line)
        if needle is not None:
            return number, needle
    return None


def _check_legacy_dispatch(command: str, ctx: _Ctx) -> None:
    scan_legacy_dispatch(
        command,
        origin=ctx.origin,
        source=ctx.source,
        report=ctx.report,
        lookup=ctx.lookup,
    )


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


def _change_group_active(args: str, ctx: _Ctx) -> DispatcherCall:
    word = args.strip().lower()
    if word in ("b", "prev"):
        return DispatcherCall("group.prev")
    if not word or word in ("f", "next"):
        return DispatcherCall("group.next")
    index = _number(word)
    if index is None:
        return DispatcherCall("group.next")
    if index <= 0:
        # hyprlang read an index of 0 or below as "the last window in the group"; Lua's
        # `group.active` takes the index straight through and has no spelling for that.
        ctx.note(
            LossCode.UNSUPPORTED_KEYWORD,
            f"changegroupactive {word} meant 'the last window' in hyprlang, which "
            "hl.dsp.group.active has no index for; passed through unchanged",
        )
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
    """`on` / `toggle` / else off -- note this one tests the literal word, not truthiness.

    `denywindowfromgroup 1` is *off* to hyprlang, because the translator compares against
    the string `on` rather than calling `truthy()` (`DispatcherTranslator.cpp:753-762`).
    Reading it as a boolean here would invert the rule for anyone who wrote `1`.
    """
    return DispatcherCall(
        "window.deny_from_group",
        {"action": _action(args, ctx, empty="off", enable_words=("on",))},
    )


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
            LossCode.UNSUPPORTED_KEYWORD,
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
    lookup: ScriptLookup | None = None,
) -> DispatcherCall | None:
    """Translate one legacy dispatcher call, filing any loss against `report`.

    Returns `None` when there is nothing to emit -- a dead dispatcher, an unknown name, or
    arguments the Lua side cannot express. The caller decides what that means; for a bind
    it means the bind is dropped, and the Loss report already says why.
    """
    lowered = name.strip().lower()
    ctx = _Ctx(
        report=report,
        origin=origin,
        source=source or f"{name} {args}".strip(),
        lookup=lookup,
    )
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
