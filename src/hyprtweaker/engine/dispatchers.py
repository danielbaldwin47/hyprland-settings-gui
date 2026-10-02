"""The dispatcher catalog: every `hl.dsp.*` the picker can offer (ADR-0007).

The "Hyprland action" door of the two-door add flow is a picker over every dispatcher, so
something has to know what they all are. That something cannot be the Lua stub alone:
`/usr/share/hypr/stubs/hl.meta.lua` declares each one as `fun(...): HL.Dispatcher`, with no
argument types at all. The *names* are machine-readable; the *arguments* are not.

So the catalog is curated, and honest about which half is which:

- **the path set** is transcribed from the stub's namespace classes and is complete;
- **argument specs** are hand-written, and exist for the dispatchers whose argument shape
  is actually known from probing. Everything else is `free_form`: the editor
  offers a key/value table rather than a generated form (`coverage()` counts them).

`free_form` is a real answer, not a placeholder for one. A guessed form is worse than no
form -- it would present invented field names as though Hyprland documented them, and a
wrong key is either a config error or a silently ignored no-op. A free-form table lets a
user write the call they already know how to write, and the round-trip through `binds.lua`
preserves it exactly.

**`positional` is load-bearing and was settled by probing 0.56.2, not by reading.** The two
forms are not interchangeable and the compositor refuses the wrong one outright:

    hl.dsp.exec_cmd("kitty")            -- ok
    hl.dsp.exec_cmd{ command = "kitty" }  -- "bad argument 1: expected string, got table"
    hl.dsp.window.tag{ tag = "x" }      -- ok
    hl.dsp.window.tag("x")              -- "expected a table { tag, window? }"

So exec takes a bare string and `window.tag` takes a table, in opposite directions, and
nothing in the stub says so. The same probe found `workspace.move` requires `monitor` --
not the `workspace` its name suggests.

**Every dispatcher below was then asked of a nested 0.56.2, not read from docs (#126).**
`tests/integration/harness/dispatcher_probe.py` records, per dispatcher, the call forms the
compositor accepts, the keys it requires, and the keys it *reads*: an unknown key is silently
ignored, so a key is only listed here when a wrong-typed value for it raises or a fired call
visibly changes state. The record is `tests/golden/dispatcher-probe-0.56.2.json`. An entry
lists every key the compositor read, because the bind editor rebuilds a call from the listed
keys alone and a missing one would be dropped from a saved bind on its next edit. A shape
`ArgSpec` cannot say (exactly-one-of keys, alternative call shapes) or a key no probe could
confirm stays `free_form`, with its reason on the entry.

Engine-side and GTK-free on purpose: the picker is UI, but "what dispatchers exist" is a
fact about Hyprland, and the Binds writer needs it to validate a path it is about to emit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

ArgType = Literal["string", "int", "bool", "window", "workspace", "direction"]


@dataclass(frozen=True, slots=True)
class ArgSpec:
    """One argument of a dispatcher call."""

    name: str
    type: ArgType = "string"
    required: bool = False
    label: str = ""
    placeholder: str = ""

    def title(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


@dataclass(frozen=True, slots=True)
class Dispatcher:
    """One `hl.dsp.*` entry the picker can offer."""

    path: str
    """Dotted path under `hl.dsp`, e.g. `window.close` or `exec_cmd`."""

    label: str
    """What the picker calls it."""

    args: tuple[ArgSpec, ...] = ()
    """Curated argument specs. Empty plus `free_form` means "shape unknown"."""

    free_form: bool = False
    """Offer a raw key/value table instead of a generated form."""

    free_form_reason: str = ""
    """Why no generated form is honest, in one line. Required of every `free_form` entry."""

    positional: bool = False
    """Takes bare arguments rather than a table (`hl.dsp.submap("resize")`)."""

    @property
    def namespace(self) -> str:
        return self.path.rsplit(".", 1)[0] if "." in self.path else ""

    @property
    def leaf(self) -> str:
        return self.path.rsplit(".", 1)[-1]


def _plain(path: str, label: str) -> Dispatcher:
    """A dispatcher that takes nothing -- the shape the stub does tell us about."""
    return Dispatcher(path=path, label=label)


def _free_form(path: str, label: str, why: str) -> Dispatcher:
    """A dispatcher no generated form can describe truthfully, and the reason, in one line."""
    return Dispatcher(path=path, label=label, free_form=True, free_form_reason=why)


WINDOW = ArgSpec(
    name="window",
    type="window",
    label="Window",
    placeholder="class:firefox, title:.*, activewindow",
)

ACTION = ArgSpec(
    name="action",
    label="Action",
    placeholder="toggle, enable or disable",
)
"""`float` and `pin` read it (probed: `enable` twice holds still, a toggle would not)."""

CATALOG: tuple[Dispatcher, ...] = (
    # --- root -------------------------------------------------------------------------
    Dispatcher(
        path="exec_cmd",
        label="Run a command",
        args=(
            ArgSpec(
                name="command",
                required=True,
                label="Command",
                placeholder="kitty",
            ),
        ),
        positional=True,
    ),
    Dispatcher(
        path="exec_raw",
        label="Run a command without shell processing",
        args=(ArgSpec(name="command", required=True, label="Command"),),
        positional=True,
    ),
    Dispatcher(
        path="submap",
        label="Switch to a submap",
        args=(ArgSpec(name="name", required=True, label="Submap"),),
        positional=True,
    ),
    Dispatcher(
        path="global",
        label="Trigger a global shortcut",
        args=(
            ArgSpec(
                name="name",
                required=True,
                label="Shortcut",
                placeholder="quickshell:searchToggle",
            ),
        ),
        positional=True,
    ),
    _plain("exit", "Exit Hyprland"),
    _plain("force_renderer_reload", "Reload the renderer"),
    Dispatcher(
        path="force_idle",
        label="Force idle",
        args=(ArgSpec(name="seconds", type="int", required=True, label="Seconds"),),
        positional=True,
    ),
    _plain("no_op", "Do nothing"),
    Dispatcher(
        path="pass",
        label="Pass the key through",
        args=(replace(WINDOW, required=True),),
    ),
    _plain("release_input_capture", "Release input capture"),
    Dispatcher(
        path="dpms",
        label="Turn displays on or off",
        args=(
            ArgSpec(name="action", label="Action", placeholder="on, off or toggle"),
            ArgSpec(name="monitor", label="Monitor", placeholder="DP-1"),
        ),
    ),
    Dispatcher(
        path="event",
        label="Emit a custom event",
        args=(ArgSpec(name="data", required=True, label="Event data"),),
        positional=True,
    ),
    _free_form(
        "focus",
        "Move focus",
        "takes exactly one of direction, monitor, workspace, window, urgent_or_last or last",
    ),
    Dispatcher(
        path="layout",
        label="Send a layout message",
        args=(
            ArgSpec(
                name="message",
                required=True,
                label="Message",
                placeholder="togglesplit",
            ),
        ),
        positional=True,
    ),
    Dispatcher(
        path="send_key_state",
        label="Send a key state",
        args=(
            ArgSpec(name="mods", required=True, label="Modifiers", placeholder="SUPER"),
            ArgSpec(name="key", required=True, label="Key", placeholder="a"),
            ArgSpec(
                name="state", required=True, label="State", placeholder="down, up or repeat"
            ),
            WINDOW,
        ),
    ),
    Dispatcher(
        path="send_shortcut",
        label="Send a shortcut to a window",
        args=(
            ArgSpec(name="mods", required=True, label="Modifiers", placeholder="SUPER"),
            ArgSpec(name="key", required=True, label="Key", placeholder="a"),
            WINDOW,
        ),
    ),
    # --- cursor -----------------------------------------------------------------------
    Dispatcher(
        path="cursor.move",
        label="Move the cursor",
        args=(
            ArgSpec(name="x", type="int", required=True, label="X"),
            ArgSpec(name="y", type="int", required=True, label="Y"),
        ),
    ),
    Dispatcher(
        path="cursor.move_to_corner",
        label="Move the cursor to a corner",
        args=(
            ArgSpec(
                name="corner",
                type="int",
                required=True,
                label="Corner",
                placeholder="0 to 3",
            ),
            WINDOW,
        ),
    ),
    # --- group ------------------------------------------------------------------------
    Dispatcher(path="group.toggle", label="Toggle group", args=(WINDOW,)),
    _free_form(
        "group.lock",
        "Lock the group",
        "its action only shows when fired at a grouped window, so no probe confirmed the key",
    ),
    _free_form(
        "group.lock_active",
        "Lock the active group",
        "its action only shows when fired at a grouped window, so no probe confirmed the key",
    ),
    Dispatcher(path="group.next", label="Focus the next window in the group", args=(WINDOW,)),
    Dispatcher(
        path="group.prev", label="Focus the previous window in the group", args=(WINDOW,)
    ),
    Dispatcher(
        path="group.active",
        label="Focus a group member",
        args=(ArgSpec(name="index", type="int", required=True, label="Index"), WINDOW),
    ),
    _free_form(
        "group.move_window",
        "Move a window out of the group",
        "the compositor reads no key when it is built, and `forward` was never seen to act",
    ),
    # --- window -----------------------------------------------------------------------
    Dispatcher(path="window.close", label="Close the window", args=(WINDOW,)),
    Dispatcher(path="window.kill", label="Force-kill the window", args=(WINDOW,)),
    Dispatcher(path="window.center", label="Centre the window", args=(WINDOW,)),
    Dispatcher(path="window.float", label="Toggle floating", args=(WINDOW, ACTION)),
    Dispatcher(path="window.pin", label="Pin the window", args=(WINDOW, ACTION)),
    Dispatcher(path="window.pseudo", label="Toggle pseudo-tiling", args=(WINDOW, ACTION)),
    _plain("window.bring_to_top", "Bring the window to the top"),
    _plain("window.toggle_swallow", "Toggle swallowing"),
    _free_form(
        "window.deny_from_group",
        "Deny the window from a group",
        "the compositor reads no key when it is built, and an `action` was never seen to act",
    ),
    Dispatcher(
        path="window.fullscreen",
        label="Toggle fullscreen",
        args=(
            WINDOW,
            ArgSpec(name="mode", label="Mode", placeholder="fullscreen or maximized"),
            ArgSpec(name="action", label="Action", placeholder="toggle, set or unset"),
        ),
    ),
    Dispatcher(
        path="window.tag",
        label="Tag the window",
        args=(WINDOW, ArgSpec(name="tag", required=True, label="Tag")),
    ),
    Dispatcher(path="window.clear_tags", label="Clear the window's tags", args=(WINDOW,)),
    Dispatcher(
        path="window.signal",
        label="Send a signal to the window",
        args=(WINDOW, ArgSpec(name="signal", type="int", required=True, label="Signal")),
    ),
    Dispatcher(
        path="window.alter_zorder",
        label="Change the window's z-order",
        args=(
            ArgSpec(name="mode", required=True, label="Mode", placeholder="top or bottom"),
            WINDOW,
        ),
    ),
    Dispatcher(
        path="window.cycle_next",
        label="Focus the next window",
        args=(
            WINDOW,
            ArgSpec(name="next", type="bool", label="Forwards", placeholder="false goes back"),
            ArgSpec(name="tiled", type="bool", label="Tiled only", placeholder="true"),
            ArgSpec(name="floating", type="bool", label="Floating only", placeholder="true"),
        ),
    ),
    _plain("window.drag", "Drag the window"),
    Dispatcher(
        path="window.fullscreen_state",
        label="Set the fullscreen state",
        args=(
            ArgSpec(name="internal", type="int", required=True, label="Internal state"),
            ArgSpec(name="client", type="int", required=True, label="Client state"),
            ArgSpec(name="action", label="Action", placeholder="toggle, set or unset"),
            WINDOW,
        ),
    ),
    _free_form(
        "window.move",
        "Move the window",
        "takes exactly one of direction, x and y, workspace, monitor or a group move",
    ),
    _free_form(
        "window.resize",
        "Resize the window",
        "three call shapes: no arguments, x and y with relative, or keep_aspect_ratio",
    ),
    Dispatcher(
        path="window.set_prop",
        label="Set a window property",
        args=(
            ArgSpec(name="prop", required=True, label="Property", placeholder="opaque"),
            ArgSpec(name="value", required=True, label="Value", placeholder="1"),
            WINDOW,
        ),
    ),
    _free_form(
        "window.swap",
        "Swap the window",
        "takes exactly one of direction, target, next or prev",
    ),
    # --- workspace --------------------------------------------------------------------
    Dispatcher(
        path="workspace.change_id",
        label="Change a workspace's id",
        args=(
            ArgSpec(name="workspace", type="workspace", required=True, label="Workspace"),
            ArgSpec(name="id", type="int", required=True, label="New id"),
        ),
    ),
    Dispatcher(
        path="workspace.move",
        label="Move to a workspace",
        args=(
            ArgSpec(name="monitor", required=True, label="Monitor", placeholder="DP-1"),
            ArgSpec(name="workspace", type="workspace", label="Workspace"),
        ),
    ),
    Dispatcher(
        path="workspace.rename",
        label="Rename a workspace",
        args=(
            ArgSpec(name="workspace", type="workspace", required=True, label="Workspace"),
            ArgSpec(name="name", label="Name"),
        ),
    ),
    Dispatcher(
        path="workspace.swap_monitors",
        label="Swap workspaces between monitors",
        args=(
            ArgSpec(name="monitor1", required=True, label="First monitor", placeholder="DP-1"),
            ArgSpec(name="monitor2", required=True, label="Second monitor", placeholder="DP-2"),
        ),
    ),
    Dispatcher(
        path="workspace.toggle_special",
        label="Toggle the special workspace",
        args=(ArgSpec(name="name", label="Name", placeholder="magic"),),
        positional=True,
    ),
)

BY_PATH: dict[str, Dispatcher] = {entry.path: entry for entry in CATALOG}

NAMESPACE_LABELS: dict[str, str] = {
    "": "General",
    "cursor": "Cursor",
    "group": "Groups",
    "window": "Windows",
    "workspace": "Workspaces",
}

EXEC_PATH = "exec_cmd"
"""The "Run command" door. Named because two UI surfaces branch on it (ADR-0007)."""


def namespaces() -> dict[str, list[Dispatcher]]:
    """The catalog grouped for the picker, in catalog order within each namespace."""
    grouped: dict[str, list[Dispatcher]] = {name: [] for name in NAMESPACE_LABELS}
    for entry in CATALOG:
        grouped.setdefault(entry.namespace, []).append(entry)
    return {name: entries for name, entries in grouped.items() if entries}


@dataclass(frozen=True, slots=True)
class Coverage:
    """How much of the catalog has a generated form, by path, in catalog order."""

    curated: tuple[str, ...]
    """Entries with argument specs: the editor offers a generated form."""

    plain: tuple[str, ...]
    """Entries that take nothing."""

    free_form: tuple[str, ...]
    """Entries the editor leaves to a raw key/value table, each with its reason."""


def coverage() -> Coverage:
    """The curated / plain / free_form split of the catalog (#126).

    `free_form` is a valid answer and this is where it is counted, so the number can only
    fall for a reason a probe gave: an entry leaves the group when a nested compositor has
    shown a form that does not lose a key (`tests/integration/test_dispatcher_probe.py`).
    """
    curated = tuple(entry.path for entry in CATALOG if entry.args)
    free = tuple(entry.path for entry in CATALOG if entry.free_form)
    plain = tuple(entry.path for entry in CATALOG if not entry.args and not entry.free_form)
    return Coverage(curated=curated, plain=plain, free_form=free)


def lookup(path: str) -> Dispatcher | None:
    """The catalog entry for a dotted path, or `None` for one this build has never heard of.

    `None` is expected, not exceptional: a config written for a newer Hyprland, or a plugin
    dispatcher, is a path this build cannot know. Callers render it read-only rather than
    dropping it -- the same contract the Options half keeps for unknown keys (ADR-0012).
    """
    return BY_PATH.get(path)


__all__ = [
    "BY_PATH",
    "CATALOG",
    "EXEC_PATH",
    "NAMESPACE_LABELS",
    "ArgSpec",
    "ArgType",
    "Coverage",
    "Dispatcher",
    "coverage",
    "lookup",
    "namespaces",
]
