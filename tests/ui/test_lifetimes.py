"""UI tier: what the app closes or replaces, it lets go of (#219).

The app opens and closes dialogs on its one window for as long as it runs, rebuilds its
Pages on every View switch and its Binds list on every edit, and a window it closes must not
outlive its close. Each test does the thing N times through the app's own route and asserts,
through weak references, that everything it closed or replaced was collected.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

import gc
import weakref
from pathlib import Path
from typing import Any

import main_loop
from started_app import started_application

APP_VERSION = "0.0.0-test"
TIMES = 3


def offline_session(root: Path) -> Any:
    """A Session with no compositor: its `close` calls back at once (`Session.close`)."""
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    return Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(root),
        app_version=APP_VERSION,
        connect=no_compositor,
    )


def wired_window(session: Any) -> Any:
    """A MainWindow with the Session's callbacks on it, as `application.py` wires them."""
    from gi.repository import Adw

    from hyprtweaker.ui.shell.window import MainWindow

    Adw.init()
    app = started_application()
    window = MainWindow(session, application=app)
    session.on_state_changed = window.sync
    session.on_applied = window.show_result
    session.on_reverted = window.show_revert
    session.on_notice = window.show_notice
    session.on_recorded = window.offer_undo
    return window


def add_bind_button(window: Any) -> Any:
    """The Binds page's own "Add a keybind" button."""
    from gi.repository import Gtk

    page = window.binds_page.page
    stack = [page]
    while stack:
        widget = stack.pop()
        if isinstance(widget, Gtk.Button) and widget.get_tooltip_text() == "Add a keybind":
            return widget
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError("the Binds page has no Add a keybind button")


def collected(refs: list[weakref.ref[Any]]) -> list[bool]:
    main_loop.settle("the closed objects' teardown")
    gc.collect()
    return [ref() is None for ref in refs]


def test_a_bind_editor_closed_on_one_window_is_released_each_time(tmp_path: Path) -> None:
    window = wired_window(offline_session(tmp_path))
    refs = []
    for _ in range(TIMES):
        add_bind_button(window).emit("clicked")
        dialog = window.get_visible_dialog()
        assert type(dialog).__name__ == "BindEditor"
        refs.append(weakref.ref(dialog))
        dialog.force_close()
        del dialog
        main_loop.settle("the bind editor to close")

    assert window.get_visible_dialog() is None
    assert collected(refs) == [True] * TIMES
    window.close()


def test_a_dialog_is_released_once_its_animation_out_takes_it_out_of_the_window(
    tmp_path: Path,
) -> None:
    """libadwaita emits `closed` as a dialog starts to animate out, still in the window (#228).

    It takes the dialog out when the animation ends (`adw-dialog.c`: `sheet_closing_cb` emits
    `closed`, `sheet_closed_cb` removes it). A release queued on `closed` alone ran in
    between whenever the main loop went idle mid-animation, was refused, and the dialog
    stayed for as long as the window did. The animation's timing is the main loop's, so the
    test plays its two ends itself: `closed` first, the removal after the loop has settled.
    """
    window = wired_window(offline_session(tmp_path))
    add_bind_button(window).emit("clicked")
    dialog = window.get_visible_dialog()
    ref = weakref.ref(dialog)

    dialog.emit("closed")
    main_loop.settle("what the animation's first frames leave queued")
    assert dialog.get_parent() is not None
    dialog.force_close()
    del dialog

    assert collected([ref]) == [True]
    window.close()


def test_a_window_closed_through_its_close_route_is_released_each_time(tmp_path: Path) -> None:
    from gi.repository import Gtk

    refs = []
    for turn in range(TIMES):
        window = wired_window(offline_session(tmp_path / str(turn)))
        # Presented, as the app does: GTK ignores `close()` on a window never realized.
        window.present()
        main_loop.settle("the window to map")
        window.close()
        assert window not in list(Gtk.Window.get_toplevels())
        refs.append(weakref.ref(window))
        del window

    assert collected(refs) == [True] * TIMES


def test_switching_the_view_releases_the_pages_it_replaced(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.plan import View

    window = wired_window(offline_session(tmp_path))
    window.present()
    main_loop.settle("the window to map")
    refs = []
    for _ in range(TIMES):
        # Each Row's chrome: its own buttons' handlers hold it, the cycle a dropped Page
        # left behind.
        refs.extend(weakref.ref(row.chrome) for page in window.pages for row in page.rows)
        window.set_view(View.TASKS if window.view is View.CONFIG else View.CONFIG)
        main_loop.settle("the new View to build")

    assert refs
    assert collected(refs) == [True] * len(refs)
    window.close()


def test_a_binds_page_refresh_releases_the_rows_it_replaced(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall

    session = offline_session(tmp_path)
    window = wired_window(session)
    # Rows offer controls only on a live session; this tier has no compositor to connect.
    monkeypatch.setattr(type(session), "live", property(lambda _self: True))
    session.model.entities.binds.append(
        Bind(
            keys="SUPER + T", dispatcher=DispatcherCall(path="exec_cmd", positional=("kitty",))
        )
    )
    refs = []
    for _ in range(TIMES):
        window.binds_page.refresh()
        refs.extend(weakref.ref(row) for row in window.binds_page.rows)
    window.binds_page.refresh()

    assert len(refs) == TIMES
    assert collected(refs) == [True] * TIMES


def test_releasing_a_widget_still_in_the_tree_is_refused_and_leaves_it_working() -> None:
    import pytest
    from gi.repository import Gtk

    from hyprtweaker.ui.release import release

    box = Gtk.Box()
    clicks: list[bool] = []
    button = Gtk.Button(label="Still here")
    button.connect("clicked", lambda _button: clicks.append(True))
    box.append(button)

    with pytest.raises(ValueError, match="still in it"):
        release(button)
    button.emit("clicked")

    assert clicks == [True]


def test_a_dialog_shown_again_after_one_it_opened_closes_is_released_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Capture over the bind editor hands visibility back to the editor when it closes."""
    from hyprtweaker.ui import release as release_module

    release = release_module.release
    released: list[str] = []

    def counting(widget: Any) -> None:
        released.append(type(widget).__name__)
        release(widget)

    # Where a closed dialog is released (`release_when_unparented`, F18).
    monkeypatch.setattr(release_module, "release", counting)
    window = wired_window(offline_session(tmp_path))
    add_bind_button(window).emit("clicked")
    editor = window.get_visible_dialog()
    editor._choose(None)  # past the door, to the form whose Keys row opens Capture
    for _ in range(TIMES):
        editor._capture()
        window.get_visible_dialog().force_close()
        main_loop.settle("Capture to close")
        assert window.get_visible_dialog() is editor
    editor.force_close()
    main_loop.settle("the bind editor to close")

    assert released == ["CaptureDialog"] * TIMES + ["BindEditor"]
    window.close()


# --- what an open dialog removes, it lets go of (review #151, findings 14 and 15) --------

WINDOWS = ({"class": "kitty", "title": "shell", "initialClass": "kitty"},)


def widget_with_tooltip(root: Any, tooltip: str) -> Any:
    """The first widget under `root` whose tooltip starts with `tooltip`."""
    stack = [root]
    while stack:
        widget = stack.pop()
        if (widget.get_tooltip_text() or "").startswith(tooltip):
            return widget
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError(f"nothing under {root} has a tooltip starting {tooltip!r}")


def rule_editor(window: Any, fetch: Any, match: dict[str, Any] | None = None) -> Any:
    """A window-rule editor presented on `window`, as the Rules page presents one."""
    from hyprtweaker.engine.model.entities import WindowRule
    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    rule = WindowRule(match=match, effects={"float": True}) if match is not None else None
    editor = RuleEditor(
        kind="window", on_done=lambda _rule: None, rule=rule, fetch_targets=fetch
    )
    editor.present(window)
    main_loop.settle("the rule editor to open")
    return editor


def test_a_rule_editor_whose_match_was_removed_is_released_after_close(tmp_path: Path) -> None:
    window = wired_window(offline_session(tmp_path))
    refs = []
    for _ in range(TIMES):
        editor = rule_editor(window, None, match={"class": "kitty", "title": "shell"})
        widget_with_tooltip(editor, "Remove this match").emit("clicked")
        main_loop.settle("the removed match row's release")
        refs.append(weakref.ref(editor))
        editor.force_close()
        del editor
        main_loop.settle("the rule editor to close")

    assert collected(refs) == [True] * TIMES
    window.close()


def test_a_workspace_rule_editor_whose_field_was_removed_is_released_after_close(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.model.entities import WorkspaceRule

    session = offline_session(tmp_path)
    session.model.entities.workspace_rules.append(
        WorkspaceRule(workspace="3", fields={"monitor": "DP-1", "persistent": True})
    )
    window = wired_window(session)
    refs = []
    for _ in range(TIMES):
        dialog = window.workspace_rule_editor("3")
        dialog.present(window)
        widget_with_tooltip(dialog.fields.row("persistent"), "Remove").emit("clicked")
        main_loop.settle("the removed field row's release")
        # The field rows, not only the dialog: a removed row's own button holds them.
        refs.extend((weakref.ref(dialog), weakref.ref(dialog.fields)))
        dialog.force_close()
        del dialog
        main_loop.settle("the workspace rule editor to close")

    assert collected(refs) == [True] * TIMES * 2
    window.close()


def test_a_rule_editor_whose_picker_was_used_is_released_after_close(tmp_path: Path) -> None:
    window = wired_window(offline_session(tmp_path))
    refs = []
    for _ in range(TIMES):
        editor = rule_editor(window, lambda done: done(WINDOWS))
        editor._open_picker()
        editor._picker_rows[0].emit("activated")
        main_loop.settle("the picker page to pop")
        refs.append(weakref.ref(editor))
        editor.force_close()
        del editor
        main_loop.settle("the rule editor to close")

    assert collected(refs) == [True] * TIMES
    window.close()


def test_the_pickers_also_match_switches_still_work_on_its_second_opening(
    tmp_path: Path,
) -> None:
    """The switches outlive each picker page: releasing a popped page must not take them."""
    window = wired_window(offline_session(tmp_path))
    editor = rule_editor(window, lambda done: done(WINDOWS))
    editor._open_picker()
    editor._view.pop()
    main_loop.settle("the first picker page's release")

    editor._open_picker()
    editor._pick_title.set_active(True)
    editor._picker_rows[0].emit("activated")

    assert editor._collect_match() == {"class": "^(kitty)$", "title": "^(shell)$"}
    editor.force_close()
    window.close()


def test_a_fetch_answer_after_the_rule_editor_closed_touches_nothing(
    tmp_path: Path, capfd: Any
) -> None:
    window = wired_window(offline_session(tmp_path))
    pending: list[Any] = []
    editor = rule_editor(window, pending.append, match={"class": "kitty"})
    editor._open_picker()
    editor.force_close()
    main_loop.settle("the rule editor's close and release")
    capfd.readouterr()

    for answer in pending:  # the badge's count, then the picker's list
        answer(WINDOWS)

    assert "CRITICAL" not in capfd.readouterr().err
    window.close()


def test_a_monitors_answer_after_a_rebuild_lands_on_the_new_page(tmp_path: Path) -> None:
    session = offline_session(tmp_path)
    window = wired_window(session)
    pending: list[Any] = []
    session.fetch_monitors = pending.append
    window._refresh_monitors()
    window.rebuild()

    pending[0](({"name": "DP-1", "width": 1920, "height": 1080, "x": 0, "y": 0, "scale": 1},))

    assert [row.get_title() for row in window.monitors_page.connected_rows] == ["DP-1"]
    window.close()


def test_a_scripting_page_refresh_releases_the_plugin_rows_and_groups_it_replaced(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The merger's `release(group)` in `ScriptingPage.refresh` (spec #152 review finding
    6): a `PluginRow`'s switch, step and drop handlers are its own bound methods, the cycle
    #219 found, and each refresh replaces every row and every inventory group."""
    from gi.repository import Adw

    from hyprtweaker.engine.model.entities import PluginLoad

    session = offline_session(tmp_path)
    session.paths.user_lua.parent.mkdir(parents=True, exist_ok=True)
    session.paths.user_lua.write_text('hl.on("workspace.active", f)\nhl.timer(f, {})\n')
    window = wired_window(session)
    monkeypatch.setattr(type(session), "live", property(lambda _self: True))
    session.model.entities.plugins.extend(
        [PluginLoad("/usr/lib/libhyprbars.so"), PluginLoad("/usr/lib/hyprexpo.so")]
    )
    page = window.scripting_page

    def inventory() -> list[Any]:
        found, stack = [], [page.page]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Adw.PreferencesGroup) and widget is not page.plugins.group:
                found.append(widget)
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return found

    refs = []
    for _ in range(TIMES):
        page.refresh()
        refs.extend(weakref.ref(row) for row in page.plugins.rows)
        refs.extend(weakref.ref(row.widget) for row in page.plugins.rows)
        refs.extend(weakref.ref(group) for group in inventory())
    page.refresh()

    # Per refresh: two rows and their widgets, the lead group and two kinds' groups.
    assert len(refs) == TIMES * 7
    assert collected(refs) == [True] * len(refs)


def test_a_theming_page_refresh_releases_the_rows_it_replaced(
    tmp_path: Path, stub_tool: Any
) -> None:
    """`ThemingPage.refresh` replaces every row of its groups (#164); each row's buttons
    hold the page's bound methods, the cycle #219 found."""
    from hyprtweaker.engine.bridge import MATUGEN, Wallpaper, bridge_states_for
    from hyprtweaker.engine.state import Manifest
    from hyprtweaker.engine.writer import Writer

    stub_tool("matugen")
    stub_tool("dms")
    (tmp_path / "hypr/dms").mkdir(parents=True)
    (tmp_path / "hypr/dms/colors.lua").write_text("return {}\n")
    session = offline_session(tmp_path)
    manifest = Manifest.load(session.paths.manifest, app_version="x", schema_version="y")
    entries = bridge_states_for(
        Wallpaper("matugen"), manifest.add_bridge(MATUGEN, present=()).bridges, present=()
    )
    Writer(session.paths, app_version=APP_VERSION).record_bridges(session.model, entries)
    window = wired_window(session)
    page = window.theming_page
    assert page.button("Remove…") is not None, page.rows

    refs = []
    for _ in range(TIMES):
        page.refresh()
        refs.extend(weakref.ref(row) for rows in page._rows.values() for row in rows)
    page.refresh()

    assert len(refs) >= TIMES * 5
    assert collected(refs) == [True] * len(refs)


def test_a_presets_group_rebuild_releases_the_rows_it_replaced(tmp_path: Path) -> None:
    """Every Preset row's buttons hold the group's bound methods, the cycle #219 found; the
    group rebuilds on a save, a delete, an import and when its revision moved (#171)."""
    from hyprtweaker.engine.presets import CaptureScope

    session = offline_session(tmp_path)
    session.model.set("general:border_size", 3)
    window = wired_window(session)
    group = window.theming_page.presets
    session.save_preset("Nord", (CaptureScope.GAPS_LAYOUT,), done=lambda _result: None)
    session.save_preset("Fjord", (CaptureScope.GAPS_LAYOUT,), done=lambda _result: None)

    refs = []
    for _ in range(TIMES):
        group.refresh(force=True)
        refs.extend(weakref.ref(row) for row in (*group.rows.values(), *group._others))
        refs.extend(weakref.ref(button) for button in group._buttons.values())
    group.refresh(force=True)
    session.delete_preset("nord")
    group.refresh()  # the revision moved: the rows go and come back as one

    assert len(refs) >= TIMES * 8
    assert collected(refs) == [True] * len(refs)


def test_a_picker_page_popped_in_a_mapped_dialog_is_released_after_its_animation(
    tmp_path: Path,
) -> None:
    """F18 of the #148 review: libadwaita keeps a popped page parented until the pop
    animation ends, so in a mapped dialog the idle `release` raised ValueError (the UI
    tier's gate fails a test whose idle raises). The page goes once it is out."""
    import time

    window = wired_window(offline_session(tmp_path))
    window.present()
    editor = rule_editor(window, lambda done: done(WINDOWS))
    main_loop.settle("the rule editor to map")
    editor._open_picker()
    main_loop.settle("the picker page to push")
    page = weakref.ref(editor._view.get_visible_page())
    editor._view.pop()
    deadline = time.monotonic() + 3
    while (
        page() is not None and page().get_parent() is not None and time.monotonic() < deadline
    ):
        main_loop.settle("the pop animation")
        time.sleep(0.05)
    main_loop.settle("the popped page's release")

    assert page() is None or page().get_parent() is None
    editor.force_close()
    main_loop.settle("the rule editor to close")
    window.close()
