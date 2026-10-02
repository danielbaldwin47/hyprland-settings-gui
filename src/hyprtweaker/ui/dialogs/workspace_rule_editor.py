"""The workspace rule editor: the selector, and room for the fields (#159, #160).

A rule's identity is its selector (ADR-0008), so the one thing this dialog must get right
is what happens when the typed selector already has a rule: it stays open and says so,
with "Show it" to go to that rule, instead of closing on a save the session refused.

Every field rides through a save untouched: `collect_fields` returns the rule's own
mapping, the same object, until field rows exist to say otherwise. A save is
`dataclasses.replace(rule, workspace=new, fields=...)`, so `origin` rides along too.

Extension points, for the field rows and selector modes (#160):
- `add_group(group)` puts a group of rows below the selector, in the dialog's one page.
- `collect_fields()` is what a save stores as the rule's fields.
- `selector_problem(text)` judges a selector the user typed; it never sees an untouched
  one, so an imported selector the app cannot parse still saves with its other fields.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import replace
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import WorkspaceRule  # noqa: E402

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
    ) -> None:
        """`taken` are the selectors other rules hold: saving onto one is refused here,
        with the way to that rule, because the session refuses it too and a dialog that
        closes on a refused save looks like a save. `on_show(selector)` goes to the rule
        that holds `selector` -- the duplicate message's "Show it"."""
        super().__init__(
            title="Edit workspace rule" if rule is not None else "Add workspace rule",
            content_width=480,
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

        self._selector = Adw.EntryRow(title="Workspace")
        if rule is not None:
            self._selector.set_text(rule.workspace)
        self._selector.connect("changed", lambda _row: self._clear_messages())
        self._selector.connect("entry-activated", lambda _row: self.save())

        # The duplicate message sits under the entry it is about, with the way out on it.
        self._duplicate = Adw.ActionRow(use_markup=False, visible=False)
        self._duplicate.add_css_class("warning")
        self._show = Gtk.Button(label="Show it", valign=Gtk.Align.CENTER)
        self._show.connect("clicked", lambda _button: self._show_existing())
        self._duplicate.add_suffix(self._show)

        selector_group = Adw.PreferencesGroup(
            title="Workspace",
            description=(
                "Which workspace the rule is for: a number such as 5, a name such as "
                "name:web, or special:scratch."
            ),
        )
        selector_group.add(self._selector)
        selector_group.add(self._duplicate)

        self._groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._groups.append(selector_group)

        self.set_child(self._body())
        # The selector is the dialog's one question: the cursor starts there.
        self.set_focus(self._selector)

    def _body(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar(show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)
        header.pack_end(self._save_button)
        box.append(header)
        box.append(self._error)
        # No scroller yet: one around a selector-only body pinned the dialog at its first
        # height, cutting the duplicate row off when it appeared (widget probe). Field
        # groups (#160) bring the height that needs a scroller and a `content_height`.
        clamp = Adw.Clamp(margin_top=12, margin_bottom=18, margin_start=12, margin_end=12)
        clamp.set_child(self._groups)
        box.append(clamp)
        return box

    # --- extension points (#160) -----------------------------------------------------------

    def add_group(self, group: Adw.PreferencesGroup) -> None:
        """Add a group of rows below the selector, in order of the calls."""
        self._groups.append(group)

    def collect_fields(self) -> Mapping[str, Any]:
        """The fields a save stores: the rule's own, untouched, while nothing edits them."""
        return self._original.fields if self._original is not None else {}

    def selector_problem(self, selector: str) -> str | None:
        """Why a selector the user typed cannot be saved, or `None` when it can."""
        if not selector:
            return "Type a workspace selector, such as 5 or name:web."
        return None

    # --- what tests and probes drive --------------------------------------------------------

    @property
    def selector_row(self) -> Adw.EntryRow:
        return self._selector

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
        text = self._selector.get_text()
        # Untouched means byte-identical: an imported selector is kept exactly as read.
        selector = original if original is not None and text == original else text.strip()

        if selector != original:
            problem = self.selector_problem(selector)
            if problem is not None:
                self._error.set_label(problem)
                self._error.set_visible(True)
                return
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

    def _show_existing(self) -> None:
        self.close()
        self._on_show(self._duplicate_of)
