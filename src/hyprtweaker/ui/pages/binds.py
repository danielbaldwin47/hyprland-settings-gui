"""The Binds Page: every keybind, in order, with the two doors that add one (ADR-0007).

An Entity Page rather than a Section Page. It shares `ConfigPage`'s shape -- a `page` to
put in the stack and a `refresh()` the window calls on every sync -- so the shell can hold
both kinds in one list without asking which it has.

**Everything is listed, including what the compositor cannot see.** `hyprctl binds` reports
`code:N` binds as `key:"", keycode:0`, so a list built from IPC would be missing exactly
the layout-independent number-row binds the corpus is full of. This list is built from the
model, which came from the file.

**Nothing is hidden for being uneditable.** A function-valued action lives in `user.lua`
and a multi-key `A&B` bind maps only approximately, so both are shown with their controls
insensitive and a badge saying why. Dropping them would be the app quietly claiming a
config is smaller than it is.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.dispatchers import EXEC_PATH, lookup  # noqa: E402
from hyprtweaker.engine.model.entities import Bind  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session

MULTI_KEY = "&"
"""The multi-key separator. Read-only: 0 uses in the corpus, and the mapping is approximate
(ADR-0007), so a capture UX for it would be effort spent on a case nobody has."""


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


def flag_text(bind: Bind) -> str:
    """The set flags, as the names the user will find in the file."""
    table = bind.options.as_table()
    return ", ".join(key for key, value in table.items() if value is True)


def is_read_only(bind: Bind) -> str:
    """Why this bind cannot be edited here, or `""` when it can be."""
    if bind.dispatcher is None:
        return "Defined by a Lua function in user.lua"
    if MULTI_KEY in bind.keys:
        return "Multi-key binds are edited as text"
    return ""


class BindRow:
    """One `Adw.ActionRow` for one Bind, plus what the Page needs to keep about it."""

    def __init__(
        self,
        bind: Bind,
        index: int,
        *,
        on_edit: Callable[[int], None],
        on_remove: Callable[[int], None],
        editable: bool,
    ) -> None:
        self.bind = bind
        self.index = index

        reason = is_read_only(bind)
        subtitle = action_text(bind)
        if flags := flag_text(bind):
            subtitle = f"{subtitle}\n{flags}"

        self.widget = Adw.ActionRow(
            title=trigger_text(bind),
            subtitle=subtitle,
            subtitle_lines=2,
        )
        if description := bind.options.description:
            self.widget.set_tooltip_text(description)

        if reason:
            badge = Gtk.Label(label="Read-only", css_classes=["dim-label", "caption"])
            badge.set_tooltip_text(reason)
            self.widget.add_suffix(badge)
            return

        if not editable:
            return

        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        edit.add_css_class("flat")
        edit.set_tooltip_text("Edit this bind")
        edit.connect("clicked", lambda _button: on_edit(index))
        self.widget.add_suffix(edit)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this bind")
        remove.connect("clicked", lambda _button: on_remove(index))
        self.widget.add_suffix(remove)


class BindsPage:
    """The Page listing every Bind, rebuilt whenever the model's bind list moves.

    Rebuilt wholesale rather than patched per row, for the reason identity is position: an
    insert or a reorder shifts every index after it, so a partial update would have to
    re-derive them all anyway, and a stale index is an edit landing on the wrong bind.
    """

    section = "binds"
    """The stack name. Matches the Section vocabulary the shell keys pages by."""

    title = "Keybinds"

    def __init__(
        self,
        session: Session,
        *,
        on_add: Callable[[], None],
        on_edit: Callable[[int], None],
        on_remove: Callable[[int], None],
    ) -> None:
        self._session = session
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_remove = on_remove
        self._rows: list[BindRow] = []

        self._page = Adw.PreferencesPage(title=self.title)
        self._group = Adw.PreferencesGroup(title="Keybinds")
        self._group.set_header_suffix(self._add_button())
        self._page.add(self._group)
        self._empty = Adw.ActionRow(
            title="No keybinds yet",
            subtitle="Add one with the button above, or import an existing config.",
        )
        self.refresh()

    def _add_button(self) -> Gtk.Widget:
        button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Add a keybind")
        button.connect("clicked", lambda _button: self._on_add())
        return button

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def rows(self) -> tuple[BindRow, ...]:
        """Every built Row, in list order. What the UI smoke tier asserts against."""
        return tuple(self._rows)

    @property
    def binds(self) -> list[Bind]:
        return list(self._session.model.entities.binds)

    def refresh(self) -> None:
        """Rebuild the list from the model."""
        for row in self._rows:
            self._group.remove(row.widget)
        if self._empty.get_parent() is not None:
            self._group.remove(self._empty)
        self._rows = []

        editable = bool(self._session.live)
        binds = self.binds
        if not binds:
            self._group.add(self._empty)
            return

        for index, bind in enumerate(binds):
            row = BindRow(
                bind,
                index,
                on_edit=self._on_edit,
                on_remove=self._on_remove,
                editable=editable,
            )
            self._rows.append(row)
            self._group.add(row.widget)
