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
action on a visible row lands on the right rule. Besides the free text there is one chip per
match prop and per effect the list uses (#113): the vocabulary is derived from the rules on
every refresh, chips and text narrow together, and a chip whose rules are gone disappears
and stops filtering rather than stranding the user on an empty list.

**Reorder has a keyboard route.** Alt+Up and Alt+Down on a row move it past the rule shown
above or below it, the same keys as the Binds page.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from hyprtweaker.engine.rule_filter import (  # noqa: E402
    Chip,
    ChipGroup,
    chips_for,
    filter_rules,
)
from hyprtweaker.ui.pages.entity_text import (  # noqa: E402
    Rule,
    rule_subtitle,
    rule_title,
)
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session

REORDER_HINT = "Drag to reorder, or press Alt+Up or Alt+Down"


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
        neighbours: tuple[int | None, int | None] = (None, None),
    ) -> None:
        """`neighbours` are the model indexes of the rules shown above and below this one
        (`None` at an end): where Alt+Up and Alt+Down move it. The *shown* neighbours,
        not the model's, so under a filter a step lands past the row the user sees."""
        self.rule = rule
        self.index = index
        self.neighbours = neighbours

        subtitle = rule_subtitle(rule)
        # Labels and match patterns are user text: as Pango markup `&` renders blank.
        self.widget = Adw.ActionRow(title=rule_title(rule), subtitle=subtitle, use_markup=False)

        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        handle.add_css_class("dim-label")
        if editable:
            handle.set_tooltip_text(REORDER_HINT)
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
        self.enabled_switch: Gtk.Switch = switch
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
            self._wire_keys(actions)

    @staticmethod
    def _on_switch(_switch: Gtk.Switch, state: bool, actions: RuleActions, index: int) -> bool:
        actions.enable(index, state)
        # Handled: the refresh rebuilds the row from the model, which is the truth.
        return True

    def _wire_keys(self, actions: RuleActions) -> None:
        """Alt+Up and Alt+Down: the keyboard route to the same move as the drag."""
        keys = Gtk.ShortcutController()
        for accelerator, neighbour in zip(
            ("<Alt>Up", "<Alt>Down"), self.neighbours, strict=True
        ):
            keys.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(accelerator),
                    Gtk.CallbackAction.new(self._on_step, neighbour, actions),
                )
            )
        self.widget.add_controller(keys)

    def _on_step(
        self, widget: Gtk.Widget, _args: object, neighbour: int | None, actions: RuleActions
    ) -> bool:
        if neighbour is None:
            widget.error_bell()  # already first (or last) of the rules shown
            return True
        actions.move(self.index, neighbour)
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
    section = entity_page_id("window_rules")
    title = "Window rules"
    empty_hint = "Add one with the button above, or import an existing config."

    def __init__(self, session: Session, *, actions: RuleActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[RuleRow] = []
        self._filter_text = ""
        self._active_chips: set[Chip] = set()
        self._chip_vocabulary: tuple[Chip, ...] = ()
        self.chip_buttons: dict[Chip, Gtk.ToggleButton] = {}

        self._page = Adw.PreferencesPage(title=self.title)

        self._filter = Gtk.SearchEntry(placeholder_text="Filter by label, match or effect")
        self._filter.connect("search-changed", self._on_filter_changed)

        filter_group = Adw.PreferencesGroup()
        filter_group.add(self._filter)
        self._page.add(filter_group)

        # One chip per match prop and effect the list uses; built by `_sync_chips`.
        self.chip_box = Adw.PreferencesGroup(visible=False)
        self._chip_rows: dict[ChipGroup, Adw.WrapBox] = {}
        for chip_group, caption in ((ChipGroup.MATCH, "Match"), (ChipGroup.EFFECT, "Effect")):
            wrap = Adw.WrapBox(child_spacing=6, line_spacing=6, hexpand=True)
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_bottom=6)
            line.append(
                Gtk.Label(
                    label=caption,
                    css_classes=["dim-label", "caption-heading"],
                    xalign=0,
                    yalign=0,
                    width_chars=6,
                    margin_top=6,
                )
            )
            line.append(wrap)
            self.chip_box.add(line)
            self._chip_rows[chip_group] = wrap
        self._page.add(self.chip_box)

        self._group = Adw.PreferencesGroup(
            title=self.title,
            description=(
                "Rules apply top to bottom; later rules win when they set the same "
                "effect. Drag the handle to reorder, or press Alt+Up or Alt+Down on a rule."
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

    def _sync_chips(self, rules: list[Rule]) -> None:
        """Bring the chips in line with the list: one per prop and effect in use.

        An active chip whose prop left the list is dropped from the filter first, so
        deleting the last rule that carried it widens the list instead of emptying it.
        Buttons are rebuilt only when the vocabulary changed: a toggle refreshes the page
        from inside its own `toggled` signal, and replacing the button there would pull
        the widget out from under the handler.
        """
        vocabulary = chips_for(self.kind, rules)
        self._active_chips.intersection_update(vocabulary)
        if vocabulary == self._chip_vocabulary:
            return
        self._chip_vocabulary = vocabulary
        for chip_group, wrap in self._chip_rows.items():
            for button in [b for c, b in self.chip_buttons.items() if c.group is chip_group]:
                wrap.remove(button)
        self.chip_buttons = {}
        for chip in vocabulary:
            button = Gtk.ToggleButton(label=chip.title, active=chip in self._active_chips)
            button.add_css_class("pill")
            button.connect("toggled", self._on_chip_toggled, chip)
            self._chip_rows[chip.group].append(button)
            self.chip_buttons[chip] = button
        self.chip_box.set_visible(bool(vocabulary))
        for chip_group, wrap in self._chip_rows.items():
            wrap.get_parent().set_visible(any(c.group is chip_group for c in vocabulary))

    def _on_chip_toggled(self, button: Gtk.ToggleButton, chip: Chip) -> None:
        if button.get_active():
            self._active_chips.add(chip)
        else:
            self._active_chips.discard(chip)
        self.refresh()

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
            release(widget)
        self._listed = []
        self._rows = []

        editable = bool(self._session.live)
        self._add_button.set_sensitive(editable)

        rules = self.rules
        self._sync_chips(rules)
        visible = filter_rules(rules, self._filter_text, self._active_chips)
        for position, index in enumerate(visible):
            above = visible[position - 1] if position > 0 else None
            below = visible[position + 1] if position + 1 < len(visible) else None
            row = RuleRow(
                rules[index],
                index,
                actions=self._actions,
                editable=editable,
                neighbours=(above, below),
            )
            self._rows.append(row)
            self._group.add(row.widget)
            self._listed.append(row.widget)

        if not visible:
            if rules:
                empty = Adw.ActionRow(
                    title="No rules match this filter",
                    subtitle="Clear the search or chips to see all rules.",
                )
            else:
                empty = Adw.ActionRow(
                    title=f"No {self.title.lower()} yet", subtitle=self.empty_hint
                )
            self._group.add(empty)
            self._listed.append(empty)


class WindowRulesPage(RulesPage):
    """The window-rule instantiation. Class attributes only: the shell reads `section`
    and `title` off the class, so a parameterised constructor would not do."""

    kind = "window"
    section = entity_page_id("window_rules")
    title = "Window rules"


class LayerRulesPage(RulesPage):
    """The layer-rule instantiation -- ADR-0008's "same list model and editor shell"."""

    kind = "layer"
    section = entity_page_id("layer_rules")
    title = "Layer rules"
