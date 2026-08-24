"""The canonical `window_rules.lua` and `layer_rules.lua` Modules (ADR-0008).

Like `binds.lua`, rules are Modules the app both writes *and* reads: window state over IPC
(`hyprctl clients`) is helper data, never rule state, so the file is the interface and a
hand edit is adopted by re-reading it. Read-back evaluates under the importer's recording
stub for the same reason binds do -- a hand-written loop over a table of rules is
legitimate Lua, and a regex over the app's own output would overwrite it.

**Order is meaning.** Rules register in pure call order and later rules win per Effect
(`lua-api-surface.md` §5, ADR-0008), so identity is list position and this renderer never
sorts. The named-before-anonymous ordering that preserves *legacy* precedence (L15) is
applied once, by the hyprlang Importer at mapping time -- by the time a model reaches this
renderer, its list order is the one the GUI owns.

**Disabled is a value, not a comment.** `hl.window_rule` takes `enabled` directly, so a
disabled rule renders as the same call with `enabled = false` -- it stays in the file,
keeps its position, and re-enabling by hand is deleting two words (ADR-0008).

**Unknown effects pass through.** Any effect key outside the typed surface is emitted
exactly as held -- the dynamic/plugin effect registry accepts arbitrary keys, and dropping
one would break a plugin's config silently (ADR-0008: "never dropped").
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..model.entities import EntitySet, LayerRule, WindowRule
from ..model.values import lua_string
from ..paths import WINDOW_RULES_MODULE
from .binds import lua_value
from .lua import GENERATED_BANNER, table_key


@dataclass(frozen=True, slots=True)
class ParsedRules:
    """What one read of a rules Module found."""

    window_rules: tuple[WindowRule, ...] = ()
    layer_rules: tuple[LayerRule, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


# --- rendering -------------------------------------------------------------------------


def _spec_table(rule: WindowRule | LayerRule) -> str:
    """The one spec table of a rule call, keys in the canonical order.

    `name` first when set, `enabled` only when false (the call's default is true, and
    emitting it everywhere would bury the one rule that differs), then `match`, then every
    effect in the model's insertion order -- which round-trips, because `table_fields`
    preserves Lua's definition order for the read-back.
    """
    parts: list[str] = []
    if rule.name:
        parts.append(f"name = {lua_string(rule.name)}")
    if not rule.enabled:
        parts.append("enabled = false")
    match_inner = ", ".join(
        f"{table_key(str(key))}{lua_value(value)}" for key, value in rule.match.items()
    )
    parts.append(f"match = {{ {match_inner} }}" if match_inner else "match = {}")
    parts.extend(
        f"{table_key(str(key))}{lua_value(value)}" for key, value in rule.effects.items()
    )
    return f"{{ {', '.join(parts)} }}"


def render_window_rule(rule: WindowRule) -> str:
    """One `hl.window_rule({...})` line."""
    return f"hl.window_rule({_spec_table(rule)})"


def render_layer_rule(rule: LayerRule) -> str:
    """One `hl.layer_rule({...})` line."""
    return f"hl.layer_rule({_spec_table(rule)})"


def _render(rules: list[str], *, comment: str, app_version: str) -> str | None:
    """The shared module shell: banner, one comment line, one call per line.

    `None` for an empty list so the writer prunes the Module -- absence is how this
    config model spells "no rules", the same as for binds.
    """
    if not rules:
        return None
    header = (
        f"{GENERATED_BANNER.format(version=app_version)}\n"
        f"-- {comment}\n"
        f"-- Edits here are read back into the app, not overwritten.\n"
    )
    return header + "\n" + "\n".join(rules) + "\n"


def render_window_rules_module(rules: list[WindowRule], *, app_version: str) -> str | None:
    """The whole `window_rules.lua`, or `None` when there is nothing to write.

    `None` rather than an empty file so the writer prunes the Module -- absence is how
    this config model spells "no rules", the same as for binds.
    """
    return _render(
        [render_window_rule(rule) for rule in rules],
        comment="Window rules. Order is meaning: later rules win per effect.",
        app_version=app_version,
    )


def render_layer_rules_module(rules: list[LayerRule], *, app_version: str) -> str | None:
    """The whole `layer_rules.lua`, or `None` when there is nothing to write."""
    return _render(
        [render_layer_rule(rule) for rule in rules],
        comment="Layer rules. Order is meaning: later rules win per effect.",
        app_version=app_version,
    )


# --- reading back ----------------------------------------------------------------------


def parse_rules_module(
    source: str | Path, *, module: str = WINDOW_RULES_MODULE, timeout: float = 5.0
) -> ParsedRules:
    """Read a rules Module back into Entities.

    Takes text or a path; evaluation happens in the importer's sandbox with consent
    granted, defensible exactly as for `binds.lua`: this is a Module the app wrote into
    its own App dir, already required by the Entrypoint on every reload. A foreign config
    goes through the Migration wizard and asks.

    Both rule kinds are collected regardless of `module`, so a hand edit that put a layer
    rule in `window_rules.lua` still comes back as what it is instead of vanishing.
    """
    text = source.read_text(encoding="utf-8") if isinstance(source, Path) else source

    # Evaluated from a scratch copy keeping the module's basename, so origins read as
    # `window_rules.lua:N` with `N` the line in the real file.
    with tempfile.TemporaryDirectory(prefix="hyprtweaker-rules-") as scratch:
        path = Path(scratch) / module
        path.write_text(text, encoding="utf-8")
        return _parse_path(path, timeout=timeout)


def _parse_path(path: Path, *, timeout: float) -> ParsedRules:
    from ..importer.lua.mapping import table_fields
    from ..importer.lua.sandbox import Consent, LuaUnavailable, evaluate

    try:
        recording = evaluate(path, consent=Consent(evaluate=True), timeout=timeout)
    except LuaUnavailable as error:
        # No interpreter is an installation problem, not a config one; this must not look
        # like "the file has no rules".
        return ParsedRules(errors=(str(error),))

    # Collected through EntitySet's adders rather than appended, because Hyprland merges:
    # re-declaring a `name` updates the existing rule in place (`registerRule`). A hand
    # edit that names one rule twice must come back as the one rule the compositor sees,
    # or the adoption path would hold two rows for it and the next write would emit both.
    entities = EntitySet()

    for call in recording.calls:
        if call.name not in ("window_rule", "layer_rule"):
            continue
        fields = table_fields(call.args)
        raw_match = fields.pop("match", None)
        match: Mapping[str, Any] = table_fields(raw_match) if raw_match is not None else {}
        name = fields.pop("name", "")
        enabled = fields.pop("enabled", True)
        if call.name == "window_rule":
            entities.add_window_rule(
                WindowRule(
                    match=match,
                    effects=fields,
                    name=str(name) if name else "",
                    enabled=bool(enabled),
                    origin=call.origin,
                )
            )
        else:
            entities.add_layer_rule(
                LayerRule(
                    match=match,
                    effects=fields,
                    name=str(name) if name else "",
                    enabled=bool(enabled),
                    origin=call.origin,
                )
            )

    return ParsedRules(
        window_rules=tuple(entities.window_rules),
        layer_rules=tuple(entities.layer_rules),
        errors=tuple(recording.errors),
    )


# The module-name constants are deliberately not in `__all__`: they live in `paths`, and
# `binds.py` set the precedent of one import path per constant.
__all__ = [
    "ParsedRules",
    "parse_rules_module",
    "render_layer_rule",
    "render_layer_rules_module",
    "render_window_rule",
    "render_window_rules_module",
]
