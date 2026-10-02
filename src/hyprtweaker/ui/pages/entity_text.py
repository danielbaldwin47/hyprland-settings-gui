"""What an Entity row says, in words, with no display (ADR-0011, #220).

The Binds, Rules, Workspaces and Monitors Pages and the Presets group each describe their
Entity in one place; the search entries (#75, #172) describe the same Entity in the same
words, so those functions live here, in a module that imports no `gi`, and the Pages and
the finder both read them. An Entity is therefore worded alike wherever it turns up, and
strict mypy covers the words.

Only text and the vocabulary of a badge live here. What a row does about a badge -- the
button, its callable -- stays in the Page, which holds the widgets those callables reach.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hyprtweaker.engine.binds_analysis import submap_target
from hyprtweaker.engine.dispatchers import EXEC_PATH, lookup
from hyprtweaker.engine.model.entities import Bind, LayerRule, MonitorRule, WindowRule
from hyprtweaker.engine.presets import CaptureScope, Preset
from hyprtweaker.engine.profiles import MonitorProfile
from hyprtweaker.engine.rule_filter import value_text
from hyprtweaker.engine.rules_catalog import is_negated, prop_title, strip_negation
from hyprtweaker.engine.triggers import (
    AmpMultiKey,
    Blocked,
    DeadKeys,
    trigger_load_problem,
)

Rule = WindowRule | LayerRule


def trigger_text(bind: Bind) -> str:
    """The Trigger as the list shows it.

    `code:N` gets spelled out rather than left as jargon: it is the one Trigger a user
    cannot recognise from its own text, and the one the compositor will not help identify.
    """
    parts = []
    for token in bind.keys.split("+"):
        token = token.strip()
        if token.startswith("code:"):
            parts.append(f"key code {token[5:]}")
        else:
            parts.append(token)
    return " + ".join(part for part in parts if part)


def action_text(bind: Bind) -> str:
    """The Action as one line of prose, falling back to the raw call.

    An unknown path is rendered, not hidden: a config written for a newer Hyprland or a
    plugin dispatcher is something this build cannot know about, and showing the call is
    more use than showing nothing (ADR-0012's contract for unknown keys).
    """
    if bind.dispatcher is None:
        return "Runs a Lua function"
    call = bind.dispatcher
    if call.path == EXEC_PATH:
        command = call.args.get("command") or (call.positional[0] if call.positional else "")
        return str(command) or "Run a command"
    entry = lookup(call.path)
    label = entry.label if entry else f"hl.dsp.{call.path}"
    detail = ", ".join(f"{key}: {value}" for key, value in call.args.items())
    if not detail and call.positional:
        detail = ", ".join(str(arg) for arg in call.positional)
    return f"{label} ({detail})" if detail else label


EMPTY_SUBMAP = (
    "Hyprland cannot enter a submap with no enabled keybinds. Add or enable a keybind in it."
)
"""The empty-submap flag's sentence (#208). A group description and the tooltip of the badge
on each bind that enters the submap carry it alike. It leads the unreachable sentence: a
submap with neither gets the bind first, then the way in."""


class BadgeKind(Enum):
    """Why a Bind row carries a badge: the one vocabulary for disabled and read-only binds.

    The Binds Page row reads this through `bind_badge`; the search entries #75 adds should
    read it the same way, so a bind is described alike wherever it turns up. Each kind fixes
    what the row offers and how it looks, not only what it says:

    - `ERROR`: commented out with a Trigger Hyprland cannot load, most often a key xkb
      does not know, which the Importer disables (ADR-0007). Enabled as it stands, Hyprland
      would refuse the *whole* config and the Session refuses the enable (#199), so the row
      offers re-capture in place of Enable. Edit and Remove stay.
    - `MULTI_KEY`: an `A&B` Trigger, which Hyprland (0.56.2, ADR-0007) cannot load.
      Nothing in the app can make it valid, so no edit and no Enable; Remove is offered.
    - `LUA_FUNCTION`: the action is a Lua function in `user.lua`, which the app does not
      write. Badge only: no edit, no Enable, and no Remove of a line it cannot see.
    - `DISABLED`: commented out by the user. One-click Enable, edit and Remove.
    - `EMPTY_SUBMAP`: an enabled bind that enters a submap with no live bind (#208), which
      Hyprland never registers, so the bind fires and then errors. The bind itself is fine,
      so nothing is taken away: Edit and Remove stay, and the fix is a bind in the submap.

    The button a row offers to turn the bind on is the Page's: `binds.row_verb`.
    """

    ERROR = "error"
    MULTI_KEY = "multi-key"
    LUA_FUNCTION = "lua-function"
    DISABLED = "disabled"
    EMPTY_SUBMAP = "empty-submap"

    @property
    def editable(self) -> bool:
        """Whether the row offers the bind editor."""
        return self in (BadgeKind.ERROR, BadgeKind.DISABLED, BadgeKind.EMPTY_SUBMAP)

    @property
    def removable(self) -> bool:
        """Whether the row offers Remove."""
        return self is not BadgeKind.LUA_FUNCTION

    @property
    def style(self) -> str:
        """The badge label's style class: loud where the badge asks the user to act."""
        return {
            BadgeKind.ERROR: "error",
            BadgeKind.MULTI_KEY: "warning",
            BadgeKind.EMPTY_SUBMAP: "warning",
        }.get(self, "dim-label")

    @property
    def dims_row(self) -> bool:
        """Whether a disabled bind's row is dimmed.

        Not when the badge asks the user to act: row opacity reaches the badge too, and that
        badge is the one line saying this bind needs them.
        """
        return self not in (BadgeKind.ERROR, BadgeKind.MULTI_KEY)


@dataclass(frozen=True, slots=True)
class BindBadge:
    """What one badged row shows: its kind, the badge's words, and the tooltip's reason."""

    kind: BadgeKind
    text: str
    tooltip: str


def bind_badge(bind: Bind, *, empty_submaps: frozenset[str] = frozenset()) -> BindBadge | None:
    """The badge this Bind's row carries, or `None` for an enabled, editable bind.

    Recomputed from the Bind rather than carried on the model: the Trigger already says
    everything, and a stored flag could disagree with it after an edit.

    A function action wins first, since nothing on the row is the app's to change. The
    rest reads `trigger_load_problem`, the one definition of a Trigger Hyprland can load,
    which the Session enforces: a disabled bind it would refuse to enable offers re-capture.

    The dead-keysym answer is the same oracle the Importer used to disable the bind. Where
    libxkbcommon will not load it answers nothing, so a bind reads as plain disabled -- but
    on such a machine the Importer could not have found the dead key either, so the row
    never claims more than the import knew.

    `empty_submaps` is `binds_analysis.empty_submaps` of the whole model, which one Bind
    cannot know. It is read last: a bind that cannot load, or is off, has a reason that
    comes before it, and only an enabled bind that would fire can fail on entering one.
    """
    if bind.dispatcher is None:
        return BindBadge(
            BadgeKind.LUA_FUNCTION,
            "Defined by a Lua function",
            "This keybind's action is a Lua function, so this app shows it but cannot edit "
            "it. Change it in the file that defines it.",
        )
    problem = trigger_load_problem(bind.keys)
    if isinstance(problem, AmpMultiKey):
        return BindBadge(
            BadgeKind.MULTI_KEY,
            "Multi-key: Hyprland can't load it",
            f"Hyprland rejects multi-key triggers like {trigger_text(bind)}: enabled, "
            "this keybind would stop your whole config from loading. It stays commented "
            "out; remove it, or add a keybind with a single key instead.",
        )
    if bind.enabled:
        if (target := submap_target(bind)) is not None and target in empty_submaps:
            return BindBadge(
                BadgeKind.EMPTY_SUBMAP,
                "Submap has no enabled keybinds",
                f"This keybind enters the submap {target}. {EMPTY_SUBMAP} "
                "Or remove this keybind.",
            )
        return None
    match problem:
        case DeadKeys(names=dead):
            names = ", ".join(f'"{name}"' for name in dead)
            noun = "key" if len(dead) == 1 else "keys"
            return BindBadge(
                BadgeKind.ERROR,
                f"Unknown {noun} {names}",
                f"Hyprland has no {noun} named {names}, so this keybind was imported "
                "commented out: enabled, it would stop your whole config from loading. "
                "Record a new trigger to use it.",
            )
        case Blocked():
            return BindBadge(
                BadgeKind.ERROR,
                "Trigger can't load",
                f"{problem.message} Hyprland can't load this trigger, so this keybind stays "
                "commented out. Record a new trigger to use it.",
            )
        case None:
            return BindBadge(
                BadgeKind.DISABLED,
                "Disabled",
                "Kept in place but commented out in binds.lua; it does not fire.",
            )


def match_text(rule: Rule) -> str:
    """The Match half of a row's auto-summary: `class kitty · not title ^(x)$`."""
    parts = []
    for name, value in rule.match.items():
        negated = is_negated(value)
        shown = strip_negation(value) if isinstance(value, str) else value_text(value)
        prefix = "not " if negated else ""
        parts.append(f"{prefix}{_words(name)} {shown}".strip())
    return " · ".join(parts)


def _words(name: str) -> str:
    """A match prop or effect as the editor titles it, in a sentence's case: `no_blur` reads
    "no blur", as the filter chips and the editor say it (#148 hand-test 16)."""
    return prop_title(name).lower()


def effects_text(rule: Rule) -> str:
    """The Effects half: bools by bare name, everything else `name value`."""
    parts = []
    for name, value in rule.effects.items():
        words = _words(name)
        if value is True:
            parts.append(words)
        elif value is False:
            parts.append(f"{words} off")
        else:
            parts.append(f"{words} {value_text(value)}")
    return ", ".join(parts)


def rule_title(rule: Rule) -> str:
    """The row title: the Label when there is one, else the auto-summary (ADR-0008)."""
    if rule.name:
        return rule.name
    match = match_text(rule) or "any"
    effects = effects_text(rule)
    return f"{match} → {effects}" if effects else match


def rule_subtitle(rule: Rule) -> str:
    """Under a Label, the summary the Label replaced; under a summary, nothing."""
    if not rule.name:
        return ""
    match = match_text(rule) or "any"
    effects = effects_text(rule)
    return f"{match} → {effects}" if effects else match


def rule_summary(rule: MonitorRule) -> str:
    """A rule's fields as one dim line: `mode 1920x1080@60 · position 0x0`."""
    parts = []
    for key, value in rule.fields.items():
        if value is True:
            parts.append(key)
        elif isinstance(value, Mapping):
            inner = " ".join(f"{k}={v}" for k, v in value.items())
            parts.append(f"{key} {inner}")
        else:
            parts.append(f"{key} {value}")
    return " · ".join(parts) or "no fields yet"


def profile_summary(profile: MonitorProfile) -> str:
    """A Monitor profile's row subtitle: `2 display rules · 1 workspace pin`."""
    rules = len(profile.monitors)
    pins = sum(1 for pin in profile.pins.values() if pin is not None)
    summary = f"{rules} display {'rule' if rules == 1 else 'rules'}"
    if pins:
        summary += f" · {pins} workspace {'pin' if pins == 1 else 'pins'}"
    return summary


NO_FIELDS = "Nothing set yet"
"""A workspace rule's subtitle while it has no fields: a new rule, until its fields are set."""


def fields_summary(fields: Mapping[str, Any]) -> str:
    """A workspace rule's row subtitle: `monitor DP-1, default, decorate off`."""
    parts = []
    for name, value in fields.items():
        if value is True:
            parts.append(name)
        elif value is False:
            parts.append(f"{name} off")
        else:
            parts.append(f"{name} {value_text(value)}")
    return ", ".join(parts) or NO_FIELDS


def preset_summary(preset: Preset) -> str:
    """A Preset row's first subtitle line, what it keeps and when: `Colors · saved 14 Sep 2026`.

    The Presets group adds a line about the wallpaper under it; that line depends on the
    running session, so search leaves it out."""
    kept = [scope.label for scope in CaptureScope if scope in preset.scopes]
    made = preset.created
    return f"{', '.join(kept) or 'Settings'} · saved {made.day} {made:%b %Y}"
