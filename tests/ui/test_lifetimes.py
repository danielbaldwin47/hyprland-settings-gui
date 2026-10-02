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
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
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
