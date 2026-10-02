"""The Binds Page: every keybind, in order, with the two doors that add one (ADR-0007).

An Entity Page rather than a Section Page. It shares `ConfigPage`'s shape -- a `page` to
put in the stack and a `refresh()` the window calls on every sync -- so the shell can hold
both kinds in one list without asking which it has.

**Everything is listed, including what the compositor cannot see.** `hyprctl binds` reports
`code:N` binds as `key:"", keycode:0`, so a list built from IPC would be missing exactly
the layout-independent number-row binds the corpus is full of. This list is built from the
model, which came from the file.

**Nothing is hidden for being uneditable.** A function-valued action lives in `user.lua`,
a multi-key `A&B` bind cannot load in Hyprland 0.56, and a dead-keysym bind was imported
commented out, so each is shown with a badge saying why (`BadgeKind`). Dropping them would
be the app quietly claiming a config is smaller than it is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from hyprtweaker.engine.binds_analysis import (  # noqa: E402
    empty_submaps,
    find_conflicts,
    submap_names,
    unreachable_submaps,
)
from hyprtweaker.engine.model.entities import Bind  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.entity_text import (  # noqa: E402
    EMPTY_SUBMAP,
    BadgeKind,
    action_text,
    bind_badge,
    trigger_text,
)
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

if TYPE_CHECKING:  # pragma: no cover - a cycle at runtime, a type here
    from hyprtweaker.session import Session


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
    recapture: Callable[[int], None]
    """Open Capture on an error-badged bind; a captured trigger also enables it."""
    swap: Callable[[int, int], None]
    """Exchange two binds' positions -- which same-submap duplicate fires first."""
    move: Callable[[int, int], None]
    """Move the bind at the first index to the second, in its group -- the drag reorder."""
    edit_submap: Callable[[str | None], None]
    """Open the Submap editor; `None` means create one."""


@dataclass(slots=True)
class BindDrag:
    """The one bind drag in flight on a Page: whose it is and which group it belongs to.

    The payload GTK carries is only the origin's index, and a drop target has to decide
    whether to light up *before* the drop delivers it. So the handle records the drag here
    when it starts, and every row of the Page reads it: a row of another group stays dark
    and refuses the drop, because `binds.lua` keeps no order between groups.
    """

    origin: int | None = None
    submap: str | None = None

    def start(self, row: BindRow) -> None:
        self.origin, self.submap = row.index, row.bind.submap

    def accepts(self, origin: int | None, target: BindRow) -> bool:
        """Whether a drop of the bind at `origin` on `target` moves anything."""
        return (
            origin is not None
            and origin == self.origin
            and origin != target.index
            and self.submap == target.bind.submap
        )


REORDER_HINT = "Drag to reorder within this group, or press Alt+Up or Alt+Down"


@dataclass(frozen=True, slots=True)
class RowVerb:
    """The button a badged row offers to turn its bind on, and the action it calls."""

    label: str
    tooltip: str
    run: Callable[[BindActions, int], None]


_VERBS = {
    BadgeKind.DISABLED: RowVerb(
        "Enable",
        "Uncomment this bind so it fires again",
        lambda actions, index: actions.enable(index, True),
    ),
    # Never a bare Enable (ADR-0007): as it stands this bind fails the whole config, so the
    # way back is a new trigger, and a captured one turns it on.
    BadgeKind.ERROR: RowVerb(
        "Fix trigger…",
        "Record a key Hyprland knows, then enable this bind with it",
        lambda actions, index: actions.recapture(index),
    ),
}


def row_verb(kind: BadgeKind) -> RowVerb | None:
    """The button a row with this badge offers to turn its bind on, if any."""
    return _VERBS.get(kind)


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
        drag: BindDrag | None = None,
        neighbours: tuple[int | None, int | None] = (None, None),
        empty_submaps: frozenset[str] = frozenset(),
    ) -> None:
        """`drag` is the Page's one `BindDrag`, shared by its rows; `neighbours` are the
        flat indices of the binds just above and below this one *in its group*, where the
        keyboard move goes. `empty_submaps` are the submaps no bind makes enterable."""
        self.bind = bind
        self.index = index
        self.drag_handle: Gtk.Image | None = None
        """The drag source, on rows the app may move: those whose badge offers Edit."""
        self.conflict = conflict
        self.conflict_badge: Gtk.MenuButton | None = None
        self.badge = bind_badge(bind, empty_submaps=empty_submaps)
        self.badge_label: Gtk.Label | None = None
        self.enable_button: Gtk.Button | None = None
        """Enable for a plain disabled bind; "Fix trigger…" (re-capture) for an error one."""
        self.edit_button: Gtk.Button | None = None
        self.remove_button: Gtk.Button | None = None

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
            # A trigger (`A&B`) or command (`a && b`) is text: as Pango markup it renders blank.
            use_markup=False,
        )
        if description := bind.options.description:
            label = Gtk.Label(label=description, css_classes=["dim-label"], wrap=True)
            label.set_max_width_chars(28)
            self.widget.add_suffix(label)

        badge = self.badge
        if badge is not None:
            self.badge_label = Gtk.Label(
                label=badge.text, css_classes=[badge.kind.style, "caption"]
            )
            self.badge_label.set_tooltip_text(badge.tooltip)
            self.widget.add_suffix(self.badge_label)
            if not bind.enabled and badge.kind.dims_row:
                self.widget.add_css_class("dim-label")

        # A read-only bind still fires, so it still conflicts -- the badge is not gated
        # on editability.
        if conflict is not None:
            self.conflict_badge = self._conflict_button(
                conflict, actions=actions, on_jump=on_jump, editable=editable
            )
            self.widget.add_suffix(self.conflict_badge)

        if not editable:
            return
        kind = badge.kind if badge is not None else None
        self._wire_reorder(
            actions, drag or BindDrag(), neighbours, movable=kind is None or kind.editable
        )

        if kind is not None and (verb := row_verb(kind)) is not None:
            self.enable_button = Gtk.Button(label=verb.label, valign=Gtk.Align.CENTER)
            self.enable_button.set_tooltip_text(verb.tooltip)
            self.enable_button.connect("clicked", lambda _button: verb.run(actions, index))
            self.enable_button.add_css_class("flat")
            self.widget.add_suffix(self.enable_button)

        if kind is None or kind.editable:
            self.edit_button = Gtk.Button(
                icon_name="document-edit-symbolic", valign=Gtk.Align.CENTER
            )
            self.edit_button.add_css_class("flat")
            self.edit_button.set_tooltip_text("Edit this bind")
            self.edit_button.connect("clicked", lambda _button: actions.edit(index))
            self.widget.add_suffix(self.edit_button)

        if kind is None or kind.removable:
            self.remove_button = Gtk.Button(
                icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER
            )
            self.remove_button.add_css_class("flat")
            self.remove_button.set_tooltip_text("Remove this bind")
            self.remove_button.connect("clicked", lambda _button: actions.remove(index))
            self.widget.add_suffix(self.remove_button)

    def _wire_reorder(
        self,
        actions: BindActions,
        drag: BindDrag,
        neighbours: tuple[int | None, int | None],
        *,
        movable: bool,
    ) -> None:
        """The handle and keys that move this bind, and the drop target every row is.

        Only a bind the app may edit gets a handle: a Lua-function or multi-key bind is
        otherwise untouchable here, so moving it would be the one change the row allows.
        Every row still takes drops, so other binds of its group can move past it. The
        handle is the drag *source* -- dragging anywhere else on the row would fight scrolling
        and button presses -- while the whole row is the target, for its full height.
        """
        target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        target.connect("enter", self._on_hover, drag)
        target.connect("motion", self._on_hover, drag)
        target.connect("drop", self._on_drop, drag, actions)
        self.widget.add_controller(target)

        handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        if not movable:
            # Holds the handle's width, so this row's trigger lines up with its neighbours'.
            handle.set_opacity(0)
            self.widget.add_prefix(handle)
            return

        self.drag_handle = handle
        self.drag_handle.add_css_class("dim-label")
        self.drag_handle.set_tooltip_text(REORDER_HINT)
        self.widget.add_prefix(self.drag_handle)
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, drag)
        self.drag_handle.add_controller(source)

        keys = Gtk.ShortcutController()
        for accelerator, neighbour in zip(("<Alt>Up", "<Alt>Down"), neighbours, strict=True):
            keys.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(accelerator),
                    Gtk.CallbackAction.new(self._on_step, neighbour, actions),
                )
            )
        self.widget.add_controller(keys)

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, _x: float, _y: float, drag: BindDrag
    ) -> Gdk.ContentProvider:
        drag.start(self)
        return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, self.index))

    def _on_hover(
        self, _target: Gtk.DropTarget, _x: float, _y: float, drag: BindDrag
    ) -> Gdk.DragAction:
        # No action means no drop highlight and no drop: the row says "not here" while the
        # pointer is still over it, rather than after the user lets go.
        if drag.accepts(drag.origin, self):
            return Gdk.DragAction.MOVE
        return Gdk.DragAction(0)

    def _on_drop(
        self,
        _target: Gtk.DropTarget,
        value: int,
        _x: float,
        _y: float,
        drag: BindDrag,
        actions: BindActions,
    ) -> bool:
        # Only the action: it refreshes the Page, which rebuilds every row, this one too.
        origin = int(value)
        if not drag.accepts(origin, self):
            return False
        actions.move(origin, self.index)
        return True

    def _on_step(
        self, widget: Gtk.Widget, _args: object, neighbour: int | None, actions: BindActions
    ) -> bool:
        if neighbour is None:
            widget.error_bell()  # already first (or last) in its group
            return True
        actions.move(self.index, neighbour)
        return True

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

    section = entity_page_id("binds")
    """The stack name, namespaced `entity:` because Hyprland also has a `binds` Section
    (see `DeclarationKind.section`, #120)."""

    title = "Keybinds"

    def __init__(self, session: Session, *, actions: BindActions) -> None:
        self._session = session
        self._actions = actions
        self._rows: list[BindRow] = []
        self._drag = BindDrag()

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
            release(group)
        self._groups = []
        self._rows = []

        editable = bool(self._session.live)
        entities = self._session.model.entities
        binds = self.binds
        conflicts = find_conflicts(binds)
        unreachable = unreachable_submaps(entities)
        empty = frozenset(empty_submaps(entities))

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
            for (index, bind), neighbours in zip(rooted, _neighbours(rooted), strict=True):
                root.add(self._row(bind, index, editable, binds, conflicts, neighbours, empty))
        else:
            root.add(
                Adw.ActionRow(
                    title="No keybinds yet",
                    subtitle="Add one with the button above, or import an existing config.",
                )
            )

        for name in submap_names(entities):
            description = "These keybinds only fire while this submap is active."
            if name in empty:
                description += f" {EMPTY_SUBMAP}"
            if name in unreachable:
                description += f" {UNREACHABLE}"
            # The title is Pango markup: a name with `&` would render blank unescaped.
            group = Adw.PreferencesGroup(
                title=f"Submap: {GLib.markup_escape_text(name)}", description=description
            )
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
            for (index, bind), neighbours in zip(owned, _neighbours(owned), strict=True):
                group.add(self._row(bind, index, editable, binds, conflicts, neighbours, empty))
            if not owned:
                group.add(
                    Adw.ActionRow(
                        title="No keybinds in this submap yet",
                        subtitle="Add one with the button above.",
                    )
                )

    def reveal(self, index: int) -> Gtk.Widget | None:
        """Bring the Row for the bind at `index` into view -- the conflict jump, a search hit.

        Navigate + flash (ADR-0007): grabbing focus makes every ancestor scroll the row
        into view, and a short background pulse marks which row that was for a reader
        whose eyes were on the popover, not the focus ring. Returns the row, so a caller
        whose row may be insensitive (a read-only session) can scroll it explicitly; `None`
        when no row has that index.
        """
        for row in self._rows:
            if row.index == index:
                row.widget.grab_focus()
                flash(row.widget)
                return row.widget
        return None

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
        neighbours: tuple[int | None, int | None],
        empty: frozenset[str],
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
            drag=self._drag,
            neighbours=neighbours,
            empty_submaps=empty,
        )
        self._rows.append(row)
        return row.widget


def _neighbours(group: list[tuple[int, Bind]]) -> list[tuple[int | None, int | None]]:
    """For each bind of one group, the flat indices of the binds above and below it there."""
    indices: list[int | None] = [None, *(index for index, _bind in group), None]
    return list(zip(indices, indices[2:], strict=False))
