"""Rendering one Module per Section, and the Entrypoint that requires them.

Two renderers, both pure: model in, Lua text out. Nothing here touches the filesystem --
that is `writer.Writer`, which needs these to be pure so it can render, syntax-gate, and
only then decide whether the bytes are worth writing at all.

**One Module per Section.** `hl.config` merges per leaf, so a config split across 21 files
that each set disjoint keys means the same thing as one file that sets all of them
(research `lua-api-surface.md` §1). Splitting buys per-file blast radius: each `require`d
module runs in its own pcall scope, so a broken Module loses its own values and nothing
else -- and rollback is restoring one file's previous bytes.

**Absence is the default.** Unset Options are simply not in the tree. Nothing is emitted to
say "leave this alone", because a reload resets every value first.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..model.values import lua_literal_for, lua_string
from ..paths import (
    ANIMATIONS_MODULE,
    AUTOSTART_MODULE,
    BINDS_MODULE,
    DEVICES_MODULE,
    ENV_MODULE,
    GESTURES_MODULE,
    LAYER_RULES_MODULE,
    MONITORS_MODULE,
    OPTIONS_DIR,
    PERMISSIONS_MODULE,
    PLUGINS_MODULE,
    WINDOW_RULES_MODULE,
    WORKSPACE_RULES_MODULE,
)
from ..schema import ResolvedOption, is_plugin_option
from .lua import GENERATED_BANNER, INDENT, LuaTree, insert, render_table


def module_stem(option: ResolvedOption) -> str:
    """The Module filename stem for an Option's Section.

    The Lua spelling of the Section (`input_capture`), not the hyprctl spelling
    (`input-capture`): it is the same token that opens the table inside the file, and it is
    guaranteed to be a plain identifier, so no Section can ever need a quoted require path.
    """
    return option.path[0]


def module_relpath(option: ResolvedOption) -> str:
    """Where a Section's Module lives inside the App dir.

    `options/` is a subdirectory rather than a flat `<section>.lua` because `binds` is both
    a Section and an Entity kind, and the two Modules would collide (ADR-0005).
    """
    return f"{OPTIONS_DIR}/{module_stem(option)}.lua"


def is_option_module(relpath: str) -> bool:
    """Whether an App-dir-relative path is one of the *Option* Modules this writer owns.

    The inverse of `module_relpath`, and its neighbour so that moving `options/` is one
    edit. Entity Modules (`binds.lua`, `monitors.lua`, ...) live beside them and answer to
    `is_entity_module` instead.
    """
    return relpath.startswith(f"{OPTIONS_DIR}/") and relpath.endswith(".lua")


ENTITY_MODULES: frozenset[str] = frozenset(
    {
        BINDS_MODULE,
        WINDOW_RULES_MODULE,
        LAYER_RULES_MODULE,
        MONITORS_MODULE,
        WORKSPACE_RULES_MODULE,
        ANIMATIONS_MODULE,
        GESTURES_MODULE,
        DEVICES_MODULE,
        ENV_MODULE,
        PERMISSIONS_MODULE,
        AUTOSTART_MODULE,
        PLUGINS_MODULE,
    }
)
"""The App-dir-relative names of the Entity Modules the app generates.

`binds.lua` (#64), the window and layer rule Modules (#67), the monitor and workspace rule
Modules (#68), the six declarative Modules of #70, and the plugin load list (#174). Named
as a set rather than inferred from "a `.lua` at the App dir root", because `legacy.lua` and
`user.lua` live there too and the app must never touch those.
"""


def is_entity_module(relpath: str) -> bool:
    """Whether an App-dir-relative path is an Entity Module the app generates.

    Needed alongside `is_option_module` because pruning asks "may the app delete this?",
    and an entity Module whose last Entity was deleted has to go the same way a Section's
    Module does -- a `binds.lua` left behind would keep binding keys the user removed.
    """
    return relpath in ENTITY_MODULES


def is_generated_module(relpath: str) -> bool:
    """Whether the app generates this Module at all -- Option or Entity."""
    return is_option_module(relpath) or is_entity_module(relpath)


def render_module(
    items: Sequence[tuple[ResolvedOption, Any]],
    *,
    app_version: str,
) -> str:
    """One Section's `hl.config` Module.

    `items` are `(option, value)` pairs from `ConfigModel`, already in Hyprland's
    declaration order and already all from the same Section.
    """
    if not items:
        raise ValueError("a Module needs at least one set Option")

    sections = {option.section for option, _ in items}
    if len(sections) != 1:
        raise ValueError(f"a Module holds exactly one Section, got {sorted(sections)}")

    header = (
        f"{GENERATED_BANNER.format(version=app_version)}\n-- Section: {items[0][0].section}\n"
    )
    if is_plugin_option(items[0][0].name):
        return (
            header + _PLUGIN_NOTE + "".join(_guarded(option, value) for option, value in items)
        )

    tree: LuaTree = {}
    for option, value in items:
        insert(tree, option.path, lua_literal_for(option, value))

    body = render_table(tree)
    return f"{header}\nhl.config({body})\n"


_PLUGIN_NOTE = "-- Each setting applies only while the plugin that adds it is loaded.\n"


def _guarded(option: ResolvedOption, value: Any) -> str:
    """One plugin setting, set only while Hyprland knows it (ADR-0018 §Plugins, #175).

    Hyprland reports a config error for a key of a plugin that is not loaded, `pcall`
    included; `hl.get_config` answers `nil` for it instead, and a loaded plugin's key is
    known even on the first pass of a fresh start (nested 0.56.2 probe, #175). One guard
    per setting, so a plugin that drops one setting costs only that one.
    """
    tree: LuaTree = {}
    insert(tree, option.path, lua_literal_for(option, value))
    return (
        f"\nif hl.get_config({lua_string(option.name)}) ~= nil then\n"
        f"{INDENT}hl.config({render_table(tree, 1)})\n"
        f"end\n"
    )


@dataclass(frozen=True, slots=True)
class BridgeRequire:
    """One Bridge module's line in the Entrypoint, already decided (ADR-0006 §Placement)."""

    require: str
    """The `require` name: Quarantine's key, and what an error naming the file is matched to."""

    file: str
    """Where the module lives, relative to the hypr dir."""

    text: str
    """The line as written: the require, or the require commented with its reason."""

    @property
    def loads(self) -> bool:
        return not self.text.startswith("--")


def render_entrypoint(
    *,
    modules: Sequence[str],
    legacy: str | None,
    bridges: Sequence[BridgeRequire],
    user: str | None,
    app_version: str,
    quarantined: Sequence[str] = (),
) -> str:
    """The generated `hyprland.lua`: a header and a require list, in the one right order.

    The order is the app's whole override story (ADR-0005, ADR-0006):

    1. **generated Modules** -- what the GUI owns;
    2. **`legacy`** -- imported constructs the GUI cannot represent, so they sit above the
       generated values they may need to correct;
    3. **Bridges** -- external tools (matugen, wallust, ...), which must beat the GUI or
       live theming silently stops working. Each states its own line: a loading require, or
       the require commented with why it does not load (`engine/bridge/entries.py`);
    4. **`user`** -- last, so the escape hatch actually escapes. The app never fights it; it
       badges Options `user.lua` overrides instead.

    Only files that exist are required: Hyprland's `require` is protected, and asking for a
    `user.lua` the user never created would add an error to every reload.

    `quarantined` holds the lines the caller has already left out of the lists above
    (ADR-0016 §Quarantine). They are re-stated here commented out, which is the whole reason
    this takes them at all: the file is the user's to read, and a `user.lua` that has
    silently stopped loading is indistinguishable from one the app never noticed. The
    comment says what happened and that it is reversible.
    """
    lines = [
        GENERATED_BANNER.format(version=app_version),
        "-- Regenerated whenever the module set changes; directories are not watched.",
        "",
    ]

    def block(comment: str, requires: Sequence[str]) -> None:
        if not requires:
            return
        lines.append(f"-- {comment}")
        lines.extend(requires)
        lines.append("")

    def required(paths: Sequence[str]) -> list[str]:
        return [f'require("{path}")' for path in paths]

    block("Settings written by hyprtweaker.", required(modules))
    block(
        "Imported constructs the GUI cannot represent. Never rewritten.",
        required([legacy] if legacy else []),
    )
    block(
        "External tools. Owned by the tool, not by hyprtweaker.",
        [bridge.text for bridge in bridges],
    )
    block(
        "Your own Lua. Required last, so it wins. Never rewritten.",
        required([user] if user else []),
    )

    if quarantined:
        lines.append("-- Disabled by hyprtweaker because it stopped the config from loading.")
        lines.append("-- Nothing in these files was changed. Re-enable them in Settings.")
        lines.extend(f"-- {line}" for line in sorted(quarantined))
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
