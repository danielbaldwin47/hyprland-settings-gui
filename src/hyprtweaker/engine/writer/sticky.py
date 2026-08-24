"""The canonical `env.lua`, `permissions.lua` and `autostart.lua` Modules (#70).

Three kinds, one file, because they share the property that makes them the odd ones out:
**omission is not deletion here.** Every other Entity kind is wiped and replayed on each
reload, so deleting a line deletes the thing. Not these three:

* `hl.env` calls `setenv` and there is no unset path (`lua-api-surface.md` §13). Removing
  a variable from the model removes it from the file and from the *next* session; the
  running one keeps it until Hyprland restarts.
* `hl.permission` is applied on first launch only (§15), so both adding and removing one
  needs a restart to take effect.
* An autostart command that already ran cannot be un-run.

None of that changes what gets written -- the model is still rendered whole -- but it is
the reason the Pages for these three say "after a restart" where the others say nothing,
and the reason they are grouped rather than scattered.
"""

from __future__ import annotations

from ..entities_catalog import EVERY_RELOAD, SHUTDOWN_EVENT, STARTUP_EVENT
from ..model.entities import EnvVar, Permission, StartupCommand
from ..model.values import lua_string
from .lua import render_entity_module

_EVENT_COMMENTS: dict[str, str] = {
    STARTUP_EVENT: "Once, when Hyprland starts.",
    SHUTDOWN_EVENT: "When Hyprland shuts down.",
}


def render_env(variable: EnvVar) -> str:
    """One `hl.env(...)` call: two positional strings, or three when `envd` was meant.

    The third argument is the `dbus` export -- `systemctl --user import-environment` plus
    `dbus-update-activation-environment`, what the legacy `envd` keyword did. It is
    source-only in Hyprland (undocumented in the wiki, `lua-api-surface.md` §13) and
    emitted only when true, so a plain variable renders as the two-argument form the wiki
    does document.
    """
    args = [lua_string(variable.name), lua_string(variable.value)]
    if variable.dbus:
        args.append("true")
    return f"hl.env({', '.join(args)})"


def render_permission(permission: Permission) -> str:
    """One `hl.permission({...})` call.

    The table form rather than the positional one the upstream example uses: a binary is a
    *regex*, regexes are the values most likely to want editing later, and
    `{ binary = ..., type = ..., mode = ... }` says which of the three strings is which
    without counting commas.
    """
    parts = [
        f"binary = {lua_string(permission.binary)}",
        f"type = {lua_string(permission.kind)}",
        f"mode = {lua_string(permission.mode)}",
    ]
    return f"hl.permission({{ {', '.join(parts)} }})"


def render_startup_command(command: StartupCommand) -> str:
    """The single call one autostart command makes, without its `hl.on` wrapper.

    `raw` is the legacy `execr` family: `hl.dsp.exec_raw` skips the `[rules] cmd` prefix
    parsing that `hl.exec_cmd` still performs, which matters for any command whose first
    character is a `[` (`hyprlang-to-lua.md` §2.10.7).
    """
    if command.raw:
        return f"hl.dispatch(hl.dsp.exec_raw({lua_string(command.command)}))"
    return f"hl.exec_cmd({lua_string(command.command)})"


def render_env_module(variables: list[EnvVar], *, app_version: str) -> str | None:
    """The whole `env.lua`, or `None` when there is nothing to write."""
    return render_entity_module(
        [render_env(variable) for variable in variables],
        comment="Environment variables. Removing one needs a Hyprland restart to undo.",
        app_version=app_version,
    )


def render_permissions_module(permissions: list[Permission], *, app_version: str) -> str | None:
    """The whole `permissions.lua`, or `None` when there is nothing to write."""
    return render_entity_module(
        [render_permission(permission) for permission in permissions],
        comment="Permissions. Applied on first launch, so changes need a restart.",
        app_version=app_version,
    )


def render_autostart_module(commands: list[StartupCommand], *, app_version: str) -> str | None:
    """The whole `autostart.lua`, or `None` when there is nothing to write.

    Three shapes for three timings (`lua-api-surface.md` §14). A command that runs once at
    startup goes inside a single `hl.on("hyprland.start", ...)` block -- one block rather
    than one per command, because the handler list is cleared and re-registered on every
    reload and a file full of one-line handlers says nothing a grouped block does not. A
    command with no event is the old `exec`: top level, re-run by the very act of
    re-executing the file. Shutdown commands get their own block.

    Top-level commands are emitted *first*. They are the ones that run on every reload, so
    a user scanning the file sees the surprising timing at the top rather than buried under
    a startup block they would have to read past.
    """
    by_event: dict[str, list[StartupCommand]] = {}
    for command in commands:
        by_event.setdefault(command.event, []).append(command)

    blocks: list[str] = []
    for command in by_event.get(EVERY_RELOAD, ()):
        blocks.append(render_startup_command(command))

    for event in (STARTUP_EVENT, SHUTDOWN_EVENT):
        group = by_event.get(event)
        if not group:
            continue
        body = "\n".join(f"  {render_startup_command(item)}" for item in group)
        blocks.append(
            f"-- {_EVENT_COMMENTS[event]}\nhl.on({lua_string(event)}, function()\n{body}\nend)"
        )

    # Any other event is something the model was handed by an importer that knew more than
    # this renderer does; wrapping it the same way keeps it working rather than dropping it.
    for event, group in by_event.items():
        if event in (EVERY_RELOAD, STARTUP_EVENT, SHUTDOWN_EVENT):
            continue
        body = "\n".join(f"  {render_startup_command(item)}" for item in group)
        blocks.append(f"hl.on({lua_string(event)}, function()\n{body}\nend)")

    return render_entity_module(
        blocks,
        comment="Autostart. Top-level commands re-run on every config reload.",
        app_version=app_version,
    )


__all__ = [
    "render_autostart_module",
    "render_env",
    "render_env_module",
    "render_permission",
    "render_permissions_module",
    "render_startup_command",
]
