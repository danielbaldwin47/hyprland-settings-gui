"""Save the current look as a Preset: a name and the Capture scopes to keep (ADR-0014, #171).

A scope is a checkbox row whose subtitle says how many settings it keeps, so "Fonts" is
never a guess. A scope that cannot be saved right now stays visible, insensitive, and its
subtitle says why (no wallpaper daemon; applying is off): the user learns the reason
instead of finding the row gone. Save waits for a name and at least one scope.

The dialog owns no saving: it hands `(name, scopes)` to `on_save`. The Presets group asks the
session, and when a name is taken or nothing can be captured it opens this dialog again with
the same answers and the reason in the body, so a refusal is never a dead end.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.presets import MAX_NAME, CaptureScope  # noqa: E402

HEADING = "Save current as preset"
BODY = "A preset keeps the look you choose, so you can switch back to it later."
NAME_HINT = "Name"
WALLPAPER_SUBTITLE = "Your wallpaper"


def size_text(count: int) -> str:
    """How many settings a scope keeps, in the words its subtitle uses."""
    return "1 setting" if count == 1 else f"{count} settings"


@dataclass(frozen=True, slots=True)
class ScopeChoice:
    """One row of the checklist: what it is, what it keeps, and whether it can be kept now."""

    scope: CaptureScope
    subtitle: str
    """"12 settings", or "Your wallpaper"; the reason instead when `available` is false."""
    available: bool = True


class SavePresetDialog(Adw.AlertDialog):
    """Name and scopes. `on_save(name, scopes)` runs on Save; Cancel and Escape do nothing."""

    def __init__(
        self,
        choices: Sequence[ScopeChoice],
        *,
        on_save: Callable[[str, frozenset[CaptureScope]], None],
        name: str = "",
        chosen: frozenset[CaptureScope] | None = None,
        problem: str | None = None,
    ) -> None:
        super().__init__()
        self.set_heading(HEADING)
        self.set_body(problem or BODY)
        self._on_save = on_save
        self.entry = Gtk.Entry(
            placeholder_text="Nord", activates_default=True, text=name, max_length=MAX_NAME
        )
        self.entry.update_property([Gtk.AccessibleProperty.LABEL], [NAME_HINT])
        self.entry.connect("changed", self._on_changed)
        self.checks: dict[CaptureScope, Gtk.CheckButton] = {}
        self.rows: dict[CaptureScope, Adw.ActionRow] = {}
        scopes = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        scopes.add_css_class("boxed-list")
        for choice in choices:
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            wanted = choice.scope in chosen if chosen is not None else True
            check.set_active(choice.available and wanted)
            check.set_sensitive(choice.available)
            check.connect("toggled", self._on_changed)
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(choice.scope.label)
            row.set_subtitle(choice.subtitle)
            row.add_prefix(check)
            row.set_activatable_widget(check)
            row.set_sensitive(choice.available)
            scopes.append(row)
            self.checks[choice.scope] = check
            self.rows[choice.scope] = row
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        column.append(self.entry)
        column.append(scopes)
        self.set_extra_child(column)
        self.add_response("cancel", "Cancel")
        self.add_response("save", "Save")
        self.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("save")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)
        self._on_changed()
        self.connect("map", self._on_map)

    @property
    def name(self) -> str:
        return self.entry.get_text().strip()

    @property
    def scopes(self) -> frozenset[CaptureScope]:
        return frozenset(scope for scope, check in self.checks.items() if check.get_active())

    def _on_changed(self, *_args: object) -> None:
        self.set_response_enabled("save", bool(self.name) and bool(self.scopes))

    def _on_map(self, *_args: object) -> None:
        self.entry.grab_focus()
        self.entry.select_region(0, -1)

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response == "save":
            self._on_save(self.name, self.scopes)
