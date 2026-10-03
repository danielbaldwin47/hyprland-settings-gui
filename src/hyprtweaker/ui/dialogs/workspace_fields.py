"""The field rows of the workspace rule editor (#160).

A rule's fields are what Hyprland applies to the workspaces its selector matches. The
rows here are generated from `engine/workspace_catalog`: a switch, a spin, an entry, a
gap control (`GapField`, #193) or the layout combo for each catalog field the rule holds,
a free key/value table for `layout_opts`, and a raw key/value row for every other key.

**Present means set.** Only the fields a rule holds have a row; "Add a setting" offers the
rest. A row that is absent writes no key, so "not set" is never an explicit default in the
file (the same reading S4 gives the Monitors rows). A newly added field starts at the
value its user most likely came for (`WorkspaceField.starts_at`) and is written as shown.

**Untouched means original.** Every row holds the value it opened with and returns that
very object until the user changes it, so reading a rule and saving it back changes
nothing: `1.0` stays `1.0`, a gap table with a missing side stays as it was, a
`layout_opts` value Lua held as a number stays a number. A held value a typed row cannot
show (a string where a gap belongs, say) gets a raw text row instead of being dropped or
coerced; and with no row touched, `collect()` returns the rule's own mapping.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from hyprtweaker.engine.entities_catalog import field_text  # noqa: E402
from hyprtweaker.engine.scripting import layout_label  # noqa: E402
from hyprtweaker.engine.workspace_catalog import (  # noqa: E402
    BUILTIN_LAYOUTS,
    LAYOUT_OPTS,
    SHELVES,
    WORKSPACE_FIELDS,
    WorkspaceField,
    WorkspaceFieldType,
    find_field,
    retype_like,
)
from hyprtweaker.ui.release import release  # noqa: E402
from hyprtweaker.ui.rows.gap_field import GapField, gap_row  # noqa: E402

_MISSING: Any = object()
"""No value: a row that holds none writes no key."""

_ADD_PLACEHOLDER = "Add a setting…"


@dataclass(slots=True)
class _Row:
    """One row's bookkeeping: its key, the widget, the value it opened with and how to
    read what it shows now. `touched` flips on the user's first edit."""

    key: str
    widget: Gtk.Widget
    original: Any
    read: Callable[[], Any]
    shelf: str = ""
    """The group the row sits in: a catalog shelf, or `""` for a raw row (Other settings)."""
    touched: bool = False

    def value(self) -> Any:
        if not self.touched and self.original is not _MISSING:
            return self.original
        return self.read()


class WorkspaceFieldRows:
    """The groups of rows for one rule's fields, and the collection of what they say."""

    def __init__(
        self,
        fields: Mapping[str, Any],
        *,
        layouts: Sequence[str] = BUILTIN_LAYOUTS,
    ) -> None:
        self._fields = fields
        self._layouts = tuple(layouts)
        self._dirty = False
        self._order: list[str] = []
        self._rows: dict[str, _Row] = {}
        self._opts_rows: dict[str, _Row] = {}
        self._gaps: dict[str, GapField] = {}
        self._entries: dict[str, Gtk.Entry] = {}
        self._opts_original: Any = fields.get(LAYOUT_OPTS, _MISSING)
        self._opts_touched = False

        self._picker_group = Adw.PreferencesGroup(
            title="Settings",
            description=(
                "Pick a setting to give this rule. Anything not listed is left to "
                "Hyprland's defaults."
            ),
        )
        self._shelves = {shelf: Adw.PreferencesGroup(title=shelf) for shelf in SHELVES}
        self._shelf_rows: dict[str, list[Gtk.Widget]] = {shelf: [] for shelf in SHELVES}
        self._opts_group = Adw.PreferencesGroup(
            title="Layout options",
            description=(
                "Options that belong to a layout, such as orientation for master. "
                "Hyprland reads every value as text."
            ),
        )
        self._other_group = Adw.PreferencesGroup(
            title="Other settings",
            description=(
                "Settings this editor has no row for. They are kept as they are, and "
                "Hyprland reports any it does not know."
            ),
        )
        self._other_rows: list[Gtk.Widget] = []
        self._opts_widgets: list[Gtk.Widget] = []

        self._offered: tuple[WorkspaceField, ...] = ()
        self._picking = False
        self._picker = Adw.ComboRow(
            title="Add a setting", model=Gtk.StringList.new([_ADD_PLACEHOLDER]), selected=0
        )
        self._picker.connect("notify::selected", self._on_picked)
        self._picker_group.add(self._picker)
        self._opts_add = Adw.EntryRow(title="Add a layout option", show_apply_button=True)
        self._opts_add.connect("apply", lambda _row: self._on_add_option())
        self._opts_group.add(self._opts_add)

        for key, value in fields.items():
            self._open(key, value)
        self._rebuild_picker()
        self._refresh_visibility()

    # --- what the dialog and tests read ---------------------------------------------------

    @property
    def groups(self) -> list[Adw.PreferencesGroup]:
        """The groups in display order, for the dialog to add."""
        return [
            self._picker_group,
            *(self._shelves[shelf] for shelf in SHELVES),
            self._opts_group,
            self._other_group,
        ]

    @property
    def picker(self) -> Adw.ComboRow:
        return self._picker

    @property
    def option_entry(self) -> Adw.EntryRow:
        """The "Add an option" entry of the `layout_opts` table."""
        return self._opts_add

    def row(self, key: str) -> Gtk.Widget | None:
        """The widget for a field key, typed or raw."""
        row = self._rows.get(key)
        return row.widget if row is not None else None

    def gap_field(self, key: str) -> GapField | None:
        """The gap control of a gap row, for tests and probes."""
        return self._gaps.get(key)

    def text_entry(self, key: str) -> Gtk.Entry | None:
        """The entry of a typed text row (Monitor, Display name), for tests and probes."""
        return self._entries.get(key) if key in self._rows else None

    def option_row(self, key: str) -> Adw.EntryRow | None:
        row = self._opts_rows.get(key)
        return row.widget if row is not None else None  # type: ignore[return-value]

    def remove_row(self, key: str) -> None:
        """Remove a field row, as its trash button does."""
        row = self._rows.get(key)
        if row is not None:
            self._remove(row)

    def remove_option(self, key: str) -> None:
        """Remove a `layout_opts` entry, as its trash button does."""
        row = self._opts_rows.get(key)
        if row is not None:
            self._remove_option(row)

    def add(self, key: str) -> None:
        """Add the catalog field `key` as a new row, at its opening value."""
        spec = find_field(key)
        if spec is not None and key not in self._rows:
            self._order.append(key)
            self._attach(self._build_typed(spec, _MISSING), spec.shelf)
            self._dirty = True
            self._rebuild_picker()
            self._refresh_visibility()

    def add_option(self, key: str) -> None:
        """Add a `layout_opts` entry named `key`, empty, or move to the one that exists."""
        key = key.strip()
        if not key:
            return
        existing = self._opts_rows.get(key)
        if existing is not None:
            existing.widget.grab_focus()
            return
        if LAYOUT_OPTS not in self._order:
            self._order.append(LAYOUT_OPTS)
        self._add_option_row(key, _MISSING)
        self._opts_touched = True
        self._dirty = True
        self._opts_rows[key].widget.grab_focus()

    def collect(self) -> Mapping[str, Any]:
        """The fields a save stores. The rule's own mapping while nothing changed."""
        if not self._dirty:
            return self._fields
        collected: dict[str, Any] = {}
        for key in self._order:
            held = self._rows.get(key)
            value = held.value() if held is not None else self._collect_options()
            if value is not _MISSING:
                collected[key] = value
        return collected

    # --- opening a held key ---------------------------------------------------------------

    def _open(self, key: str, value: Any) -> None:
        self._order.append(key)
        if key == LAYOUT_OPTS and isinstance(value, Mapping):
            for name, option in value.items():
                self._add_option_row(str(name), option)
            return
        # A layout_opts that is not a table cannot be a key/value table: it gets a raw row.
        spec = find_field(key)
        row = self._build_typed(spec, value) if spec and _fits(spec, value) else None
        if row is None:
            row = self._build_raw(key, value)
            self._other_rows.append(row.widget)
            self._other_group.add(row.widget)
            self._rows[key] = row
            return
        self._attach(row, spec.shelf if spec else "")

    def _attach(self, row: _Row, shelf: str) -> None:
        row.shelf = shelf
        self._rows[row.key] = row
        self._shelf_rows[shelf].append(row.widget)
        self._shelves[shelf].add(row.widget)

    # --- typed rows -----------------------------------------------------------------------

    def _touch(self, row: _Row | None = None) -> None:
        self._dirty = True
        if row is not None:
            row.touched = True

    def _build_typed(self, spec: WorkspaceField, value: Any) -> _Row:
        held = value is not _MISSING
        opening = value if held else spec.starts_at
        kind = spec.type
        remove = _trash(f"Remove {spec.title.lower()}")
        row: _Row

        if kind is WorkspaceFieldType.GAPS:
            field = GapField(opening, on_commit=lambda _gaps: self._touch(row))
            self._gaps[spec.name] = field
            widget = gap_row(spec.title, field, subtitle=spec.help or None, suffix=remove)
            row = _Row(spec.name, widget, value, lambda: field.value)
        elif kind is WorkspaceFieldType.BOOL:
            switch = Adw.SwitchRow(title=spec.title, subtitle=spec.help, active=bool(opening))
            switch.connect("notify::active", lambda *_: self._touch(row))
            switch.add_suffix(remove)
            row = _Row(spec.name, switch, value, switch.get_active)
        elif kind is WorkspaceFieldType.INT:
            number = Gtk.Adjustment(
                value=int(opening),
                lower=min(spec.minimum, int(opening)),
                upper=max(spec.maximum, int(opening)),
                step_increment=1,
                page_increment=5,
            )
            spin = Adw.SpinRow(title=spec.title, subtitle=spec.help, adjustment=number)
            spin.connect("notify::value", lambda *_: self._touch(row))
            spin.add_suffix(remove)
            row = _Row(spec.name, spin, value, lambda: int(number.get_value()))
        elif kind is WorkspaceFieldType.LAYOUT:
            # A held layout the compositor does not list joins the choices: opening a rule
            # must not quietly change its layout to the first one in the list.
            choices = (*self._layouts, *([opening] if opening not in self._layouts else []))
            # Spelled as the Layout row spells them: every Lua layout offered here is one
            # the user's files register, so only the held one may read "(not found)".
            labels = [layout_label(each, found=each in self._layouts) for each in choices]
            combo = Adw.ComboRow(
                title=spec.title,
                model=Gtk.StringList.new(labels),
                selected=choices.index(opening),
            )
            combo.connect("notify::selected", lambda *_: self._touch(row))
            combo.add_suffix(remove)
            row = _Row(
                spec.name,
                combo,
                value,
                lambda: choices[min(combo.get_selected(), len(choices) - 1)],
            )
        else:
            # ADR-0013 §2: an ActionRow with an entry suffix, so the help stays on screen
            # as the subtitle, where an EntryRow could only hide it in a tooltip.
            entry = Gtk.Entry(text=str(opening), valign=Gtk.Align.CENTER, hexpand=True)
            entry.connect("changed", lambda *_: self._touch(row))
            self._entries[spec.name] = entry
            text_row = Adw.ActionRow(title=spec.title, subtitle=spec.help)
            text_row.add_suffix(entry)
            text_row.add_suffix(remove)
            row = _Row(
                spec.name, text_row, value, _blank_is_missing(lambda: entry.get_text().strip())
            )

        remove.connect("clicked", lambda _b: self._remove(row))
        return row

    def _remove(self, row: _Row) -> None:
        if row.shelf:
            self._shelves[row.shelf].remove(row.widget)
            self._shelf_rows[row.shelf].remove(row.widget)
        else:
            self._other_group.remove(row.widget)
            self._other_rows.remove(row.widget)
        # From an idle: this runs inside the row's own trash button's handler, and the
        # button's closure holds this object, a cycle `release` cuts.
        GLib.idle_add(release, row.widget)
        del self._rows[row.key]
        self._gaps.pop(row.key, None)
        self._order.remove(row.key)
        self._dirty = True
        self._rebuild_picker()
        self._refresh_visibility()

    # --- raw rows and the layout_opts table -----------------------------------------------

    def _build_raw(self, key: str, value: Any) -> _Row:
        widget = Adw.EntryRow(use_markup=False, text=field_text(value))
        widget.set_title(key)  # the key is the user's text: never markup (F7)
        scalar = value is None or isinstance(value, str | int | float | bool)
        if not scalar:
            widget.set_editable(False)
            widget.set_tooltip_text("This value is a table or list: it is kept as it is.")
        row = _Row(key, widget, value, lambda: retype_like(value, widget.get_text()))
        widget.connect("changed", lambda *_: self._touch(row))
        button = _trash(f"Remove {key}")
        button.connect("clicked", lambda _b: self._remove(row))
        widget.add_suffix(button)
        return row

    def _add_option_row(self, key: str, value: Any) -> None:
        widget = Adw.EntryRow(
            use_markup=False, text="" if value is _MISSING else field_text(value)
        )
        widget.set_title(key)  # the key is the user's text: never markup (F7)
        row = _Row(key, widget, value, lambda: widget.get_text().strip())
        widget.connect("changed", lambda *_: self._touch_option(row))
        button = _trash(f"Remove {key}")
        button.connect("clicked", lambda _b: self._remove_option(row))
        widget.add_suffix(button)
        self._opts_rows[key] = row
        self._opts_group.remove(self._opts_add)
        self._opts_group.add(widget)
        self._opts_group.add(self._opts_add)
        self._opts_widgets.append(widget)

    def _touch_option(self, row: _Row) -> None:
        self._opts_touched = True
        self._touch(row)

    def _remove_option(self, row: _Row) -> None:
        self._opts_group.remove(row.widget)
        self._opts_widgets.remove(row.widget)
        GLib.idle_add(release, row.widget)
        del self._opts_rows[row.key]
        self._opts_touched = True
        self._dirty = True

    def _on_add_option(self) -> None:
        name = self._opts_add.get_text()
        self._opts_add.set_text("")
        self.add_option(name)

    def _collect_options(self) -> Any:
        if not self._opts_touched and self._opts_original is not _MISSING:
            return self._opts_original
        collected = {}
        for key, row in self._opts_rows.items():
            value = row.value()
            if value != "":
                collected[key] = value
        return collected if collected else _MISSING

    # --- the picker and group visibility --------------------------------------------------

    def _rebuild_picker(self) -> None:
        """Offer the catalog fields no row holds. Updates the combo in place: replacing it
        from inside its own `notify::selected` would destroy it mid-signal."""
        self._offered = tuple(spec for spec in WORKSPACE_FIELDS if spec.name not in self._rows)
        titles = [_ADD_PLACEHOLDER, *(spec.title for spec in self._offered)]
        self._picking = True
        try:
            model = self._picker.get_model()
            model.splice(0, model.get_n_items(), titles)
            self._picker.set_selected(0)
        finally:
            self._picking = False
        self._picker.set_visible(bool(self._offered))

    def _on_picked(self, row: Adw.ComboRow, _param: Any) -> None:
        index = row.get_selected()
        if self._picking or index <= 0 or index > len(self._offered):
            return
        self.add(self._offered[index - 1].name)

    def _refresh_visibility(self) -> None:
        for shelf, group in self._shelves.items():
            group.set_visible(bool(self._shelf_rows[shelf]))
        self._other_group.set_visible(bool(self._other_rows))


def _fits(spec: WorkspaceField, value: Any) -> bool:
    """Whether a held value is one the field's typed row can show without changing it."""
    kind = spec.type
    if kind is WorkspaceFieldType.BOOL:
        return isinstance(value, bool)
    if kind is WorkspaceFieldType.INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is WorkspaceFieldType.GAPS:
        return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(
            value, Mapping
        )
    return isinstance(value, str)


def _blank_is_missing(read: Callable[[], Any]) -> Callable[[], Any]:
    """A text field left blank writes no key: `monitor = ""` says nothing Hyprland can use."""

    def blanked() -> Any:
        text = read()
        return text if text != "" else _MISSING

    return blanked


def _trash(tooltip: str) -> Gtk.Button:
    button = Gtk.Button(
        icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"]
    )
    button.set_tooltip_text(tooltip)
    return button
