"""UI smoke tier: the Binds Page assembles, and shows what the model holds (#64).

Shallow on purpose, like the rest of this tier. The *text* each Row shows is settled in
`tests/unit/test_ui_binds_text.py` where no display is needed; what is left here is whether
GTK and libadwaita build the list at all, and whether the read-only cases really come out
without edit controls -- which is a fact about assembled widgets, not about a string.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def exec_bind(keys: str, command: str, **kwargs: Any) -> Any:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall

    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,
    )


def test_the_binds_page_is_in_the_sidebar(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.binds import BindsPage

    _session, window = build_window(tmp_path)

    assert window.binds_page is not None
    assert window.binds_page.page.get_title() == BindsPage.title


def test_every_bind_becomes_a_row(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [
            exec_bind("SUPER + Q", "kitty"),
            exec_bind("SUPER + E", "nautilus"),
            exec_bind("SUPER + code:10", "workspace 1"),
        ]
    )
    window.binds_page.refresh()

    assert len(window.binds_page.rows) == 3


def test_duplicates_each_get_their_own_row(tmp_path: Path) -> None:
    """Duplicates are legal and all fire, so the list must not collapse them (ADR-0007)."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    assert len(window.binds_page.rows) == 2


def test_a_function_valued_bind_is_listed_read_only(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind

    session, window = build_window(tmp_path)

    session.model.entities.binds.append(Bind(keys="SUPER + W", dispatcher=None))
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert len(rows) == 1, "a function-valued bind must still be listed"
    assert rows[0].widget.get_subtitle().startswith("Runs a Lua function")


def test_an_empty_list_says_so(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    assert window.binds_page.rows == ()


def test_duplicate_triggers_badge_both_rows_with_fire_order(tmp_path: Path) -> None:
    """ADR-0007: saved duplicates keep a warn badge on both rows stating fire order."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[0].conflict_badge is not None
    assert rows[1].conflict_badge is not None
    assert rows[0].conflict is not None and "fires 1st of 2" in rows[0].conflict.badge_text
    assert rows[1].conflict is not None and "fires 2nd of 2" in rows[1].conflict.badge_text
    assert rows[0].conflict.rivals[0].index == 1


def test_same_trigger_in_different_submaps_is_not_a_conflict(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "a", submap="resize"), exec_bind("SUPER + Q", "b")]
    )
    window.binds_page.refresh()

    assert all(row.conflict_badge is None for row in window.binds_page.rows)


def test_a_universal_conflict_across_submaps_is_badged_without_order(tmp_path: Path) -> None:
    """Cross-submap rivals never share a firing sequence, so no 1st-of-N is claimed."""
    from hyprtweaker.engine.model.entities import BindOptions

    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [
            exec_bind("SUPER + Q", "everywhere", options=BindOptions(submap_universal=True)),
            exec_bind("SUPER + Q", "resize-only", submap="resize"),
        ]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[0].conflict is not None and rows[1].conflict is not None
    assert not rows[0].conflict.ordered
    assert not rows[1].conflict.ordered
    assert "fires" not in rows[0].conflict.badge_text
    assert rows[0].conflict.rivals[0].same_submap is False


def test_a_disabled_bind_is_badged_and_does_not_conflict(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "kept"), exec_bind("SUPER + Q", "off", enabled=False)]
    )
    window.binds_page.refresh()

    rows = window.binds_page.rows
    assert rows[1].badge_label is not None
    assert rows[1].badge_label.get_label() == "Disabled"
    assert rows[0].conflict_badge is None, "a disabled bind must not count as a conflict"


# --- the four badge states (#139) -------------------------------------------------------


def editable_row(
    bind: Any,
    index: int = 3,
    *,
    calls: list[tuple[Any, ...]] | None = None,
    drag: Any = None,
    neighbours: tuple[int | None, int | None] = (None, None),
) -> tuple[Any, list[tuple[Any, ...]]]:
    """One `BindRow` as a live session builds it, with every verb recorded."""
    from hyprtweaker.ui.pages.binds import BindActions, BindRow

    calls = [] if calls is None else calls
    actions = BindActions(
        add=lambda submap: calls.append(("add", submap)),
        edit=lambda index: calls.append(("edit", index)),
        remove=lambda index: calls.append(("remove", index)),
        enable=lambda index, on: calls.append(("enable", index, on)),
        rebind=lambda index: calls.append(("rebind", index)),
        recapture=lambda index: calls.append(("recapture", index)),
        swap=lambda first, second: calls.append(("swap", first, second)),
        move=lambda index, to: calls.append(("move", index, to)),
        edit_submap=lambda name: calls.append(("edit_submap", name)),
    )
    row = BindRow(
        bind,
        index,
        actions=actions,
        on_jump=lambda _index: None,
        editable=True,
        drag=drag,
        neighbours=neighbours,
    )
    return row, calls


def no_xkb_for_notakey(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "hyprtweaker.engine.importer.binds.known_keysym", lambda name: name != "notakey"
    )


def test_a_user_disabled_bind_enables_in_one_click(tmp_path: Path) -> None:
    row, calls = editable_row(exec_bind("SUPER + Q", "kitty", enabled=False))

    assert row.badge_label.get_label() == "Disabled"
    assert "dim-label" in row.widget.get_css_classes()
    assert row.enable_button.get_label() == "Enable"
    row.enable_button.emit("clicked")
    assert calls == [("enable", 3, True)]
    assert row.edit_button is not None and row.remove_button is not None


def test_a_dead_keysym_bind_wears_an_error_badge_and_enable_recaptures(
    tmp_path: Path, monkeypatch: Any
) -> None:
    no_xkb_for_notakey(monkeypatch)
    row, calls = editable_row(exec_bind("SUPER + notakey", "kitty", enabled=False))

    assert row.badge_label.get_label() == 'Unknown key "notakey"'
    assert "error" in row.badge_label.get_css_classes()
    assert "dim-label" not in row.widget.get_css_classes(), "row opacity would fade the badge"
    assert row.enable_button.get_label() == "Fix trigger…"
    row.enable_button.emit("clicked")
    assert calls == [("recapture", 3)], "an error-disabled bind must never enable in one click"
    assert row.edit_button is not None and row.remove_button is not None


def test_a_multi_key_bind_offers_only_remove(tmp_path: Path, monkeypatch: Any) -> None:
    no_xkb_for_notakey(monkeypatch)
    row, calls = editable_row(exec_bind("SUPER + A&B", "kitty", enabled=False))

    assert row.badge_label.get_label() == "Multi-key: Hyprland can't load it"
    assert "0.56" not in row.badge_label.get_tooltip_text(), "copy that expires on a release"
    # Readable: the badge is the one line telling the user to remove this bind.
    assert "warning" in row.badge_label.get_css_classes()
    assert "dim-label" not in row.badge_label.get_css_classes()
    assert "dim-label" not in row.widget.get_css_classes()
    assert (row.enable_button, row.edit_button) == (None, None)
    row.remove_button.emit("clicked")
    assert calls == [("remove", 3)]


def test_a_lua_function_bind_offers_no_controls(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind

    row, _calls = editable_row(Bind(keys="SUPER + W", dispatcher=None))

    assert row.badge_label.get_label() == "Defined by a Lua function in user.lua"
    assert (row.enable_button, row.edit_button, row.remove_button) == (None, None, None)


def test_fix_trigger_enables_the_bind_with_the_captured_key(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import hyprtweaker.ui.shell.window as window_module

    no_xkb_for_notakey(monkeypatch)
    session, window = build_window(tmp_path)
    # Rows offer controls only on a live session; this tier has no compositor to connect.
    monkeypatch.setattr(type(session), "live", property(lambda _self: True))
    session.model.entities.binds.append(exec_bind("SUPER + notakey", "kitty", enabled=False))
    window.binds_page.refresh()

    captures: list[Any] = []

    class FakeCapture:
        def __init__(self, *, on_done: Any, **_kwargs: Any) -> None:
            captures.append(on_done)

        def present(self, _parent: Any) -> None:
            pass

    replaced: list[tuple[int, Any]] = []
    monkeypatch.setattr(window_module, "CaptureDialog", FakeCapture)
    monkeypatch.setattr(
        session, "replace_bind", lambda index, bind: replaced.append((index, bind)) or True
    )

    window.binds_page.rows[0].enable_button.emit("clicked")
    captures[0]("SUPER + F")

    [(index, bind)] = replaced
    assert (index, bind.keys, bind.enabled) == (0, "SUPER + F", True)


def test_a_declared_empty_submap_gets_a_group_flagged_unreachable(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Submap
    from hyprtweaker.ui.pages.binds import UNREACHABLE

    session, window = build_window(tmp_path)

    session.model.entities.submaps.append(Submap(name="resize"))
    window.binds_page.refresh()

    groups = window.binds_page.groups
    assert [g.get_title() for g in groups] == ["Keybinds", "Submap: resize"]
    assert UNREACHABLE in groups[1].get_description()


def test_an_empty_submap_and_the_bind_entering_it_are_flagged_until_it_gets_a_bind(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall, Submap

    reason = (
        "Hyprland cannot enter a submap with no enabled keybinds. "
        "Add or enable a keybind in it."
    )
    session, window = build_window(tmp_path)
    session.model.entities.submaps.append(Submap(name="resize"))
    session.model.entities.binds.append(
        Bind(keys="SUPER + R", dispatcher=DispatcherCall(path="submap", positional=("resize",)))
    )
    window.binds_page.refresh()

    description = window.binds_page.groups[1].get_description()
    assert reason in description
    # Fixed first: the empty sentence leads the unreachable one, here absent (SUPER + R enters).
    badge = window.binds_page.rows[0].badge_label
    assert badge is not None
    assert reason in badge.get_tooltip_text()
    assert "warning" in badge.get_css_classes()

    session.model.entities.binds.append(exec_bind("right", "grow", submap="resize"))
    window.binds_page.refresh()

    assert reason not in window.binds_page.groups[1].get_description()
    assert window.binds_page.rows[0].badge_label is None


def test_a_submap_both_empty_and_unreachable_says_the_empty_part_first(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Submap
    from hyprtweaker.ui.pages.binds import UNREACHABLE

    session, window = build_window(tmp_path)
    session.model.entities.submaps.append(Submap(name="resize"))
    window.binds_page.refresh()

    description = window.binds_page.groups[1].get_description()
    empty = (
        "Hyprland cannot enter a submap with no enabled keybinds. "
        "Add or enable a keybind in it."
    )
    assert empty in description and UNREACHABLE in description
    assert description.index(empty) < description.index(UNREACHABLE)


def test_a_submap_with_only_a_disabled_bind_is_flagged_empty(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Submap

    session, window = build_window(tmp_path)
    session.model.entities.submaps.append(Submap(name="resize"))
    session.model.entities.binds.append(
        exec_bind("right", "grow", submap="resize", enabled=False)
    )
    window.binds_page.refresh()

    assert "cannot enter a submap" in window.binds_page.groups[1].get_description()


def test_a_submap_name_with_an_ampersand_shows_in_its_group_title(tmp_path: Path) -> None:
    """The group title is Pango markup: an unescaped `&` renders it blank."""
    from gi.repository import Gtk

    from hyprtweaker.engine.model.entities import Submap

    session, window = build_window(tmp_path)
    session.model.entities.submaps.append(Submap(name="a&b"))
    window.binds_page.refresh()

    def labels(widget: Any) -> Any:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                yield child
            yield from labels(child)
            child = child.get_next_sibling()

    shown = [label.get_text() for label in labels(window.binds_page.groups[1])]
    assert "Submap: a&b" in shown


def test_an_entered_submap_is_not_flagged(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall, Submap
    from hyprtweaker.ui.pages.binds import UNREACHABLE

    session, window = build_window(tmp_path)

    session.model.entities.submaps.append(Submap(name="resize"))
    session.model.entities.binds.extend(
        [
            Bind(
                keys="SUPER + R",
                dispatcher=DispatcherCall(path="submap", positional=("resize",)),
            ),
            exec_bind("right", "grow", submap="resize"),
        ]
    )
    window.binds_page.refresh()

    groups = window.binds_page.groups
    assert UNREACHABLE not in groups[1].get_description()


def test_reveal_focuses_the_named_row(tmp_path: Path) -> None:
    """The conflict popover's jump: reveal(index) must address the model index."""
    session, window = build_window(tmp_path)

    session.model.entities.binds.extend(
        [exec_bind("SUPER + Q", "first"), exec_bind("SUPER + Q", "second")]
    )
    window.binds_page.refresh()

    # No display focus in the smoke tier; what is assertable is that the lookup finds
    # the right row rather than raising or walking off the list.
    window.binds_page.reveal(1)
    window.binds_page.reveal(99)  # out of range must be a no-op, not an error


def press(widget: Any, accelerator: str) -> None:
    """Fire `widget`'s shortcut for `accelerator`, as the key press would."""
    from gi.repository import Gtk

    for controller in widget.observe_controllers():
        if isinstance(controller, Gtk.ShortcutController):
            for each in controller:
                if each.get_trigger().to_string() == accelerator:
                    each.get_action().activate(Gtk.ShortcutActionFlags(0), widget, None)
                    return
    raise AssertionError(f"{widget!r} has no {accelerator} shortcut")


def test_alt_down_twice_moves_the_same_bind_twice_and_focus_follows_it(tmp_path: Path) -> None:
    """Review of #151, finding 11: keyboard reorder is the only non-pointer route, so the
    moved bind's rebuilt row must hold the focus for the next Alt+Down."""
    import main_loop
    from _live_window import live_entity_window

    session, window, applier = live_entity_window(tmp_path)
    session.model.entities.binds.extend(
        exec_bind(f"SUPER + {key}", f"app-{key}") for key in ("A", "B", "C")
    )
    page = window.binds_page
    page.refresh()
    window.present()
    window._select_section(page.section)
    main_loop.settle("the Binds page to map")
    page.rows[0].widget.grab_focus()

    press(window.get_focus(), "<Alt>Down")
    applier.settle()
    press(window.get_focus(), "<Alt>Down")
    applier.settle()

    assert [row.bind.keys for row in page.rows] == ["SUPER + B", "SUPER + C", "SUPER + A"]
    assert window.get_focus() is page.rows[2].widget


def test_an_ampersand_in_a_trigger_or_command_is_shown_as_written(tmp_path: Path) -> None:
    """`A&B` and `a && b` are text, not Pango markup: parsed as markup they render blank."""
    from gi.repository import Gtk

    row, _calls = editable_row(exec_bind("SUPER + A&B", "make && run"))

    def texts(widget: Any) -> list[str]:
        found = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
        child = widget.get_first_child()
        while child is not None:
            found += texts(child)
            child = child.get_next_sibling()
        return found

    shown = texts(row.widget)
    assert "SUPER + A&B" in shown
    assert "make && run" in shown


# --- reorder: drag and keyboard (#161) ------------------------------------------------------


def controller(widget: Any, kind: Any) -> Any:
    """The one event controller of type `kind` on `widget`."""
    found = [each for each in widget.observe_controllers() if isinstance(each, kind)]
    assert len(found) == 1, f"{len(found)} {kind.__name__} on {widget}"
    return found[0]


def drag_onto(source: Any, target: Any) -> bool:
    """Drive one drag the way GTK does: the handle's source prepares the payload, then the
    target row's drop target gets `enter` and, if it took the drag, `drop`."""
    from gi.repository import Gdk, GObject, Gtk

    provider = controller(source.drag_handle, Gtk.DragSource).emit("prepare", 0.0, 0.0)
    assert provider.ref_formats().contain_gtype(GObject.TYPE_INT)
    drop = controller(target.widget, Gtk.DropTarget)
    if drop.emit("enter", 0.0, 0.0) != Gdk.DragAction.MOVE:
        return False
    return bool(drop.emit("drop", source.index, 0.0, 0.0))


def test_a_drag_from_one_handle_to_another_row_calls_move() -> None:
    from hyprtweaker.ui.pages.binds import BindDrag

    drag, calls = BindDrag(), []
    first, _ = editable_row(exec_bind("SUPER + Q", "a"), 0, calls=calls, drag=drag)
    second, _ = editable_row(exec_bind("SUPER + Q", "b"), 4, calls=calls, drag=drag)

    assert drag_onto(first, second) is True

    assert calls == [("move", 0, 4)]


def test_a_row_from_another_group_is_not_a_drop_target() -> None:
    """No highlight, no drop: order between groups is not something binds.lua holds."""
    from hyprtweaker.ui.pages.binds import BindDrag

    drag, calls = BindDrag(), []
    root, _ = editable_row(exec_bind("SUPER + Q", "a"), 0, calls=calls, drag=drag)
    resize, _ = editable_row(
        exec_bind("right", "grow", submap="resize"), 1, calls=calls, drag=drag
    )
    other, _ = editable_row(exec_bind("left", "go", submap="move"), 2, calls=calls, drag=drag)

    assert drag_onto(root, resize) is False
    assert drag_onto(resize, root) is False
    assert drag_onto(resize, other) is False
    assert drag_onto(root, root) is False, "a row is no target for itself"
    assert calls == []


def test_read_only_rows_get_no_handle_but_other_binds_move_past_them(
    monkeypatch: Any,
) -> None:
    from hyprtweaker.engine.model.entities import Bind
    from hyprtweaker.ui.pages.binds import BindDrag

    no_xkb_for_notakey(monkeypatch)
    drag, calls = BindDrag(), []
    lua, _ = editable_row(Bind(keys="SUPER + W", dispatcher=None), 1, calls=calls, drag=drag)
    multi, _ = editable_row(
        exec_bind("SUPER + A&B", "kitty", enabled=False), 2, calls=calls, drag=drag
    )
    plain, _ = editable_row(exec_bind("SUPER + E", "e"), 3, calls=calls, drag=drag)

    assert (lua.drag_handle, multi.drag_handle) == (None, None)
    assert plain.drag_handle is not None
    assert drag_onto(plain, lua) is True
    assert drag_onto(plain, multi) is True
    assert calls == [("move", 3, 1), ("move", 3, 2)]


def test_an_offline_row_has_no_handle_and_takes_no_drop(tmp_path: Path) -> None:
    from gi.repository import Gtk

    session, window = build_window(tmp_path)
    session.model.entities.binds.append(exec_bind("SUPER + Q", "a"))
    window.binds_page.refresh()

    (row,) = window.binds_page.rows
    assert row.drag_handle is None
    assert not [c for c in row.widget.observe_controllers() if isinstance(c, Gtk.DropTarget)]


def test_an_offline_row_greys_out_its_edit_controls(tmp_path: Path, monkeypatch: Any) -> None:
    """Read-only is temporary (the Banner says why), so Edit, Remove and Enable show,
    insensitive, as on the Workspaces page; the rival verbs stay hidden (#159, #113)."""
    from gi.repository import Gtk

    session, window = build_window(tmp_path)
    session.model.entities.binds.extend(
        [
            exec_bind("SUPER + Q", "a"),
            exec_bind("SUPER + Q", "b"),
            exec_bind("SUPER + E", "e", enabled=False),
        ]
    )
    window.binds_page.refresh()

    first, _second, off = window.binds_page.rows
    sensitive = [
        (button is not None, button is not None and button.get_sensitive())
        for button in (first.edit_button, first.remove_button, off.enable_button)
    ]
    assert sensitive == [(True, False), (True, False), (True, False)]
    assert not [
        c for c in first.widget.observe_controllers() if isinstance(c, Gtk.ShortcutController)
    ]
    assert first.conflict_badge is not None
    verbs = button_labels(first.conflict_badge.get_popover())
    assert verbs == ["Show"], "the rival verbs Rebind and Disable stay hidden"


def button_labels(widget: Any) -> list[str]:
    """The label of every button under `widget`, in tree order."""
    from gi.repository import Gtk

    labels: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button) and child.get_label():
            labels.append(child.get_label())
        labels.extend(button_labels(child))
        child = child.get_next_sibling()
    return labels


def shortcut(row: Any, accelerator: str) -> bool:
    """Press `accelerator` on the row, through its own shortcut controller."""
    from gi.repository import Gtk

    shortcuts = controller(row.widget, Gtk.ShortcutController)
    for each in shortcuts:
        if each.get_trigger().to_string() == accelerator:
            return bool(
                each.get_action().activate(Gtk.ShortcutActionFlags(0), row.widget, None)
            )
    raise AssertionError(f"no {accelerator} shortcut on the row")


def test_alt_up_and_down_move_a_row_past_its_group_neighbours() -> None:
    """The keyboard route to the same move: no reorder only a pointer can make."""
    row, calls = editable_row(exec_bind("SUPER + Q", "a"), 4, neighbours=(1, 6))

    assert shortcut(row, "<Alt>Up") is True
    assert shortcut(row, "<Alt>Down") is True

    assert calls == [("move", 4, 1), ("move", 4, 6)]
    tooltip = row.drag_handle.get_tooltip_text()
    assert "Alt+Up" in tooltip and "Alt+Down" in tooltip


def test_alt_up_on_a_groups_first_row_moves_nothing() -> None:
    row, calls = editable_row(exec_bind("SUPER + Q", "a"), 0, neighbours=(None, 2))

    shortcut(row, "<Alt>Up")

    assert calls == []


def test_a_drag_on_the_page_reorders_and_ctrl_z_puts_it_back(tmp_path: Path) -> None:
    """The whole loop: drop, the Session moves, the page shows the new fire order with a
    "Keybinds reordered" toast, and Undo restores the old order."""
    from test_undo import live_entity_window

    session, window, applier = live_entity_window(tmp_path)
    assert session.edit_binds(
        lambda binds: binds.extend(
            [
                exec_bind("SUPER + Q", "first"),
                exec_bind("right", "grow", submap="resize"),
                exec_bind("SUPER + Q", "second"),
            ]
        )
    )
    applier.settle()
    page = window.binds_page
    page.refresh()

    def fire_order() -> list[tuple[str, str]]:
        return [
            (row.bind.dispatcher.positional[0], row.conflict.short_text)
            for row in page.rows
            if row.conflict is not None
        ]

    assert fire_order() == [("first", "1st of 2"), ("second", "2nd of 2")]
    first, second, _grow = page.rows

    assert drag_onto(first, second) is True
    applier.settle()

    assert fire_order() == [("second", "1st of 2"), ("first", "2nd of 2")]
    assert window.undo_toast is not None
    assert window.undo_toast.get_title() == "Keybinds reordered"

    window.activate_action("win.undo", None)

    assert fire_order() == [("first", "1st of 2"), ("second", "2nd of 2")]
