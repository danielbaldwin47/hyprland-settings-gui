"""The canonical `monitors.lua` and `workspace_rules.lua` Modules (ADR-0008).

Like the rule Modules, both are files the app writes *and* reads: `hyprctl -j monitors`
reflects display *state*, never rules -- `desc:` identities, the catch-all, and rules for
disconnected outputs are unrecoverable from it -- so the file is the interface and a hand
edit is adopted by re-reading it under the importer's recording stub.

**Identity is a key, not a position.** Monitor rules merge per `output` string and
workspace rules per selector (`lua-api-surface.md` §3, §9): Hyprland itself keeps one rule
per key, so these renderers emit what `EntitySet`'s merging adders hold -- one call per
identity, in the model's insertion order. Emitting a duplicate would be legal Lua that
means something other than what the list shows.

**The catch-all is a value.** `output = ""` is the "Any other display" rule (ADR-0008),
so the empty string is emitted like any other identity, never skipped.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..model.entities import EntitySet, MonitorRule, WorkspaceRule
from ..model.values import lua_string
from ..paths import MONITORS_MODULE
from .binds import lua_value
from .lua import render_entity_module, table_key


@dataclass(frozen=True, slots=True)
class ParsedMonitors:
    """What one read of a monitors or workspace-rules Module found."""

    monitors: tuple[MonitorRule, ...] = ()
    workspace_rules: tuple[WorkspaceRule, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# --- rendering -------------------------------------------------------------------------


def render_monitor_rule(rule: MonitorRule) -> str:
    """One `hl.monitor({...})` line: the identity first, then the fields as held."""
    parts = [f"output = {lua_string(rule.output)}"]
    parts.extend(
        f"{table_key(str(key))}{lua_value(value)}" for key, value in rule.fields.items()
    )
    return f"hl.monitor({{ {', '.join(parts)} }})"


def render_workspace_rule(rule: WorkspaceRule) -> str:
    """One `hl.workspace_rule({...})` line: the selector first, then the fields."""
    parts = [f"workspace = {lua_string(rule.workspace)}"]
    parts.extend(
        f"{table_key(str(key))}{lua_value(value)}" for key, value in rule.fields.items()
    )
    return f"hl.workspace_rule({{ {', '.join(parts)} }})"


def render_monitors_module(rules: list[MonitorRule], *, app_version: str) -> str | None:
    """The whole `monitors.lua`, or `None` when there is nothing to write.

    `None` rather than an empty file so the writer prunes the Module -- absence is how
    this config model spells "no monitor rules", the same as for binds and rules.
    """
    return render_entity_module(
        [render_monitor_rule(rule) for rule in rules],
        comment='Monitor rules. One rule per output; "" is any other display.',
        app_version=app_version,
    )


def render_workspace_rules_module(
    rules: list[WorkspaceRule], *, app_version: str
) -> str | None:
    """The whole `workspace_rules.lua`, or `None` when there is nothing to write."""
    return render_entity_module(
        [render_workspace_rule(rule) for rule in rules],
        comment="Workspace rules. One rule per selector; Hyprland merges duplicates.",
        app_version=app_version,
    )


# --- reading back ----------------------------------------------------------------------


def parse_monitors_module(
    source: str | Path, *, module: str = MONITORS_MODULE, timeout: float = 5.0
) -> ParsedMonitors:
    """Read a monitors or workspace-rules Module back into Entities.

    Takes text or a path; evaluation happens in the importer's sandbox with consent
    granted, defensible exactly as for `binds.lua` and the rule Modules: this is a Module
    the app wrote into its own App dir, already required by the Entrypoint on every reload.

    Both entity kinds are collected regardless of `module`, so a hand edit that put a
    workspace rule in `monitors.lua` still comes back as what it is instead of vanishing.
    """
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source

    # Evaluated from a scratch copy keeping the module's basename, so origins read as
    # `monitors.lua:N` with `N` the line in the real file.
    with tempfile.TemporaryDirectory(prefix="hyprtweaker-monitors-") as scratch:
        path = Path(scratch) / module
        path.write_text(text, encoding="utf-8")
        return _parse_path(path, timeout=timeout)


def _parse_path(path: Path, *, timeout: float) -> ParsedMonitors:
    from ..importer.lua.mapping import table_fields
    from ..importer.lua.sandbox import Consent, LuaUnavailable, evaluate

    try:
        recording = evaluate(path, consent=Consent(evaluate=True), timeout=timeout)
    except LuaUnavailable as error:
        # No interpreter is an installation problem, not a config one; this must not look
        # like "the file has no monitor rules".
        return ParsedMonitors(errors=(str(error),))

    # Collected through EntitySet's adders because Hyprland merges: a hand edit that
    # declares an output or a selector twice must come back as the one rule the
    # compositor sees, or the next write would emit both. `hl.monitor` always merges
    # (`lua-api-surface.md` §3), which is what `merge=True` spells.
    entities = EntitySet()

    for call in recording.calls:
        if call.name not in ("monitor", "workspace_rule"):
            continue
        fields = table_fields(call.args)
        if call.name == "monitor":
            output = fields.pop("output", "")
            entities.add_monitor_rule(
                MonitorRule(output=str(output), fields=fields, origin=call.origin),
                merge=True,
            )
        else:
            workspace = fields.pop("workspace", None)
            if workspace is None:
                # A selector-less call registers nothing in Hyprland either; dropping it
                # mirrors the compositor rather than inventing a rule for "".
                continue
            entities.add_workspace_rule(
                WorkspaceRule(workspace=str(workspace), fields=fields, origin=call.origin)
            )

    return ParsedMonitors(
        monitors=tuple(entities.monitors),
        workspace_rules=tuple(entities.workspace_rules),
        errors=tuple(recording.errors),
    )


__all__ = [
    "ParsedMonitors",
    "parse_monitors_module",
    "render_monitor_rule",
    "render_monitors_module",
    "render_workspace_rule",
    "render_workspace_rules_module",
]
