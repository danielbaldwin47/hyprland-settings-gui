"""UI smoke tier: the Scripting Page lists what `user.lua` and `legacy.lua` hold (#173).

Which calls count as hits is settled headless in `tests/unit/test_scripting_scan.py`;
here the question is whether the window builds the Page in both Views and whether the
assembled groups, rows and buttons say what the scan found -- including when it found
nothing, could not read a file, or crashed.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

APP_VERSION = "0.0.0-test"

USER_LUA = """\
hl.on("workspace.active", function() end)
hl.timer(function() end, { timeout = 500, type = "repeat" })
hl.layout.register("columns", {})
"""

LEGACY_LUA = 'hl.plugin.load("/usr/lib/libhyprexpo.so")\n'


def build_window(tmp_path: Path, *, user: str | None = None, legacy: str | None = None) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    paths = ConfigPaths.rooted_at(tmp_path)
    for path, text in ((paths.user_lua, user), (paths.legacy_lua, legacy)):
        if text is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=paths,
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return MainWindow(session, application=app)


def sidebar_by_category(window: Any) -> dict[str, str]:
    """Each navigable sidebar row's id, mapped to the heading it sits under."""
    from gi.repository import Gtk

    placed: dict[str, str] = {}
    heading = ""
    index = 0
    while (row := window.sidebar.get_row_at_index(index)) is not None:
        child = row.get_child()
        if isinstance(child, Gtk.Label) and not row.get_selectable():
            heading = child.get_text()
        elif row.get_name():
            placed[row.get_name()] = heading
        index += 1
    return placed


def rows(page: Any) -> list[tuple[str, str, str]]:
    return [(group, row.title, row.subtitle) for group, row in page.listed_rows()]


def test_the_page_sits_in_the_system_category_of_the_tasks_view(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.tasks import entity_page_id

    window = build_window(tmp_path)

    assert sidebar_by_category(window)[entity_page_id("scripting")] == "System"


def test_the_page_is_its_own_page_in_the_config_view(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.plan import View
    from hyprtweaker.ui.pages.tasks import entity_page_id

    window = build_window(tmp_path, user=USER_LUA)
    window.set_view(View.CONFIG)

    assert entity_page_id("scripting") in sidebar_by_category(window)
    assert window.scripting_page.page.get_title() == "Scripting"


def test_every_hit_is_a_row_with_its_file_and_line(tmp_path: Path) -> None:
    window = build_window(tmp_path, user=USER_LUA, legacy=LEGACY_LUA)

    assert rows(window.scripting_page) == [
        ("Event handlers", "workspace.active", "user.lua:1"),
        ("Timers", "500 ms, repeat", "user.lua:2"),
        ("Custom layouts", "columns", "user.lua:3"),
        ("Plugin loads", "/usr/lib/libhyprexpo.so", "hyprtweaker/legacy.lua:1"),
    ]


def test_each_hit_has_an_open_in_editor_button(tmp_path: Path) -> None:
    window = build_window(tmp_path, user=USER_LUA, legacy=LEGACY_LUA)

    buttons = [
        (row.open_button.get_tooltip_text(), row.open_button.get_sensitive())
        for _group, row in window.scripting_page.listed_rows()
    ]

    assert buttons == [
        ("Open user.lua", True),
        ("Open user.lua", True),
        ("Open user.lua", True),
        ("Open legacy.lua", True),
    ]


def test_the_open_button_hands_the_file_to_the_window(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.scripting import ScriptingActions, ScriptingPage

    session = build_window(tmp_path, user=USER_LUA).scripting_page._session
    opened: list[Path] = []
    page = ScriptingPage(session, actions=ScriptingActions(open_file=opened.append))

    _group, first = page.listed_rows()[0]
    first.open_button.emit("clicked")

    assert opened == [session.paths.user_lua]


def test_the_inventory_says_it_is_best_effort(tmp_path: Path) -> None:
    window = build_window(tmp_path, user=USER_LUA)

    assert window.scripting_page.caveat == (
        "Found by reading user.lua and legacy.lua. Calls built at runtime, in loops or "
        "through other names may not appear."
    )


def test_an_empty_inventory_still_shows_the_page_with_one_sentence(tmp_path: Path) -> None:
    window = build_window(tmp_path)

    assert rows(window.scripting_page) == [
        (
            "In your Lua files",
            "No event handlers, timers, custom layouts or plugin loads found",
            "",
        )
    ]


def test_what_could_not_be_read_is_shown_beside_what_was_found(tmp_path: Path) -> None:
    user = 'local on = hl.on\nhl.on("kept", f)\n--[[ never closes\nhl.on("lost", f)\n'
    window = build_window(tmp_path, user=user, legacy=LEGACY_LUA)
    page = window.scripting_page
    legacy = page._session.paths.legacy_lua
    legacy.unlink()
    legacy.mkdir()

    page.refresh()

    assert rows(page) == [
        (
            "In your Lua files",
            "Could not read legacy.lua",
            "Nothing in it is listed. Check that it is a file you can read.",
        ),
        (
            "In your Lua files",
            "hl.on is used through another name",
            "user.lua:1 · calls made that way are not listed",
        ),
        (
            "In your Lua files",
            "Stopped reading user.lua at line 3",
            "A comment or string starts there and never closes, so nothing after it is listed.",
        ),
        ("Event handlers", "kept", "user.lua:2"),
    ]


def test_a_scanner_crash_shows_a_row_instead_of_breaking_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hyprtweaker.ui.pages import scripting

    def crash(*_args: object) -> object:
        raise RuntimeError("scanner bug")

    monkeypatch.setattr(scripting, "scan_scripting", crash)

    window = build_window(tmp_path, user=USER_LUA)

    assert rows(window.scripting_page) == [
        (
            "In your Lua files",
            "Could not search your Lua files",
            "This list is for reading only, so your settings are not affected.",
        )
    ]


def test_showing_the_page_reads_the_files_again(tmp_path: Path) -> None:
    """Open in editor, save, come back: the Page is current without a restart."""
    window = build_window(tmp_path, user=USER_LUA)
    page = window.scripting_page
    page._session.paths.user_lua.write_text('hl.on("added", f)\n')

    window._select_section(page.section)

    assert window.visible_section == page.section
    assert rows(page) == [("Event handlers", "added", "user.lua:1")]
