"""One Page class for the seven declarative Entity kinds (#70).

`RulesPage` twice over became `WindowRulesPage` and `LayerRulesPage`; this is the same move
seven ways, driven by `declaration_kinds` instead of by two class attributes. Rebuilt
wholesale on every change, exactly like Binds and Rules and for the same reason: an insert
or a delete shifts every index after it, and the index is the only address an edit has.

Two things this Page does that the rule pages do not:

**It shows findings.** Animations and curves can hold values Hyprland will refuse -- a
curve nothing declares, a speed out of range -- and the writer emits them anyway, because a
config the app declines to write is a config the user cannot fix in the app (ADR-0008's
rule for unknown effects). So the row says so, on the row, where the fix is.

**It lists what it cannot edit.** A gesture whose action is a Lua function came from
`user.lua`; it is shown with its trigger intact and no edit button, the way a
function-valued Bind action is (ADR-0007: "never silently dropped").
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.entities_catalog import (  # noqa: E402
    Finding,
    animation_findings,
    curve_findings,
    curve_usage,
    dangling_curve_references,
    missing_curve_references,
    unknown_device_fields,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.pages.declaration_kinds import (  # noqa: E402
    BY_KIND,
    DeclarationKind,
    filter_haystack,
    read_only,
    row_subtitle,
    row_title,
)


@dataclass(frozen=True, slots=True)
class DeclarationActions:
    """The verbs the window wires in. Every index is into the model's flat list."""

    add: Callable[[], None]
    edit: Callable[[int], None]
    remove: Callable[[int], None]


class DeclarationRow:
    """One `Adw.ActionRow` for one entity: summary, findings, edit, remove."""

    def __init__(
        self,
        entity: Any,
        index: int,
        *,
        kind: str,
        actions: DeclarationActions,
        editable: bool,
        findings: tuple[Finding, ...] = (),
    ) -> None:
        self.entity = entity
        self.index = index
        self.findings = findings

        subtitle = row_subtitle(kind, entity)
        if findings:
            # The finding replaces the summary rather than joining it: a row that is not
            # going to work has one thing worth reading, and burying it after the values
            # that caused it is how a warning gets skimmed past.
            subtitle = findings[0].message
        self.widget = Adw.ActionRow(title=row_title(kind, entity), subtitle=subtitle)

        if findings:
            warning = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
            warning.set_tooltip_text("\n".join(item.message for item in findings))
            warning.add_css_class("warning")
            self.widget.add_prefix(warning)

        self.scripted = read_only(kind, entity)
        if self.scripted:
            badge = Gtk.Label(label="From user.lua", css_classes=["dim-label", "caption"])
            badge.set_tooltip_text(
                "This runs Lua the app did not write, so it is listed but not edited."
            )
            self.widget.add_suffix(badge)

        if editable and not self.scripted:
            edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
            edit.add_css_class("flat")
            edit.set_tooltip_text("Edit")
            edit.connect("clicked", lambda _button: actions.edit(index))
            self.widget.add_suffix(edit)

        if editable:
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.set_tooltip_text("Remove")
            remove.connect("clicked", lambda _button: actions.remove(index))
            self.widget.add_suffix(remove)


class DeclarationsPage:
    """A Page listing one declarative Entity kind."""

    kind = "gestures"

    def __init__(self, session: Session, *, actions: DeclarationActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[DeclarationRow] = []
        self._filter_text = ""
        self._descriptor: DeclarationKind = BY_KIND[self.kind]

        self._page = Adw.PreferencesPage(title=self.title)

        self._filter = Gtk.SearchEntry(
            placeholder_text=f"Filter {self._descriptor.title.lower()}"
        )
        self._filter.connect("search-changed", self._on_filter_changed)
        filter_group = Adw.PreferencesGroup()
        filter_group.add(self._filter)
        self._page.add(filter_group)

        self._group = Adw.PreferencesGroup(
            title=self._descriptor.title,
            description=self._descriptor.description,
        )
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        add = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        add.add_css_class("flat")
        add.set_tooltip_text(f"Add a {self._descriptor.singular}")
        add.connect("clicked", lambda _button: self._actions.add())
        self._add_button = add
        header.append(add)
        self._group.set_header_suffix(header)
        self._page.add(self._group)

        # The caveat kinds say their caveat once, above the list, rather than on every row:
        # "needs a restart" is a property of the kind, and 20 identical badges would train
        # the eye to skip the place a real per-row warning appears.
        #
        # A dim label rather than an `Adw.Banner`, deliberately: ADR-0016 gives the window
        # exactly one Banner and reserves it for unhealthy state. This is neither -- these
        # Pages are working correctly and saying how -- and spending the Banner vocabulary
        # on a normal caveat is how "there is a Banner" stops meaning "something is wrong".
        self._note: Gtk.Label | None = None
        if self._descriptor.note:
            self._note = Gtk.Label(
                label=self._descriptor.note,
                wrap=True,
                xalign=0.0,
                css_classes=["dim-label", "caption"],
            )
            self._page.add(_note_group(self._note))

        self._listed: list[Gtk.Widget] = []
        self.refresh()

    # --- class-level identity ---------------------------------------------------------

    @property
    def section(self) -> str:
        return BY_KIND[self.kind].section

    @property
    def title(self) -> str:
        return BY_KIND[self.kind].title

    # --- widgets ----------------------------------------------------------------------

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def rows(self) -> tuple[DeclarationRow, ...]:
        """Every built Row, in list order. What the UI smoke tier asserts against."""
        return tuple(self._rows)

    @property
    def entities(self) -> list[Any]:
        return list(self._session.declarations(self.kind))

    @property
    def filter_entry(self) -> Gtk.SearchEntry:
        return self._filter

    def set_filter(self, text: str) -> None:
        """Programmatic filter -- applied now, not on the entry's debounced signal."""
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
        because that index is what an edit or a delete addresses.
        """
        for widget in self._listed:
            self._group.remove(widget)
        self._listed = []
        self._rows = []

        editable = bool(self._session.live)
        self._add_button.set_sensitive(editable)

        entities = self.entities
        by_subject = self._findings()
        shown = 0
        for index, entity in enumerate(entities):
            haystack = filter_haystack(self.kind, entity)
            if self._filter_text and self._filter_text not in haystack:
                continue
            row = DeclarationRow(
                entity,
                index,
                kind=self.kind,
                actions=self._actions,
                editable=editable,
                findings=by_subject.get(row_title(self.kind, entity), ()),
            )
            self._rows.append(row)
            self._group.add(row.widget)
            self._listed.append(row.widget)
            shown += 1

        if not shown:
            if entities:
                empty = Adw.ActionRow(
                    title="Nothing matches this filter",
                    subtitle="Clear the filter to see everything.",
                )
            else:
                empty = Adw.ActionRow(
                    title=f"No {self._descriptor.title.lower()} yet",
                    subtitle=self._descriptor.empty_hint,
                )
            self._group.add(empty)
            self._listed.append(empty)

    def _findings(self) -> dict[str, tuple[Finding, ...]]:
        """Everything wrong with this kind's entities right now, keyed by row title.

        Computed per refresh rather than stored on the entity: a dangling curve reference
        is a property of the *pair* of lists, so deleting a curve has to be able to light
        up a row on the Animations page that nobody touched.
        """
        entities = self._session.model.entities
        collected: list[Finding] = []
        if self.kind == "animations":
            collected += list(dangling_curve_references(entities))
            collected += list(missing_curve_references(entities))
            for animation in entities.animations:
                collected += list(animation_findings(animation))
        elif self.kind == "curves":
            for curve in entities.curves:
                collected += list(curve_findings(curve))
        elif self.kind == "devices":
            for device in entities.devices:
                collected += [
                    Finding(
                        device.name,
                        f"Hyprland has no per-device setting called “{key}”, "
                        f"so it will refuse this device.",
                    )
                    for key in unknown_device_fields(device)
                ]

        grouped: dict[str, list[Finding]] = {}
        for finding in collected:
            grouped.setdefault(finding.subject, []).append(finding)
        return {subject: tuple(items) for subject, items in grouped.items()}

    def curve_users(self, name: str) -> tuple[str, ...]:
        """The animation leaves that name this curve -- the delete confirmation's content."""
        return curve_usage(self._session.model.entities, name).leaves


def _note_group(label: Gtk.Label) -> Adw.PreferencesGroup:
    """The kind's caveat parked inside a group, because a Page takes only groups."""
    group = Adw.PreferencesGroup()
    group.add(label)
    return group


class AnimationsPage(DeclarationsPage):
    kind = "animations"


class CurvesPage(DeclarationsPage):
    kind = "curves"


class GesturesPage(DeclarationsPage):
    kind = "gestures"


class DevicesPage(DeclarationsPage):
    kind = "devices"


class EnvironmentPage(DeclarationsPage):
    kind = "env"


class AutostartPage(DeclarationsPage):
    kind = "startup"


class PermissionsPage(DeclarationsPage):
    kind = "permissions"


PAGES: tuple[type[DeclarationsPage], ...] = (
    AnimationsPage,
    CurvesPage,
    GesturesPage,
    DevicesPage,
    EnvironmentPage,
    AutostartPage,
    PermissionsPage,
)
"""Every declarative Page, in the order the sidebar lists them.

Animations before curves because that is the order a user meets them: you come looking for
"make windows open faster" and only then need a curve to do it with.
"""


__all__ = [
    "PAGES",
    "AnimationsPage",
    "AutostartPage",
    "CurvesPage",
    "DeclarationActions",
    "DeclarationRow",
    "DeclarationsPage",
    "DevicesPage",
    "EnvironmentPage",
    "GesturesPage",
    "PermissionsPage",
]
