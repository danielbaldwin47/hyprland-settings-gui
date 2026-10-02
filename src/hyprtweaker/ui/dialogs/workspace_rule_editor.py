"""The workspace rule editor: the selector, and the rule's fields (#159, #160).

A rule's identity is its selector (ADR-0008), so the one thing this dialog must get right
is what happens when the typed selector already has a rule: it stays open and says so,
with "Show it" to go to that rule, instead of closing on a save the session refused.

**Two selector modes.** Simple: a kind (number, name, special workspace) and one entry,
which compose `5`, `name:web` or `special:scratch`. Advanced: the raw string, for the
filters Hyprland reads (`w[tv1]`, `r[2-4]`, `f[1]s[false]`). The mode follows the value on
open: a selector the pickers cannot say opens in advanced. The headless grammar is
`engine/workspace_selector`.

**An imported selector never blocks Save.** Only a selector the user edited is judged: a
rule the app cannot parse would otherwise be uneditable here (CONTEXT.md "Finding"). It
opens in advanced with a line saying Hyprland may not read it, and saves with its fields.

The fields are `WorkspaceFieldRows`; a save is `dataclasses.replace(rule, workspace=new,
fields=collect_fields())`, so `origin` rides along too.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import WorkspaceRule  # noqa: E402
from hyprtweaker.engine.workspace_catalog import BUILTIN_LAYOUTS  # noqa: E402
from hyprtweaker.engine.workspace_selector import (  # noqa: E402
    Severity,
    SimpleKind,
    check_selector,
    compose_simple,
    parse_simple,
)
from hyprtweaker.ui.dialogs.workspace_fields import WorkspaceFieldRows  # noqa: E402

_KINDS = [SimpleKind.NUMBER, SimpleKind.NAME, SimpleKind.SPECIAL]
_KIND_TITLES = {
    SimpleKind.NUMBER: "Number",
    SimpleKind.NAME: "Name",
    SimpleKind.SPECIAL: "Special name",
}

_BLANK_PICKER = {
    SimpleKind.NUMBER: "Type a workspace number.",
    SimpleKind.NAME: "Type a name.",
    SimpleKind.SPECIAL: "Type a name for the special workspace.",
}

Save = Callable[[WorkspaceRule], str | None]
"""Store the rule: `None` when it was stored, else why not, as a sentence fragment."""


class WorkspaceRuleEditor(Adw.Dialog):
    """Add a workspace rule, or edit the selector of an existing one."""

    def __init__(
        self,
        *,
        on_done: Save,
        on_show: Callable[[str], None],
        rule: WorkspaceRule | None = None,
        taken: Collection[str] = (),
        layouts: Sequence[str] = BUILTIN_LAYOUTS,
    ) -> None:
        """`taken` are the selectors other rules hold: saving onto one is refused here,
        with the way to that rule, because the session refuses it too and a dialog that
        closes on a refused save looks like a save. `on_show(selector)` goes to the rule
        that holds `selector` -- the duplicate message's "Show it". `layouts` are the
        choices of the layout row; a layout the rule already names joins them."""
        super().__init__(
            title="Edit workspace rule" if rule is not None else "Add workspace rule",
            content_width=560,
            content_height=640,
        )
        self._on_done = on_done
        self._on_show = on_show
        self._original = rule
        self._taken = frozenset(taken)
        self._duplicate_of = ""

        self._save_button = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self._save_button.connect("clicked", lambda _button: self.save())

        # A label, not an `Adw.Banner`: ADR-0016 keeps the Banner for the window's health.
        self._error = Gtk.Label(
            wrap=True,
            xalign=0.0,
            visible=False,
            css_classes=["error"],
            margin_start=12,
            margin_end=12,
            margin_top=6,
        )

        # The advanced entry holds the whole selector string; the pickers compose one. Only
        # the visible half is read (`_selector_text`).
        self._selector = Adw.EntryRow(title="Selector")
        self._selector.connect("changed", lambda _row: self._on_selector_changed())
        self._selector.connect("entry-activated", lambda _row: self.save())
        leave = Gtk.EventControllerFocus()
        leave.connect("leave", lambda _c: self._show_notice())
        self._selector.add_controller(leave)

        self._kind = Adw.ComboRow(
            title="Workspace",
            model=Gtk.StringList.new(["Number", "Name", "Special workspace"]),
        )
        self._value = Adw.EntryRow(title="Number")
        self._kind.connect("notify::selected", lambda *_: self._on_kind_changed())
        self._value.connect("changed", lambda _row: self._clear_messages())
        self._value.connect("entry-activated", lambda _row: self.save())

        self._advanced = Adw.SwitchRow(
            title="Advanced selector",
            subtitle="Match workspaces by their windows, monitor or state",
        )
        self._advanced.connect("notify::active", lambda *_: self._on_mode_toggled())

        # Non-blocking: a selector Hyprland may not read still saves (see the module docs).
        self._notice = Gtk.Label(
            wrap=True, xalign=0.0, visible=False, css_classes=["dim-label"], margin_start=12
        )

        # The duplicate message sits under the entry it is about, with the way out on it.
        self._duplicate = Adw.ActionRow(use_markup=False, visible=False)
        self._duplicate.add_css_class("warning")
        self._show = Gtk.Button(label="Show it", valign=Gtk.Align.CENTER)
        self._show.connect("clicked", lambda _button: self._show_existing())
        self._duplicate.add_suffix(self._show)

        selector_group = Adw.PreferencesGroup(
            title="Workspace",
            description="Which workspace the rule is for.",
        )
        selector_group.add(self._kind)
        selector_group.add(self._value)
        selector_group.add(self._selector)
        selector_group.add(self._advanced)
        selector_group.add(self._duplicate)

        self._syncing = False
        self._load_selector(rule.workspace if rule is not None else "")
        if rule is not None:
            self._notice_for_imported(rule.workspace)

        self._groups = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=18,
            margin_top=12,
            margin_bottom=18,
            margin_start=12,
            margin_end=12,
        )
        self._groups.append(selector_group)
        self._groups.append(self._notice)

        self._fields = WorkspaceFieldRows(
            rule.fields if rule is not None else {}, layouts=layouts
        )
        for group in self._fields.groups:
            self._groups.append(group)

        self.set_child(self._body())
        # The selector is the dialog's one question: the cursor starts there.
        self.set_focus(self._selector if self._advanced.get_active() else self._value)

    def _body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        header.pack_end(self._save_button)
        box.append(header)
        box.append(self._error)
        # The dialog has a fixed height now that field rows make it tall, so a scroller
        # cannot pin it at a too-small first height (#159's widget probe). The error line
        # stays above the scroller, where a long form cannot push it out of sight.
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self._groups)
        box.append(scroller)
        return box

    def collect_fields(self) -> Mapping[str, Any]:
        """The fields a save stores: the rule's own mapping while no row was touched."""
        return self._fields.collect()

    # --- what tests and probes drive --------------------------------------------------------

    @property
    def selector_row(self) -> Adw.EntryRow:
        """The advanced entry: the whole selector string. Shown in advanced mode."""
        return self._selector

    @property
    def mode_switch(self) -> Adw.SwitchRow:
        """On means advanced."""
        return self._advanced

    @property
    def kind_row(self) -> Adw.ComboRow:
        return self._kind

    @property
    def value_row(self) -> Adw.EntryRow:
        return self._value

    @property
    def notice_label(self) -> Gtk.Label:
        return self._notice

    @property
    def fields(self) -> WorkspaceFieldRows:
        return self._fields

    @property
    def error_label(self) -> Gtk.Label:
        return self._error

    @property
    def duplicate_row(self) -> Adw.ActionRow:
        return self._duplicate

    @property
    def show_button(self) -> Gtk.Button:
        return self._show

    def save(self) -> None:
        """Store the rule and close, or stay open and say why not."""
        self._clear_messages()
        original = self._original.workspace if self._original is not None else None
        text = self._selector_text()
        # Untouched means byte-identical: an imported selector is kept exactly as read.
        selector = original if original is not None and text == original else text.strip()

        if selector != original:
            # Only an error blocks; a warning shows under the selector and the save goes on.
            issue = check_selector(selector)
            problem = self._simple_problem() or (
                issue.message
                if issue is not None and issue.severity is Severity.ERROR
                else None
            )
            if problem is not None:
                self._error.set_label(problem)
                self._error.set_visible(True)
                return
            self._show_notice()
            if selector in self._taken:
                self._duplicate_of = selector
                self._duplicate.set_title(f"A rule for “{selector}” exists")
                self._duplicate.set_visible(True)
                return

        fields = self.collect_fields()
        rule = (
            replace(self._original, workspace=selector, fields=fields)
            if self._original is not None
            else WorkspaceRule(workspace=selector, fields=fields)
        )
        refused = self._on_done(rule)
        if refused is not None:
            self._error.set_label(f"Can't save: {refused}")
            self._error.set_visible(True)
            return
        self.close()

    def _clear_messages(self) -> None:
        self._error.set_visible(False)
        self._duplicate.set_visible(False)

    # --- selector modes -------------------------------------------------------------------

    def _selector_text(self) -> str:
        """What the visible half of the selector group says."""
        if self._advanced.get_active():
            return self._selector.get_text()
        return self._selector_text_from_pickers()

    def _load_selector(self, text: str) -> None:
        """Show `text` in the mode that can say it: the pickers if they can, else raw."""
        parsed = parse_simple(text)
        self._syncing = True
        try:
            self._selector.set_text(text)
            if parsed is not None:
                kind, value = parsed
                self._kind.set_selected(_KINDS.index(kind))
                self._value.set_text(value)
            else:
                self._value.set_text("")
            self._advanced.set_active(parsed is None and bool(text))
        finally:
            self._syncing = False
        self._apply_mode()

    def _apply_mode(self) -> None:
        advanced = self._advanced.get_active()
        self._selector.set_visible(advanced)
        self._kind.set_visible(not advanced)
        self._value.set_visible(not advanced)
        self._value.set_title(_KIND_TITLES[_KINDS[self._kind.get_selected()]])

    def _on_selector_changed(self) -> None:
        self._clear_messages()
        if self._syncing:
            return
        if not self._advanced.get_active():
            # The raw entry is hidden: this is a program setting the selector, not a user
            # typing, so follow the value into the mode that can show it.
            self._load_selector(self._selector.get_text())

    def _on_kind_changed(self) -> None:
        self._clear_messages()
        self._apply_mode()

    def _on_mode_toggled(self) -> None:
        if self._syncing:
            return
        self._clear_messages()
        if self._advanced.get_active():
            self._syncing = True
            self._selector.set_text(self._selector_text_from_pickers())
            self._syncing = False
            self._apply_mode()
            self._selector.grab_focus()
            return
        text = self._selector.get_text()
        parsed = parse_simple(text)
        if parsed is None and text:
            self._syncing = True
            self._advanced.set_active(True)
            self._syncing = False
            self._notice.set_label("This selector only works in advanced mode.")
            self._notice.set_visible(True)
            return
        self._load_selector(text)
        self._notice.set_visible(False)

    def _selector_text_from_pickers(self) -> str:
        return compose_simple(_KINDS[self._kind.get_selected()], self._value.get_text())

    def _simple_problem(self) -> str | None:
        """The pickers' own words for a value they cannot compose.

        The grammar's messages speak advanced syntax (`name:`), which the pickers keep out
        of sight, so a blank picker says what to type here instead. A number picker holds
        digits only: `abc` there would be read as a name."""
        if self._advanced.get_active():
            return None
        kind = _KINDS[self._kind.get_selected()]
        value = self._value.get_text().strip()
        if not value:
            return _BLANK_PICKER[kind]
        if kind is SimpleKind.NUMBER and not value.isdigit():
            return "A workspace number is digits only, such as 5. Pick Name for a name."
        return None

    def _show_notice(self) -> None:
        """The non-blocking line under the selector: a warning about what was typed."""
        text = self._selector_text()
        original = self._original.workspace if self._original is not None else None
        issue = check_selector(text) if text != original else None
        if issue is not None and issue.severity is Severity.WARNING:
            self._notice.set_label(issue.message)
            self._notice.set_visible(True)
        elif text != original:
            self._notice.set_visible(False)

    def _notice_for_imported(self, selector: str) -> None:
        issue = check_selector(selector)
        if issue is None:
            return
        self._notice.set_label(
            issue.message
            if issue.severity is Severity.WARNING
            else "Hyprland may not read this selector."
        )
        self._notice.set_visible(True)

    def _show_existing(self) -> None:
        self.close()
        self._on_show(self._duplicate_of)
