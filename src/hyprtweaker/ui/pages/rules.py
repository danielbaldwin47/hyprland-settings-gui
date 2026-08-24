"""The Rules Pages: every window or layer rule, flat, in evaluation order (ADR-0008).

One class, two kinds. ADR-0008 gives layer rules "the same list model and editor shell"
as window rules, so the Page is parameterised by kind rather than written twice.

**The list is the order.** Display order = file order = evaluation order, later rules win
per Effect, and the footer says so. Reordering is a drag: each row carries a handle, and
dropping a row on another moves it there -- a move, not a swap, because everything between
the two shifts the way the user watched it shift.

**Disabling is not deleting.** The inline switch flips `enabled`; the rule keeps its file
position and its row, dimmed, so re-enabling restores the world as it was.

**The filter narrows, never edits.** It hides rows; indexes stay model indexes, so every
action on a visible row lands on the right rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import LayerRule, WindowRule  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session

Rule = WindowRule | LayerRule

NEGATIVE_PREFIX = "negative:"


def is_negated(value: object) -> bool:
    """Whether a match value carries the `negative:` prefix (string kinds only)."""
    return isinstance(value, str) and value.startswith(NEGATIVE_PREFIX)


def strip_negation(value: str) -> str:
    return value[len(NEGATIVE_PREFIX) :] if value.startswith(NEGATIVE_PREFIX) else value


def prop_title(name: str) -> str:
    """A match prop or effect name as a row title: `initial_class` -> `Initial class`."""
    return name.replace("_", " ").capitalize()


def _value_text(value: object) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    if isinstance(value, dict):
        return " ".join(f"{key}={item}" for key, item in value.items())
    return str(value)


def match_text(rule: Rule) -> str:
    """The Match half of a row's auto-summary: `class kitty · not title ^(x)$`."""
    parts = []
    for name, value in rule.match.items():
        negated = is_negated(value)
        shown = strip_negation(value) if isinstance(value, str) else _value_text(value)
        prefix = "not " if negated else ""
        parts.append(f"{prefix}{name} {shown}".strip())
    return " · ".join(parts)


def effects_text(rule: Rule) -> str:
    """The Effects half: bools by bare name, everything else `name value`."""
    parts = []
    for name, value in rule.effects.items():
        if value is True:
            parts.append(name)
        elif value is False:
            parts.append(f"{name} off")
        else:
            parts.append(f"{name} {_value_text(value)}")
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


def filter_haystack(rule: Rule) -> str:
    """Everything the filter bar matches against: label, match, effects (ADR-0008)."""
    words = [rule.name]
    for name, value in rule.match.items():
        words.append(name)
        words.append(_value_text(value))
    for name, value in rule.effects.items():
        words.append(name)
        words.append(_value_text(value))
    return " ".join(word for word in words if word).lower()


@dataclass(frozen=True, slots=True)
class RuleActions:
    """The verbs the window wires into the Page, bundled once.

    Every index is into the model's flat list for this kind -- position is identity
    (ADR-0008), so the index is the only address an edit can safely use.
    """

    add: Callable[[], None]
    edit: Callable[[int], None]
    remove: Callable[[int], None]
    enable: Callable[[int, bool], None]
    move: Callable[[int, int], None]
    """Move the rule at the first index to the second -- the drag reorder."""


class RuleRow:
    """One `Adw.ActionRow` for one Rule: handle, summary, switch, edit, remove.

    The drag handle is the drag *source* -- dragging anywhere else on the row would fight
    scrolling and button presses -- while the whole row is the drop target, because a drop
    needs the row's full height to aim at.
    """

    def __init__(
        self,
        rule: Rule,
        index: int,
        *,
        actions: RuleActions,
        editable: bool,
    ) -> None:
        self.rule = rule
        self.index = index
        self.enabled_switch: Gtk.Switch | None = None

        subtitle = rule_subtitle(rule)
        self.widget = Adw.ActionRow(title=rule_title(rule), subtitle=subtitle)

        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        self.widget.add_prefix(handle)

        if not rule.enabled:
            badge = Gtk.Label(label="Disabled", css_classes=["dim-label", "caption"])
            badge.set_tooltip_text("Kept in the file with enabled = false; it does not apply.")
            self.widget.add_suffix(badge)
            self.widget.add_css_class("dim-label")

        switch = Gtk.Switch(active=rule.enabled, valign=Gtk.Align.CENTER)
        switch.set_tooltip_text("Apply this rule")
        switch.set_sensitive(editable)
        switch.connect("state-set", self._on_switch, actions, index)
        self.enabled_switch = switch
        self.widget.add_suffix(switch)

        if editable:
            edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
            edit.add_css_class("flat")
            edit.set_tooltip_text("Edit this rule")
            edit.connect("clicked", lambda _button: actions.edit(index))
            self.widget.add_suffix(edit)

            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.set_tooltip_text("Remove this rule")
            remove.connect("clicked", lambda _button: actions.remove(index))
            self.widget.add_suffix(remove)

            self._wire_drag(handle, actions)

    @staticmethod
    def _on_switch(_switch: Gtk.Switch, state: bool, actions: RuleActions, index: int) -> bool:
        actions.enable(index, state)
        # Handled: the refresh rebuilds the row from the model, which is the truth.
        return True

    def _wire_drag(self, handle: Gtk.Image, actions: RuleActions) -> None:
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare)
        handle.add_controller(source)

        target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop, actions)
        self.widget.add_controller(target)

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, _x: float, _y: float
    ) -> Gdk.ContentProvider:
        value = GObject.Value(GObject.TYPE_INT, self.index)
        return Gdk.ContentProvider.new_for_value(value)

    def _on_drop(
        self,
        _target: Gtk.DropTarget,
        value: int,
        _x: float,
        _y: float,
        actions: RuleActions,
    ) -> bool:
        origin = int(value)
        if origin == self.index:
            return False
        actions.move(origin, self.index)
        return True


class RulesPage:
    """A Page listing one rule kind, rebuilt whenever the model's list moves.

    Rebuilt wholesale rather than patched per row, exactly like Binds and for the same
    reason: identity is position, so any insert or move shifts every index after it.
    """

    kind = "window"
    section = "window_rules"
    title = "Window rules"
    empty_hint = "Add one with the button above, or import an existing config."

    def __init__(self, session: Session, *, actions: RuleActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[RuleRow] = []
        self._filter_text = ""

        self._page = Adw.PreferencesPage(title=self.title)

        self._filter = Gtk.SearchEntry(placeholder_text="Filter by label, match or effect")
        self._filter.connect("search-changed", self._on_filter_changed)

        filter_group = Adw.PreferencesGroup()
        filter_group.add(self._filter)
        self._page.add(filter_group)

        self._group = Adw.PreferencesGroup(
            title=self.title,
            description=(
                "Rules apply top to bottom; later rules win when they set the same "
                "effect. Drag the handle to reorder."
            ),
        )
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("Add a rule")
        add.connect("clicked", lambda _button: self._actions.add())
        self._add_button = add
        header.append(add)
        self._group.set_header_suffix(header)
        self._page.add(self._group)

        self._listed: list[Gtk.Widget] = []
        self.refresh()

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def rows(self) -> tuple[RuleRow, ...]:
        """Every built Row, in list order. What the UI smoke tier asserts against."""
        return tuple(self._rows)

    @property
    def rules(self) -> list[Rule]:
        return list(self._session.rules(self.kind))

    @property
    def filter_entry(self) -> Gtk.SearchEntry:
        return self._filter

    def set_filter(self, text: str) -> None:
        """Programmatic filter -- applied now, not on the entry's debounced signal.

        `search-changed` fires through the main loop after a delay; the smoke tier runs
        no loop, and a caller setting a filter wants the narrowed list, not a promise.
        The entry text is kept in step so the two paths cannot disagree.
        """
        self._filter.set_text(text)
        self._apply_filter(text)

    def _on_filter_changed(self, entry: Gtk.SearchEntry) -> None:
        self._apply_filter(entry.get_text())

    def _apply_filter(self, text: str) -> None:
        self._filter_text = text.strip().lower()
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the list from the model, applying the filter.

        Rows keep their index into the model's flat list, never into the filtered view,
        because that index is what an edit, a delete or a drop addresses.
        """
        for widget in self._listed:
            self._group.remove(widget)
        self._listed = []
        self._rows = []

        editable = bool(self._session.live)
        self._add_button.set_sensitive(editable)

        rules = self.rules
        shown = 0
        for index, rule in enumerate(rules):
            if self._filter_text and self._filter_text not in filter_haystack(rule):
                continue
            row = RuleRow(rule, index, actions=self._actions, editable=editable)
            self._rows.append(row)
            self._group.add(row.widget)
            self._listed.append(row.widget)
            shown += 1

        if not shown:
            if rules:
                empty = Adw.ActionRow(
                    title="No rules match this filter",
                    subtitle="Clear the filter to see all rules.",
                )
            else:
                empty = Adw.ActionRow(
                    title=f"No {self.title.lower()} yet", subtitle=self.empty_hint
                )
            self._group.add(empty)
            self._listed.append(empty)


class WindowRulesPage(RulesPage):
    kind = "window"
    section = "window_rules"
    title = "Window rules"


class LayerRulesPage(RulesPage):
    kind = "layer"
    section = "layer_rules"
    title = "Layer rules"
