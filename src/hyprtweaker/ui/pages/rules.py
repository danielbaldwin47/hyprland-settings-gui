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

**One row of chips per group.** A 332-rule rice uses about 75 chips, and wrapped they push
the list off the screen. The chips that fit stay on the row, in order; the rest fold behind
a "+N" chip whose popover holds them, and a chip picked there filters like any other. The
"+N" chip looks pressed, and says "(1 on)", while a chip folded behind it is on, so an
active filter is never out of sight.

**Reorder has a keyboard route.** Alt+Up and Alt+Down on a row move it past the rule shown
above or below it, the same keys as the Binds page, and focus follows the moved rule.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from hyprtweaker.engine.rule_filter import (  # noqa: E402
    Chip,
    ChipGroup,
    chips_for,
    filter_rules,
)
from hyprtweaker.ui.flash import flash  # noqa: E402
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

        # Shown but insensitive on a read-only session, like the switch and the Workspaces
        # page: the Banner says why, and the row still says what could be done once it is
        # live. Only the move routes (drag, Alt+Up/Down) stay unwired.
        self.edit_button = Gtk.Button(
            icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER
        )
        self.edit_button.add_css_class("flat")
        self.edit_button.set_tooltip_text("Edit this rule")
        self.edit_button.set_sensitive(editable)
        self.edit_button.connect("clicked", lambda _button: actions.edit(index))
        self.widget.add_suffix(self.edit_button)

        self.remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER
        )
        self.remove_button.add_css_class("flat")
        self.remove_button.set_tooltip_text("Remove this rule")
        self.remove_button.set_sensitive(editable)
        self.remove_button.connect("clicked", lambda _button: actions.remove(index))
        self.widget.add_suffix(self.remove_button)

        if editable:
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


CHIP_SPACING = 6
CHIP_CLASS = "hyprtweaker-chip"
_chip_css_installed = False


def chip_button(label: str = "", *, active: bool = False) -> Gtk.ToggleButton:
    """A filter chip: a compact round toggle.

    Adwaita's `pill` is a call-to-action button, 32 px of padding a side, so a row held
    two or three chips and 55 of a big rice's effects folded away. This one is about half
    as wide; its CSS rule is installed once, lazily, as `flash` does.
    """
    global _chip_css_installed
    display = Gdk.Display.get_default()
    if not _chip_css_installed and display is not None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            f"button.{CHIP_CLASS} {{"
            " padding: 2px 12px; min-height: 26px; border-radius: 9999px;"
            " }"
        )
        Gtk.StyleContext.add_provider_for_display(
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        _chip_css_installed = True
    return Gtk.ToggleButton(label=label, active=active, css_classes=[CHIP_CLASS])


def chips_that_fit(widths: list[int], width: int, more_width: int) -> int:
    """How many chips, in order, fit on a line `width` wide: all of them, or as many as
    leave room for the "+N" chip that stands for the rest."""
    if sum(widths) + CHIP_SPACING * (len(widths) - 1) <= width:
        return len(widths)
    used = more_width
    for count, chip_width in enumerate(widths):
        used += chip_width + CHIP_SPACING
        if used > width:
            return count
    return len(widths)


def _at(x: int) -> Gsk.Transform:
    return Gsk.Transform().translate(Graphene.Point().init(x, 0))


class _OneLineLayout(Gtk.LayoutManager):
    """Lays a chip line out on one row: the chips that fit, then the "+N" chip.

    The split is decided here because only an allocation knows the width. A chip past it is
    unmapped with `set_child_visible`, as `GtkPaned` hides a collapsed child, not removed,
    so the page keeps one button per chip and its state. `on_fold` hears the new count from
    an idle: it relabels the "+N" chip and refills the popover, and neither may change size
    inside an allocation.
    """

    def __init__(self, more: Gtk.Widget, popover: Gtk.Popover, on_fold: Callable[[], None]):
        super().__init__()
        self._more = more
        self._popover = popover
        self._on_fold = on_fold
        self.shown: int | None = None
        """How many chips the row shows; `None` until the first allocation."""
        self._reporting = False

    def _chips(self, widget: Gtk.Widget) -> list[Gtk.Widget]:
        chips = []
        child = widget.get_first_child()
        while child is not None:
            if child is not self._more and child is not self._popover and child.get_visible():
                chips.append(child)
            child = child.get_next_sibling()
        return chips

    def do_measure(
        self, widget: Gtk.Widget, orientation: Gtk.Orientation, _for_size: int
    ) -> tuple[int, int, int, int]:
        naturals = [chip.measure(orientation, -1)[1] for chip in self._chips(widget)]
        more = self._more.measure(orientation, -1)[1]
        if orientation == Gtk.Orientation.HORIZONTAL:
            # As narrow as the "+N" chip alone, as wide as every chip on one row.
            natural = sum(naturals) + CHIP_SPACING * max(len(naturals) - 1, 0)
            return more, max(natural, more), -1, -1
        tallest = max([*naturals, more])
        return tallest, tallest, -1, -1

    def do_allocate(self, widget: Gtk.Widget, width: int, height: int, _baseline: int) -> None:
        chips = self._chips(widget)
        widths = [chip.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for chip in chips]
        more_width = self._more.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        shown = chips_that_fit(widths, width, more_width)
        x = 0
        for index, (chip, chip_width) in enumerate(zip(chips, widths, strict=True)):
            chip.set_child_visible(index < shown)
            if index < shown:
                chip.allocate(chip_width, height, -1, _at(x))
                x += chip_width + CHIP_SPACING
        self._more.set_child_visible(shown < len(chips))
        if shown < len(chips):
            self._more.allocate(more_width, height, -1, _at(x))
            target = Gdk.Rectangle()
            target.x, target.y, target.width, target.height = x, 0, more_width, height
            self._popover.set_pointing_to(target)
        self._popover.present()
        if shown != self.shown:
            self.shown = shown
            if not self._reporting:
                self._reporting = True
                GLib.idle_add(self._report)

    def _report(self) -> bool:
        self._reporting = False
        self._on_fold()
        return GLib.SOURCE_REMOVE


def _set_twin(twin: Gtk.ToggleButton, row_chip: Gtk.ToggleButton) -> None:
    row_chip.set_active(twin.get_active())


class ChipLine:
    """One group's chips (Match or Effect): a single row, and a "+N" chip for the rest.

    The row holds one toggle per chip; the page owns which chips exist and which are on.
    The popover holds a twin toggle for each folded chip, and a twin's click sets its row
    chip, so the filter has one source of truth whichever button the user pressed.
    """

    def __init__(self, on_fold: Callable[[], None]) -> None:
        self.more = chip_button()
        self.more.connect("clicked", self._on_more_clicked)

        self._folded_box = Adw.WrapBox(
            child_spacing=CHIP_SPACING,
            line_spacing=CHIP_SPACING,
            natural_line_length=480,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        scroller = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            propagate_natural_height=True,
            propagate_natural_width=True,
            max_content_height=360,
            child=self._folded_box,
        )
        self.popover = Gtk.Popover(child=scroller)
        self.folded: dict[Chip, Gtk.ToggleButton] = {}
        self._on = False

        self.box = Gtk.Box(hexpand=True)
        self.layout = _OneLineLayout(self.more, self.popover, on_fold)
        self.box.set_layout_manager(self.layout)
        self.box.append(self.more)
        self.more.set_child_visible(False)
        self.popover.set_parent(self.box)

    def set_chips(self, buttons: list[Gtk.ToggleButton]) -> None:
        """Replace the row's chips; the next allocation decides again what folds."""
        child = self.box.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            if child is not self.more and child is not self.popover:
                self.box.remove(child)
            child = following
        for button in buttons:
            button.insert_before(self.box, self.more)
        self._drop_twins(set(self.folded))  # each twin sets a row chip that is now gone
        self.layout.shown = None
        self.box.queue_allocate()

    def fold(
        self, chips: list[Chip], buttons: dict[Chip, Gtk.ToggleButton], active: set[Chip]
    ) -> None:
        """Bring the popover in line with the chips the row could not show.

        A twin still folded stays where it is, so a popover left open while the row
        refolds (a "(1 on)" widens the "+N" chip) keeps the button under the user's
        pointer and focus.
        """
        shown = len(chips) if self.layout.shown is None else self.layout.shown
        wanted = chips[shown:]
        self._drop_twins(set(self.folded).difference(wanted))
        previous: Gtk.Widget | None = None
        for chip in wanted:
            twin = self.folded.get(chip)
            if twin is None:
                twin = chip_button(chip.title, active=chip in active)
                twin.connect("toggled", _set_twin, buttons[chip])
                self._folded_box.insert_child_after(twin, previous)
                self.folded[chip] = twin
            previous = twin
        self.folded = {chip: self.folded[chip] for chip in wanted}
        if not self.folded:
            self.popover.popdown()
        self.show_state(active)

    def _drop_twins(self, chips: set[Chip]) -> None:
        for chip in chips:
            twin = self.folded.pop(chip)
            self._folded_box.remove(twin)
            release(twin)

    def show_state(self, active: set[Chip]) -> None:
        """The "+N" chip: how many chips it holds, and whether one of them is on."""
        count = len(self.folded)
        on = sum(chip in active for chip in self.folded)
        self.more.set_label(f"+{count} ({on} on)" if on else f"+{count}")
        self.more.set_tooltip_text(f"Show {count} more filters")
        self._on = bool(on)
        self.more.set_active(self._on)

    def _on_more_clicked(self, _button: Gtk.ToggleButton) -> None:
        # A click flips a toggle; this one's pressed look means "a folded chip is on".
        self.more.set_active(self._on)
        self.popover.popup()


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
        self._chip_lines: dict[ChipGroup, ChipLine] = {}
        for chip_group, caption in ((ChipGroup.MATCH, "Match"), (ChipGroup.EFFECT, "Effect")):
            chip_line = ChipLine(lambda group=chip_group: self._on_fold(group))
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin_bottom=6)
            line.append(
                Gtk.Label(
                    label=caption,
                    css_classes=["dim-label", "caption-heading"],
                    xalign=0,
                    width_chars=6,
                )
            )
            line.append(chip_line.box)
            self.chip_box.add(line)
            self._chip_lines[chip_group] = chip_line
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
        self.chip_buttons = {}
        for chip in vocabulary:
            button = chip_button(chip.title, active=chip in self._active_chips)
            button.connect("toggled", self._on_chip_toggled, chip)
            self.chip_buttons[chip] = button
        self.chip_box.set_visible(bool(vocabulary))
        for chip_group, chip_line in self._chip_lines.items():
            chips = self._group_chips(chip_group)
            chip_line.set_chips([self.chip_buttons[chip] for chip in chips])
            chip_line.fold(chips, self.chip_buttons, self._active_chips)
            chip_line.box.get_parent().set_visible(bool(chips))

    def _group_chips(self, chip_group: ChipGroup) -> list[Chip]:
        return [chip for chip in self._chip_vocabulary if chip.group is chip_group]

    def _on_fold(self, chip_group: ChipGroup) -> None:
        """The row's width changed what fits: refill that group's "+N" popover."""
        self._chip_lines[chip_group].fold(
            self._group_chips(chip_group), self.chip_buttons, self._active_chips
        )

    @property
    def more_buttons(self) -> dict[ChipGroup, Gtk.ToggleButton]:
        """Each group's "+N" chip, shown while some of its chips are folded."""
        return {group: line.more for group, line in self._chip_lines.items()}

    @property
    def more_popovers(self) -> dict[ChipGroup, Gtk.Popover]:
        return {group: line.popover for group, line in self._chip_lines.items()}

    def folded_buttons(self, chip_group: ChipGroup) -> dict[Chip, Gtk.ToggleButton]:
        """The chips folded into `chip_group`'s popover, each its row chip's twin."""
        return dict(self._chip_lines[chip_group].folded)

    def _on_chip_toggled(self, button: Gtk.ToggleButton, chip: Chip) -> None:
        if button.get_active():
            self._active_chips.add(chip)
        else:
            self._active_chips.discard(chip)
        self._chip_lines[chip.group].show_state(self._active_chips)
        self.refresh()

    def _on_filter_changed(self, entry: Gtk.SearchEntry) -> None:
        self._apply_filter(entry.get_text())

    def _apply_filter(self, text: str) -> None:
        folded = text.strip().lower()
        if folded == self._filter_text:
            # The entry's debounced `search-changed` arriving after a programmatic change
            # (`reveal`, `set_filter`): rebuilding again would drop the row just flashed.
            return
        self._filter_text = folded
        self.refresh()

    def reveal(self, index: int) -> Gtk.Widget | None:
        """Bring the row for the rule at `index` into view and flash it -- a search hit, or
        the rule just moved (a move rebuilds every row, so focus follows it for the next
        Alt+Up or Alt+Down).

        A filter text or chip that hides the rule is cleared first: a hit that lands on "No
        rules match this filter" is a dead end. One that shows it stays, so moving a rule in
        a filtered list keeps the filter. Navigate + flash as `BindsPage.reveal`; returns
        the row for an explicit scroll, or `None` when the list has no rule at `index`.
        """
        shown = any(row.index == index for row in self._rows)
        if not shown and (self._filter_text or self._active_chips):
            self._active_chips.clear()
            for button in self.chip_buttons.values():
                button.set_active(False)
            self._filter_text = ""
            self._filter.set_text("")
            self.refresh()
        for row in self._rows:
            if row.index == index:
                row.widget.grab_focus()
                flash(row.widget)
                return row.widget
        return None

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
