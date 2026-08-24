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
from hyprtweaker.ui.flash import flash  # noqa: E402

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
    """What one conflicted Row shows: its fire order among its own submap, and its rivals.

    `order`/`total` count only the same-submap duplicates (self included), because that is
    the only sequence that exists: within one submap, list order is file order and all
    duplicates fire in it. A `submap_universal` bind also races same-trigger binds in
    *other* submaps, but those pairs never share a firing sequence -- the writer emits
    root binds before any submap block, so ranking them by list index would state an order
    the file does not have. A `total` of 1 means every rival is in another submap, and the
    badge says so instead of inventing a 1st-of-N.
    """

    order: int
    total: int
    rivals: tuple[Rival, ...]

    @property
    def ordered(self) -> bool:
        """Whether this row is part of a real firing sequence (same-submap duplicates)."""
        return self.total >= 2

    @property
    def badge_text(self) -> str:
        if self.ordered:
            return f"Duplicate trigger · fires {ordinal(self.order)} of {self.total}"
        return "Duplicate trigger in another submap"

    @property
    def short_text(self) -> str:
        """What the badge itself shows -- ADR-0007 wants fire order *on the row*."""
        return f"{ordinal(self.order)} of {self.total}" if self.ordered else "duplicate"


def rival_label(bind: Bind, order: int | None) -> str:
    """One rival as one line: fire order (when one exists), what it does, where it lives.

    `order` is `None` for a rival in another submap: the two never share a firing
    sequence, so a number would be a claim the file does not make.
    """
    place = "root keybinds" if bind.submap is None else f"submap {bind.submap}"
    prefix = f"{ordinal(order)}: " if order is not None else ""
    return f"{prefix}{action_text(bind)} ({place})"


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


@dataclass(frozen=True, slots=True)
class BindActions:
    """The verbs the window wires into the Page, bundled once.

    One object rather than seven callables riding every signature: they always travel
    together, and every index is into the model's flat bind list -- the only address an
    edit can safely use (identity is position, ADR-0007).
    """

    add: Callable[[str | None], None]
    """Open the add dialog; the argument is the owning submap (`None` = root)."""
    edit: Callable[[int], None]
    remove: Callable[[int], None]
    enable: Callable[[int, bool], None]
    rebind: Callable[[int], None]
    """Open Capture directly on the bind at this index (the conflict verb)."""
    swap: Callable[[int, int], None]
    """Exchange two binds' positions -- which same-submap duplicate fires first."""
    edit_submap: Callable[[str | None], None]
    """Open the Submap editor; `None` means create one."""


class BindRow:
    """One `Adw.ActionRow` for one Bind, plus what the Page needs to keep about it.

    The conflict badge is a `MenuButton` showing this row's fire order, whose popover
    carries the *other* bind's identity and the three verbs ADR-0007 demands -- jump to
    it, rebind it, disable it -- plus swap fire order for same-submap duplicates, which is
    the one place order is visible enough to be worth a control (#66). Never a bare
    "there is a conflict".

    Suffix order is pills first, then the conflict button, then action buttons -- the
    fixed-strip order ADR-0013 gives generated Option rows, kept here for consistency.
    """

    def __init__(
        self,
        bind: Bind,
        index: int,
        *,
        actions: BindActions,
        on_jump: Callable[[int], None],
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

        if not bind.enabled:
            self.disabled_badge = Gtk.Label(
                label="Disabled", css_classes=["dim-label", "caption"]
            )
            self.disabled_badge.set_tooltip_text(
                "Kept in place but commented out in binds.lua; it does not fire."
            )
            self.widget.add_suffix(self.disabled_badge)
            self.widget.add_css_class("dim-label")

        if reason:
            badge = Gtk.Label(label="Read-only", css_classes=["dim-label", "caption"])
            badge.set_tooltip_text(reason)
            self.widget.add_suffix(badge)

        # A read-only bind still fires, so it still conflicts -- the badge is not gated
        # on editability.
        if conflict is not None:
            self.conflict_badge = self._conflict_button(
                conflict, actions=actions, on_jump=on_jump, editable=editable
            )
            self.widget.add_suffix(self.conflict_badge)

        if reason or not editable:
            return

        if not bind.enabled:
            enable = Gtk.Button(label="Enable", valign=Gtk.Align.CENTER)
            enable.add_css_class("flat")
            enable.set_tooltip_text("Uncomment this bind so it fires again")
            enable.connect("clicked", lambda _button: actions.enable(index, True))
            self.widget.add_suffix(enable)

        edit = Gtk.Button(icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER)
        edit.add_css_class("flat")
        edit.set_tooltip_text("Edit this bind")
        edit.connect("clicked", lambda _button: actions.edit(index))
        self.widget.add_suffix(edit)

        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this bind")
        remove.connect("clicked", lambda _button: actions.remove(index))
        self.widget.add_suffix(remove)

    def _conflict_button(
        self,
        conflict: RowConflict,
        *,
        actions: BindActions,
        on_jump: Callable[[int], None],
        editable: bool,
    ) -> Gtk.MenuButton:
        badge = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badge.append(Gtk.Image(icon_name="dialog-warning-symbolic"))
        badge.append(Gtk.Label(label=conflict.short_text, css_classes=["caption"]))
        button = Gtk.MenuButton(
            child=badge,
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
            label=(
                "Duplicates are legal: every one of these fires, in the order listed."
                if conflict.ordered
                else "Duplicates are legal: each fires where its own submap is active."
            ),
            xalign=0,
            wrap=True,
            css_classes=["dim-label", "caption"],
        )
        note.set_max_width_chars(44)
        box.append(note)

        popover = Gtk.Popover()

        def act(callback: Callable[[], None]) -> Callable[[Gtk.Button], None]:
            def clicked(_button: Gtk.Button) -> None:
                popover.popdown()
                callback()

            return clicked

        def verb(label: str, tooltip: str, callback: Callable[[], None]) -> Gtk.Button:
            button = Gtk.Button(
                label=label,
                valign=Gtk.Align.CENTER,
                css_classes=["flat"],
                tooltip_text=tooltip,
            )
            button.connect("clicked", act(callback))
            return button

        for rival in conflict.rivals:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=rival.label, xalign=0, hexpand=True, wrap=True)
            label.set_max_width_chars(36)
            line.append(label)

            line.append(verb("Show", "Jump to this keybind", lambda r=rival: on_jump(r.index)))
            if editable:
                line.append(
                    verb(
                        "Rebind",
                        "Record a different trigger for that keybind",
                        lambda r=rival: actions.rebind(r.index),
                    )
                )
                line.append(
                    verb(
                        "Disable",
                        "Comment that keybind out so only this one fires",
                        lambda r=rival: actions.enable(r.index, False),
                    )
                )
                if rival.same_submap:
                    line.append(
                        verb(
                            "Swap order",
                            "Exchange which of the two fires first",
                            lambda r=rival: actions.swap(self.index, r.index),
                        )
                    )

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

    def __init__(self, session: Session, *, actions: BindActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[BindRow] = []

        self._page = Adw.PreferencesPage(title=self.title)
        self._groups: list[Adw.PreferencesGroup] = []
        self.refresh()

    def _header_button(
        self, icon: str, tooltip: str, editable: bool, on_click: Callable[[], None]
    ) -> Gtk.Widget:
        button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER)
        button.add_css_class("flat")
        button.set_tooltip_text(tooltip)
        button.set_sensitive(editable)
        button.connect("clicked", lambda _button: on_click())
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
        header.append(
            self._header_button(
                "list-add-symbolic",
                "Add a keybind",
                editable,
                lambda: self._actions.add(None),
            )
        )
        header.append(
            self._header_button(
                "folder-new-symbolic",
                "Add a submap",
                editable,
                lambda: self._actions.edit_submap(None),
            )
        )
        root.set_header_suffix(header)
        self._add_group(root)

        indexed = list(enumerate(binds))
        rooted = [(index, bind) for index, bind in indexed if bind.submap is None]
        if rooted:
            for index, bind in rooted:
                root.add(self._row(bind, index, editable, binds, conflicts))
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
            suffix.append(
                self._header_button(
                    "list-add-symbolic",
                    f"Add a keybind to {name}",
                    editable,
                    lambda submap=name: self._actions.add(submap),
                )
            )
            suffix.append(
                self._header_button(
                    "document-edit-symbolic",
                    "Rename this submap or change its reset target",
                    editable,
                    lambda submap=name: self._actions.edit_submap(submap),
                )
            )
            group.set_header_suffix(suffix)
            self._add_group(group)

            owned = [(index, bind) for index, bind in indexed if bind.submap == name]
            for index, bind in owned:
                group.add(self._row(bind, index, editable, binds, conflicts))
            if not owned:
                group.add(
                    Adw.ActionRow(
                        title="No keybinds in this submap yet",
                        subtitle="Add one with the button above.",
                    )
                )

    def reveal(self, index: int) -> None:
        """Bring the Row for the bind at `index` into view -- the conflict jump.

        Navigate + flash (ADR-0007): grabbing focus makes every ancestor scroll the row
        into view, and a short background pulse marks which row that was for a reader
        whose eyes were on the popover, not the focus ring.
        """
        for row in self._rows:
            if row.index == index:
                row.widget.grab_focus()
                flash(row.widget)
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
        binds: list[Bind],
        conflicts: dict[int, tuple[int, ...]],
    ) -> Gtk.Widget:
        conflict: RowConflict | None = None
        if index in conflicts:
            group = conflicts[index]
            # Fire order exists only among same-submap duplicates: within one submap,
            # list order is file order. A cross-submap rival (the submap_universal case)
            # is listed without a number -- see RowConflict.
            peers = [other for other in group if binds[other].submap == bind.submap]
            peer_order = {other: place + 1 for place, other in enumerate(peers)}
            conflict = RowConflict(
                order=peer_order[index],
                total=len(peers),
                rivals=tuple(
                    Rival(
                        index=other,
                        label=rival_label(binds[other], peer_order.get(other)),
                        same_submap=other in peer_order,
                    )
                    for other in group
                    if other != index
                ),
            )

        row = BindRow(
            bind,
            index,
            actions=self._actions,
            on_jump=self.reveal,
            editable=editable,
            conflict=conflict,
        )
        self._rows.append(row)
        return row.widget
