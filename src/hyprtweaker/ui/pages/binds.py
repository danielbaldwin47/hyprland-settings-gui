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
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.binds_analysis import (  # noqa: E402
    find_conflicts,
    submap_names,
    unreachable_submaps,
)
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
    """The set flags, as the names the user will find in the file.

    `device` is spelled out rather than listed as a bare flag name: ADR-0007 puts per-device
    binds on the row, and "device" alone would say a bind is restricted without saying to
    what -- which is the part that tells a user why their key does nothing on one keyboard.
    """
    table = bind.options.as_table()
    names = [key for key, value in table.items() if value is True]
    if bind.options.device is not None:
        device = bind.options.device
        listed = ", ".join(device.names) or "no devices"
        names.append(f"{'only on' if device.inclusive else 'not on'} {listed}")
    return ", ".join(names)


UNREACHABLE = "Nothing switches to this submap, so its keybinds can never fire."
"""The unreachable flag's sentence (ADR-0007). Appended to the group description rather
than hidden in a tooltip: the person most likely to hit this just made the submap and has
not yet bound a key to enter it, and a sentence in place is the difference between a
puzzle and a to-do."""


def ordinal(number: int) -> str:
    """1st, 2nd, 3rd... -- the fire-order spelling the conflict badge uses."""
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


@dataclass(frozen=True, slots=True)
class Rival:
    """One other Bind on the same Trigger, as the conflict popover presents it."""

    index: int
    label: str
    same_submap: bool


@dataclass(frozen=True, slots=True)
class RowConflict:
    """What one conflicted Row shows: its own fire order, and everyone it races."""

    order: int
    total: int
    rivals: tuple[Rival, ...]

    @property
    def badge_text(self) -> str:
        return f"Duplicate trigger · fires {ordinal(self.order)} of {self.total}"


def rival_label(bind: Bind, order: int) -> str:
    """One rival as one line: fire order, what it does, where it lives."""
    place = "root keybinds" if bind.submap is None else f"submap {bind.submap}"
    return f"{ordinal(order)}: {action_text(bind)} ({place})"


def read_only_reason(bind: Bind) -> str:
    """Why this bind cannot be edited here, or `""` when it can be.

    A reason rather than a bool: the row shows it on the badge, and "read-only" with no
    explanation is the kind of dead end that sends a user looking for a bug.
    """
    if bind.dispatcher is None:
        return "Defined by a Lua function in user.lua"
    if MULTI_KEY in bind.keys:
        return "Multi-key binds are edited as text"
    return ""


class BindRow:
    """One `Adw.ActionRow` for one Bind, plus what the Page needs to keep about it.

    The conflict badge is a `MenuButton` whose popover carries the *other* bind's identity
    and the three verbs ADR-0007 demands -- jump to it, rebind it, disable it -- plus swap
    fire order for same-submap duplicates, which is the one place order is visible enough
    to be worth a control (#66). Never a bare "there is a conflict".
    """

    def __init__(
        self,
        bind: Bind,
        index: int,
        *,
        on_edit: Callable[[int], None],
        on_remove: Callable[[int], None],
        on_enable: Callable[[int, bool], None],
        on_jump: Callable[[int], None],
        on_rebind: Callable[[int], None],
        on_swap: Callable[[int, int], None],
        editable: bool,
        conflict: RowConflict | None = None,
    ) -> None:
        self.bind = bind
        self.index = index
        self.conflict = conflict
        self.conflict_badge: Gtk.MenuButton | None = None
        self.disabled_badge: Gtk.Label | None = None

        reason = read_only_reason(bind)

        # The description is what the user named this bind, so it is the line they will scan
        # for -- shown, not hidden in a tooltip. The call itself stays visible underneath:
        # a description can be stale or wrong, and the action is the truth.
        lines = [action_text(bind)]
        if flags := flag_text(bind):
            lines.append(flags)

        self.widget = Adw.ActionRow(
            title=trigger_text(bind),
            subtitle="\n".join(lines),
            subtitle_lines=len(lines),
        )
        if description := bind.options.description:
            label = Gtk.Label(label=description, css_classes=["dim-label"], wrap=True)
            label.set_max_width_chars(28)
            self.widget.add_suffix(label)

        # A read-only bind still fires, so it still conflicts; the badge goes on before
        # the read-only early-return for exactly that reason.
        if conflict is not None:
            self.conflict_badge = self._conflict_button(
                conflict,
                on_jump=on_jump,
                on_rebind=on_rebind,
                on_swap=on_swap,
                on_enable=on_enable,
                editable=editable,
            )
            self.widget.add_suffix(self.conflict_badge)

        if not bind.enabled:
            self.disabled_badge = Gtk.Label(
                label="Disabled", css_classes=["dim-label", "caption"]
            )
            self.disabled_badge.set_tooltip_text(
                "Kept in place but commented out in binds.lua; it does not fire."
            )
            self.widget.add_suffix(self.disabled_badge)
            self.widget.add_css_class("dim-label")
            if editable and not reason:
                enable = Gtk.Button(label="Enable", valign=Gtk.Align.CENTER)
                enable.add_css_class("flat")
                enable.set_tooltip_text("Uncomment this bind so it fires again")
                enable.connect("clicked", lambda _button: on_enable(index, True))
                self.widget.add_suffix(enable)

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

    def _conflict_button(
        self,
        conflict: RowConflict,
        *,
        on_jump: Callable[[int], None],
        on_rebind: Callable[[int], None],
        on_swap: Callable[[int, int], None],
        on_enable: Callable[[int, bool], None],
        editable: bool,
    ) -> Gtk.MenuButton:
        button = Gtk.MenuButton(
            icon_name="dialog-warning-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "warning"],
            tooltip_text=conflict.badge_text,
        )

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6,
        )
        heading = Gtk.Label(label=conflict.badge_text, xalign=0)
        heading.add_css_class("heading")
        box.append(heading)
        note = Gtk.Label(
            label="Duplicates are legal: every one of these fires, in the order listed.",
            xalign=0,
            wrap=True,
            css_classes=["dim-label", "caption"],
        )
        note.set_max_width_chars(44)
        box.append(note)

        popover = Gtk.Popover()

        for rival in conflict.rivals:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=rival.label, xalign=0, hexpand=True, wrap=True)
            label.set_max_width_chars(36)
            line.append(label)

            def act(
                callback: Callable[[], None], pop: Gtk.Popover = popover
            ) -> Callable[[Gtk.Button], None]:
                def clicked(_button: Gtk.Button) -> None:
                    pop.popdown()
                    callback()

                return clicked

            show = Gtk.Button(label="Show", valign=Gtk.Align.CENTER, css_classes=["flat"])
            show.set_tooltip_text("Jump to this keybind")
            show.connect("clicked", act(lambda r=rival: on_jump(r.index)))
            line.append(show)

            if editable:
                rebind = Gtk.Button(
                    label="Rebind", valign=Gtk.Align.CENTER, css_classes=["flat"]
                )
                rebind.set_tooltip_text("Record a different trigger for that keybind")
                rebind.connect("clicked", act(lambda r=rival: on_rebind(r.index)))
                line.append(rebind)

                disable = Gtk.Button(
                    label="Disable", valign=Gtk.Align.CENTER, css_classes=["flat"]
                )
                disable.set_tooltip_text("Comment that keybind out so only this one fires")
                disable.connect("clicked", act(lambda r=rival: on_enable(r.index, False)))
                line.append(disable)

                if rival.same_submap:
                    swap = Gtk.Button(
                        label="Swap order", valign=Gtk.Align.CENTER, css_classes=["flat"]
                    )
                    swap.set_tooltip_text("Exchange which of the two fires first")
                    swap.connect("clicked", act(lambda r=rival: on_swap(self.index, r.index)))
                    line.append(swap)

            box.append(line)

        popover.set_child(box)
        button.set_popover(popover)
        return button


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
        on_add: Callable[[str | None], None],
        on_edit: Callable[[int], None],
        on_remove: Callable[[int], None],
        on_enable: Callable[[int, bool], None],
        on_rebind: Callable[[int], None],
        on_swap: Callable[[int, int], None],
        on_edit_submap: Callable[[str | None], None],
    ) -> None:
        self._session = session
        self._on_add = on_add
        self._on_edit = on_edit
        self._on_remove = on_remove
        self._on_enable = on_enable
        self._on_rebind = on_rebind
        self._on_swap = on_swap
        self._on_edit_submap = on_edit_submap
        self._rows: list[BindRow] = []

        self._page = Adw.PreferencesPage(title=self.title)
        self._groups: list[Adw.PreferencesGroup] = []
        self.refresh()

    def _add_button(self, submap: str | None, editable: bool) -> Gtk.Widget:
        button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text(
            "Add a keybind" if submap is None else f"Add a keybind to {submap}"
        )
        button.set_sensitive(editable)
        button.connect("clicked", lambda _button: self._on_add(submap))
        return button

    def _edit_submap_button(self, name: str, editable: bool) -> Gtk.Widget:
        button = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Rename this submap or change its reset target")
        button.set_sensitive(editable)
        button.connect("clicked", lambda _button: self._on_edit_submap(name))
        return button

    def _new_submap_button(self, editable: bool) -> Gtk.Widget:
        button = Gtk.Button(icon_name="folder-new-symbolic", valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text("Add a submap")
        button.set_sensitive(editable)
        button.connect("clicked", lambda _button: self._on_edit_submap(None))
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
        """Rebuild the list from the model: root binds, then one group per Submap.

        The grouping is ADR-0007's Placement, and it is not decoration. Identity is
        position and duplicates fire in order, but they only race *within* one submap -- so
        a flat list would put two binds on the same trigger side by side and imply a
        conflict that does not exist, while hiding the ones that do.

        Rows keep their index into the model's flat list, not into the group, because that
        index is what an edit or a delete addresses.

        Submap groups come from the model's declarations *and* the binds (#66): a submap
        the user just created has no binds yet, and a group is the only place its rename
        and reset-target controls can live.
        """
        for group in self._groups:
            self._page.remove(group)
        self._groups = []
        self._rows = []

        editable = bool(self._session.live)
        entities = self._session.model.entities
        binds = self.binds
        conflicts = find_conflicts(binds)
        unreachable = unreachable_submaps(entities)

        root = Adw.PreferencesGroup(title="Keybinds")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        header.append(self._add_button(None, editable))
        header.append(self._new_submap_button(editable))
        root.set_header_suffix(header)
        self._add_group(root)

        indexed = list(enumerate(binds))
        rooted = [(index, bind) for index, bind in indexed if bind.submap is None]
        if rooted:
            for index, bind in rooted:
                root.add(self._row(bind, index, editable, conflicts))
        else:
            root.add(
                Adw.ActionRow(
                    title="No keybinds yet",
                    subtitle="Add one with the button above, or import an existing config.",
                )
            )

        for name in submap_names(entities):
            description = "These keybinds only fire while this submap is active."
            if name in unreachable:
                description += f" {UNREACHABLE}"
            group = Adw.PreferencesGroup(title=f"Submap: {name}", description=description)
            suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            suffix.append(self._add_button(name, editable))
            suffix.append(self._edit_submap_button(name, editable))
            group.set_header_suffix(suffix)
            self._add_group(group)

            owned = [(index, bind) for index, bind in indexed if bind.submap == name]
            for index, bind in owned:
                group.add(self._row(bind, index, editable, conflicts))
            if not owned:
                group.add(
                    Adw.ActionRow(
                        title="No keybinds in this submap yet",
                        subtitle="Add one with the button above.",
                    )
                )

    def reveal(self, index: int) -> None:
        """Bring the Row for the bind at `index` into view -- the conflict jump.

        Focus, not selection: grabbing focus makes every ancestor scroll the row into
        view, which is all "jump to it in context" needs (ADR-0007).
        """
        for row in self._rows:
            if row.index == index:
                row.widget.grab_focus()
                return

    @property
    def groups(self) -> tuple[Adw.PreferencesGroup, ...]:
        """Every built group, root first. What the UI smoke tier asserts against."""
        return tuple(self._groups)

    def _add_group(self, group: Adw.PreferencesGroup) -> None:
        self._groups.append(group)
        self._page.add(group)

    def _row(
        self,
        bind: Bind,
        index: int,
        editable: bool,
        conflicts: dict[int, tuple[int, ...]],
    ) -> Gtk.Widget:
        conflict: RowConflict | None = None
        if index in conflicts:
            group = conflicts[index]
            binds = self.binds
            conflict = RowConflict(
                order=group.index(index) + 1,
                total=len(group),
                rivals=tuple(
                    Rival(
                        index=other,
                        label=rival_label(binds[other], group.index(other) + 1),
                        same_submap=binds[other].submap == bind.submap,
                    )
                    for other in group
                    if other != index
                ),
            )

        row = BindRow(
            bind,
            index,
            on_edit=self._on_edit,
            on_remove=self._on_remove,
            on_enable=self._on_enable,
            on_jump=self.reveal,
            on_rebind=self._on_rebind,
            on_swap=self._on_swap,
            editable=editable,
            conflict=conflict,
        )
        self._rows.append(row)
        return row.widget
