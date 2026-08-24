"""The dispatcher catalog: every `hl.dsp.*` the picker can offer (ADR-0007).

The "Hyprland action" door of the two-door add flow is a picker over every dispatcher, so
something has to know what they all are. That something cannot be the Lua stub alone:
`/usr/share/hypr/stubs/hl.meta.lua` declares each one as `fun(...): HL.Dispatcher`, with no
argument types at all. The *names* are machine-readable; the *arguments* are not.

So the catalog is curated, and honest about which half is which:

- **the path set** is transcribed from the stub's namespace classes and is complete;
- **argument specs** are hand-written, and exist for the dispatchers whose argument shape
  is actually known from research and probing. Everything else is `free_form`: the editor
  offers a key/value table rather than a generated form.

`free_form` is a real answer, not a placeholder for one. A guessed form is worse than no
form -- it would present invented field names as though Hyprland documented them, and a
wrong key is a config error, not a no-op. A free-form table lets a user write the call they
already know how to write, and the round-trip through `binds.lua` preserves it exactly.

**`positional` is load-bearing and was settled by probing 0.56.2, not by reading.** The two
forms are not interchangeable and the compositor refuses the wrong one outright:

    hl.dsp.exec_cmd("kitty")            -- ok
    hl.dsp.exec_cmd{ command = "kitty" }  -- "bad argument 1: expected string, got table"
    hl.dsp.window.tag{ tag = "x" }      -- ok
    hl.dsp.window.tag("x")              -- "expected a table { tag, window? }"

So exec takes a bare string and `window.tag` takes a table, in opposite directions, and
nothing in the stub says so. The same probe found `workspace.move` requires `monitor` --
not the `workspace` its name suggests -- which is why it stays `free_form` rather than
carrying a field this module would have got wrong.

Engine-side and GTK-free on purpose: the picker is UI, but "what dispatchers exist" is a
fact about Hyprland, and the Binds writer needs it to validate a path it is about to emit.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _unknown(path: str, label: str) -> Dispatcher:
    """A dispatcher whose argument shape is not documented anywhere machine-readable."""
    return Dispatcher(path=path, label=label, free_form=True)


WINDOW = ArgSpec(
    name="window",
    type="window",
    label="Window",
    placeholder="class:firefox, title:.*, activewindow",
)

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
    _plain("force_idle", "Force idle"),
    _plain("no_op", "Do nothing"),
    _plain("pass", "Pass the key through"),
    _plain("release_input_capture", "Release input capture"),
    _unknown("dpms", "Turn displays on or off"),
    _unknown("event", "Emit a custom event"),
    _unknown("focus", "Move focus"),
    _unknown("layout", "Send a layout message"),
    _unknown("send_key_state", "Send a key state"),
    _unknown("send_shortcut", "Send a shortcut to a window"),
    # --- cursor -----------------------------------------------------------------------
    _unknown("cursor.move", "Move the cursor"),
    _unknown("cursor.move_to_corner", "Move the cursor to a corner"),
    # --- group ------------------------------------------------------------------------
    _plain("group.toggle", "Toggle group"),
    _plain("group.lock", "Lock the group"),
    _plain("group.lock_active", "Lock the active group"),
    _plain("group.next", "Focus the next window in the group"),
    _plain("group.prev", "Focus the previous window in the group"),
    _unknown("group.active", "Focus a group member"),
    _unknown("group.move_window", "Move a window out of the group"),
    # --- window -----------------------------------------------------------------------
    Dispatcher(path="window.close", label="Close the window", args=(WINDOW,)),
    Dispatcher(path="window.kill", label="Force-kill the window", args=(WINDOW,)),
    Dispatcher(path="window.center", label="Centre the window", args=(WINDOW,)),
    Dispatcher(path="window.float", label="Toggle floating", args=(WINDOW,)),
    Dispatcher(path="window.pin", label="Pin the window", args=(WINDOW,)),
    Dispatcher(path="window.pseudo", label="Toggle pseudo-tiling", args=(WINDOW,)),
    Dispatcher(path="window.bring_to_top", label="Bring the window to the top", args=(WINDOW,)),
    Dispatcher(path="window.toggle_swallow", label="Toggle swallowing", args=(WINDOW,)),
    Dispatcher(
        path="window.deny_from_group", label="Deny the window from a group", args=(WINDOW,)
    ),
    Dispatcher(
        path="window.fullscreen",
        label="Toggle fullscreen",
        args=(WINDOW, ArgSpec(name="mode", label="Mode", placeholder="maximize")),
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
    _unknown("window.alter_zorder", "Change the window's z-order"),
    _unknown("window.cycle_next", "Focus the next window"),
    _unknown("window.drag", "Drag the window"),
    _unknown("window.fullscreen_state", "Set the fullscreen state"),
    _unknown("window.move", "Move the window"),
    _unknown("window.resize", "Resize the window"),
    _unknown("window.set_prop", "Set a window property"),
    _unknown("window.swap", "Swap the window"),
    # --- workspace --------------------------------------------------------------------
    _unknown("workspace.change_id", "Change a workspace's id"),
    _unknown("workspace.move", "Move to a workspace"),
    _unknown("workspace.rename", "Rename a workspace"),
    _unknown("workspace.swap_monitors", "Swap workspaces between monitors"),
    _unknown("workspace.toggle_special", "Toggle the special workspace"),
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
    "Dispatcher",
    "lookup",
    "namespaces",
]
