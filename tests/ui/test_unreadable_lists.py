"""UI smoke tier: an Entity page whose list was never read says so, not "none yet" (#269).

Without Lua the app cannot read its own Modules, so an empty list there is not the user's
config having no binds or rules. Each list page's empty state names that and the cause.
A launch with no App dir to read (a hyprland.conf on offer for import) keeps "none yet".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from started_app import started_application

APP_VERSION = "0.0.0-test"
UNREADABLE = (
    "This app cannot read your settings right now. This app reads your settings with Lua, "
    "which is not installed. Install Lua (lua5.5, lua5.4, lua5.3, lua or luajit) and open "
    "the app again."
)


def window_without_lua(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A window built, routed and its session started on a machine with no Lua, in the
    order `Application._build_window` does it."""
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
    window = MainWindow(session, application=started_application())
    session.on_state_changed = window.sync
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    window.route_first_run()  # a fresh machine gets its App dir here
    window.start_when_answered(session.start)
    return window


def action_rows(widget: Any) -> Iterator[tuple[str, str]]:
    from gi.repository import Adw

    if isinstance(widget, Adw.ActionRow):
        yield widget.get_title(), widget.get_subtitle() or ""
    child = widget.get_first_child()
    while child is not None:
        yield from action_rows(child)
        child = child.get_next_sibling()


def list_pages(window: Any) -> dict[str, Any]:
    pages = {
        "Keybinds": window.binds_page,
        "Window rules": window.window_rules_page,
        "Layer rules": window.layer_rules_page,
        "Workspace rules": window.workspace_rules_page,
    }
    pages.update((page.title, page) for page in window.declaration_pages)
    return pages


def test_without_lua_every_list_page_says_it_could_not_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = window_without_lua(tmp_path, monkeypatch)

    for title, page in list_pages(window).items():
        assert (f"{title} could not be read", UNREADABLE) in list(action_rows(page.page)), title
    assert window.workspace_rules_page.empty_add_button is None, "no dead Add button"
    displays = window.monitors_page.connected_empty_row
    assert displays is not None
    assert (displays.get_title(), displays.get_subtitle()) == (
        "Display rules could not be read",
        UNREADABLE,
    )
    assert window.binds_page.page.get_title() == "Keybinds"


def test_an_import_on_offer_with_no_app_dir_keeps_none_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "hypr").mkdir()
    (tmp_path / "hypr" / "hyprland.conf").write_text("general {\n    gaps_in = 4\n}\n")
    window = window_without_lua(tmp_path, monkeypatch)
    assert not (tmp_path / "hypr" / "hyprtweaker").exists()

    rows = list(action_rows(window.binds_page.page))
    assert (
        "No keybinds yet",
        "Add one with the button above, or import an existing config.",
    ) in rows
    for title, page in list_pages(window).items():
        assert not any(t.endswith("could not be read") for t, _ in action_rows(page.page)), (
            title
        )
