"""The Workspaces Page: one row per workspace rule (ADR-0008 §Workspace rules, #159).

**Identity is the selector.** Hyprland merges rules that share a selector, so the list holds
one rule per selector and every action addresses a rule by that string, never by position:
the Page is rebuilt wholesale on every change, like the Rules and declaration Pages, and a
selector survives a rebuild where an index would not.

**Fields ride along.** A row summarises the rule's fields; the editor this Page opens
edits the selector, and a save carries every field through untouched -- unknown keys
included, because a config the app declines to keep is one the user cannot fix in it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import WorkspaceRule  # noqa: E402
from hyprtweaker.engine.rule_filter import value_text  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session

NO_FIELDS = "Nothing set yet"
"""The subtitle of a rule with no fields: a new rule, until its fields are set."""


def fields_summary(fields: Mapping[str, Any]) -> str:
    """The row subtitle: `monitor DP-1, default, decorate off`, the Rules pages' grammar."""
    parts = []
    for name, value in fields.items():
        if value is True:
            parts.append(name)
        elif value is False:
            parts.append(f"{name} off")
        else:
            parts.append(f"{name} {value_text(value)}")
    return ", ".join(parts) or NO_FIELDS


@dataclass(frozen=True, slots=True)
class WorkspaceRuleActions:
    """The verbs the window wires into the Page. Each takes the rule's selector."""

    add: Callable[[], None]
    edit: Callable[[str], None]
    remove: Callable[[str], None]


class WorkspaceRuleRow:
    """One `Adw.ActionRow` for one rule: selector, field summary, edit, remove."""

    def __init__(
        self, rule: WorkspaceRule, *, actions: WorkspaceRuleActions, editable: bool
    ) -> None:
        self.rule = rule
        selector = rule.workspace
        # The selector is user text (`w[tv1]`, `name:a&b`): as Pango markup `&` renders blank.
        self.widget = Adw.ActionRow(
            title=selector, subtitle=fields_summary(rule.fields), use_markup=False
        )

        self.edit_button = Gtk.Button(
            icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER
        )
        self.edit_button.add_css_class("flat")
        self.edit_button.set_tooltip_text("Edit this rule")
        self.edit_button.connect("clicked", lambda _button: actions.edit(selector))
        self.widget.add_suffix(self.edit_button)

        self.remove_button = Gtk.Button(
            icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER
        )
        self.remove_button.add_css_class("flat")
        self.remove_button.set_tooltip_text("Remove this rule")
        self.remove_button.connect("clicked", lambda _button: actions.remove(selector))
        self.widget.add_suffix(self.remove_button)

        # Shown but insensitive on a read-only session, like the header's add button: the
        # Banner says why, and the row still says what could be done once it is live.
        self.edit_button.set_sensitive(editable)
        self.remove_button.set_sensitive(editable)


class WorkspaceRulesPage:
    """The Workspaces Page, rebuilt from the session's list on every `refresh`."""

    section = entity_page_id("workspace_rules")
    title = "Workspaces"
    empty_title = "No workspace rules yet"
    empty_hint = "Add one to give a workspace its own layout, gaps or monitor."

    def __init__(self, session: Session, *, actions: WorkspaceRuleActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[WorkspaceRuleRow] = []
        self._listed: list[Gtk.Widget] = []
        self._empty_row: Adw.ActionRow | None = None
        self._empty_add: Gtk.Button | None = None

        self._page = Adw.PreferencesPage(title=self.title)
        self._group = Adw.PreferencesGroup(
            title="Workspace rules",
            description=(
                "Settings a workspace keeps wherever it opens. One rule per workspace: "
                "Hyprland merges rules that name the same one."
            ),
        )
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text("Add a workspace rule")
        add.connect("clicked", lambda _button: self._actions.add())
        self._add_button = add
        self._group.set_header_suffix(add)
        self._page.add(self._group)

        self.refresh()

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def rows(self) -> tuple[WorkspaceRuleRow, ...]:
        """Every built Row, in list order. What the UI smoke tier asserts against."""
        return tuple(self._rows)

    @property
    def rules(self) -> list[WorkspaceRule]:
        return list(self._session.workspace_rules)

    @property
    def add_button(self) -> Gtk.Button:
        return self._add_button

    @property
    def empty_row(self) -> Adw.ActionRow | None:
        """The empty-state row, present only while there is no rule."""
        return self._empty_row

    @property
    def empty_add_button(self) -> Gtk.Button | None:
        """The empty state's own add action -- the one control an empty page needs."""
        return self._empty_add

    def refresh(self) -> None:
        """Rebuild the list from the model."""
        for widget in self._listed:
            self._group.remove(widget)
            release(widget)
        self._listed = []
        self._rows = []
        self._empty_row = None
        self._empty_add = None

        editable = bool(self._session.live)
        self._add_button.set_sensitive(editable)

        for rule in self.rules:
            row = WorkspaceRuleRow(rule, actions=self._actions, editable=editable)
            self._rows.append(row)
            self._group.add(row.widget)
            self._listed.append(row.widget)

        if not self._rows:
            # A new button with each empty row: the refresh above releases the old row and
            # everything under it, so a button kept across refreshes would come back dead.
            add = Gtk.Button(label="Add rule", valign=Gtk.Align.CENTER, sensitive=editable)
            add.connect("clicked", lambda _button: self._actions.add())
            empty = Adw.ActionRow(title=self.empty_title, subtitle=self.empty_hint)
            empty.add_suffix(add)
            self._empty_row = empty
            self._empty_add = add
            self._group.add(empty)
            self._listed.append(empty)

    def reveal(self, selector: str) -> bool:
        """Bring the row for `selector` into view and flash it; False when there is none.

        Navigate + flash, the `BindsPage.reveal` shape: focus scrolls every ancestor to the
        row, and the pulse marks it for a reader whose eyes were elsewhere -- on the dialog
        that said "A rule for 5 exists", or on a search entry (#172).
        """
        for row in self._rows:
            if row.rule.workspace == selector:
                row.widget.grab_focus()
                flash(row.widget)
                return True
        return False
