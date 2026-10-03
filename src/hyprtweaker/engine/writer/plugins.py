"""The canonical `plugins.lua` Module: the ordered plugin load list (ADR-0018, #174).

One `hl.plugin.load(path)` per entry, in list order. A disabled entry is the same line
behind `DISABLED_PREFIX`, the spelling `binds.lua` uses: the file stays the interface (the
line is readable, and deleting the prefix by hand switches the plugin back on) and the
entry keeps its place in the order. The read-back is `parse_declarations_module`, which
revives those lines.

hyprpm is out of scope (ADR-0018): an entry points at a `.so` that already exists. A path
that does not exist is still written -- Hyprland 0.56.2 skips it without a config error,
and the next line applies (probed on a nested instance, #174) -- and the Scripting Page
marks the row "File not found".
"""

from __future__ import annotations

from ..model.entities import PluginLoad
from ..model.values import lua_string
from .binds import DISABLED_PREFIX
from .lua import render_entity_module


def render_plugin_load(plugin: PluginLoad) -> str:
    """One entry: the load call, or its disabled comment."""
    line = f"hl.plugin.load({lua_string(plugin.path)})"
    return line if plugin.enabled else f"{DISABLED_PREFIX}{line}"


def render_plugins_module(plugins: list[PluginLoad], *, app_version: str) -> str | None:
    """The whole `plugins.lua`, or `None` when the list is empty.

    A list holding only disabled entries still renders: its lines are comments, but pruning
    the file would lose entries the user switched off rather than removed.
    """
    return render_entity_module(
        [render_plugin_load(plugin) for plugin in plugins],
        comment="Plugins, loaded in this order. A disabled one is kept as a comment.",
        app_version=app_version,
    )


__all__ = ["render_plugin_load", "render_plugins_module"]
