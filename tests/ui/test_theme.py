"""UI tier: the Theme override and "Forget remembered choices" in the primary menu (#183).

The override changes this app's own colour scheme through `Adw.StyleManager`, never the
desktop's. "System" is asserted as the scheme the app asks for (`DEFAULT`) and the stored
choice, not as light or dark: what System *looks* like is the platform's answer, and the
UI tier does not read the owner's settings portal to find it out. Light and Dark are forced
schemes, so their `dark` is asserted outright.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

APP_VERSION = "0.0.0-test"


@pytest.fixture(autouse=True)
def system_scheme_after() -> Iterator[None]:
    """The style manager is one per process: leave it as every other test expects it."""
    yield
    from gi.repository import Adw

    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)


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
    return MainWindow(session, application=app)


def stored(tmp_path: Path) -> Any:
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.prefs import PrefsStore

    return PrefsStore(ConfigPaths.rooted_at(tmp_path).state_dir).load()


def write_prefs(tmp_path: Path, **fields: Any) -> None:
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.prefs import FORMAT_VERSION, PREFS_FILENAME

    path = ConfigPaths.rooted_at(tmp_path).state_dir / PREFS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format_version": FORMAT_VERSION, **fields}), encoding="utf-8")


def scheme() -> tuple[Any, bool]:
    from gi.repository import Adw

    manager = Adw.StyleManager.get_default()
    return manager.get_color_scheme(), manager.get_dark()


def choose(window: Any, theme: str) -> None:
    """What clicking one of the three radio items does."""
    from gi.repository import GLib

    window.activate_action("win.theme", GLib.Variant.new_string(theme))


def primary_menu(window: Any) -> list[tuple[str | None, list[tuple[str, str, str | None]]]]:
    """The primary menu as the user sees it: (section heading, [(label, action, target)])."""
    from gi.repository import Gio, Gtk

    def find(widget: Any) -> Any:
        if (
            isinstance(widget, Gtk.MenuButton)
            and widget.get_icon_name() == "open-menu-symbolic"
        ):
            return widget
        child = widget.get_first_child()
        while child is not None:
            if (found := find(child)) is not None:
                return found
            child = child.get_next_sibling()
        return None

    def items(model: Any) -> list[tuple[str, str, str | None]]:
        entries = []
        for index in range(model.get_n_items()):
            label = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, None)
            action = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_ACTION, None)
            target = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_TARGET, None)
            if action is not None:  # a section's heading is a label with no action
                entries.append(
                    (
                        label.get_string(),
                        action.get_string(),
                        None if target is None else target.get_string(),
                    )
                )
        return entries

    model = find(window).get_popover().get_menu_model()
    sections = [(None, items(model))]
    for index in range(model.get_n_items()):
        section = model.get_item_link(index, Gio.MENU_LINK_SECTION)
        if section is not None:
            heading = model.get_item_attribute_value(index, Gio.MENU_ATTRIBUTE_LABEL, None)
            sections.append((None if heading is None else heading.get_string(), items(section)))
    return sections


# --- the Theme section ------------------------------------------------------------------------


def test_the_primary_menu_offers_system_light_and_dark_then_forgetting(tmp_path: Path) -> None:
    window = build_window(tmp_path)

    sections = primary_menu(window)
    theme = [heading for heading, _ in sections].index("Theme")

    assert sections[theme : theme + 2] == [
        (
            "Theme",
            [
                ("System", "win.theme", "system"),
                ("Light", "win.theme", "light"),
                ("Dark", "win.theme", "dark"),
            ],
        ),
        (None, [("Forget remembered choices", "win.forget-remembered", None)]),
    ]


def test_each_theme_switches_the_app_live_and_survives_a_relaunch(tmp_path: Path) -> None:
    """#78 "Theme override switches live": no restart between choices."""
    from gi.repository import Adw

    window = build_window(tmp_path)

    choose(window, "dark")
    assert scheme() == (Adw.ColorScheme.FORCE_DARK, True)
    assert window.lookup_action("theme").get_state().get_string() == "dark"
    assert stored(tmp_path).theme == "dark"

    choose(window, "light")
    assert scheme() == (Adw.ColorScheme.FORCE_LIGHT, False)
    assert window.lookup_action("theme").get_state().get_string() == "light"
    assert stored(tmp_path).theme == "light"

    choose(window, "system")
    assert scheme()[0] == Adw.ColorScheme.DEFAULT
    assert window.lookup_action("theme").get_state().get_string() == "system"
    assert stored(tmp_path).theme == "system"


def test_the_saved_theme_is_in_force_before_the_window_is_shown(tmp_path: Path) -> None:
    """Applied at construction: the first frame is already the user's choice, no flash."""
    from gi.repository import Adw

    write_prefs(tmp_path, theme="dark")

    window = build_window(tmp_path)

    assert not window.get_visible()
    assert scheme() == (Adw.ColorScheme.FORCE_DARK, True)
    assert window.lookup_action("theme").get_state().get_string() == "dark"


def test_an_unknown_saved_theme_opens_as_system(tmp_path: Path) -> None:
    """A newer app's name, or a hand edit, degrades to System instead of failing to open."""
    from gi.repository import Adw

    Adw.init()
    Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    write_prefs(tmp_path, theme="solarized")

    window = build_window(tmp_path)

    assert scheme()[0] == Adw.ColorScheme.DEFAULT
    assert window.lookup_action("theme").get_state().get_string() == "system"


# --- Forget remembered choices ----------------------------------------------------------------


def test_forgetting_is_unavailable_while_nothing_is_remembered(tmp_path: Path) -> None:
    window = build_window(tmp_path)

    assert window.lookup_action("forget-remembered").get_enabled() is False


def test_forgetting_clears_every_remembered_choice_and_keeps_the_rest(tmp_path: Path) -> None:
    from gi.repository import Adw

    write_prefs(
        tmp_path,
        view="config",
        theme="dark",
        remembered={"import-overwrite": "replace", "leave-unsaved": "discard"},
    )
    window = build_window(tmp_path)
    forget = window.lookup_action("forget-remembered")
    assert forget.get_enabled() is True

    window.activate_action("win.forget-remembered", None)

    after = stored(tmp_path)
    assert dict(after.remembered) == {}
    assert (after.view, after.theme) == ("config", "dark")
    assert forget.get_enabled() is False
    assert scheme()[0] == Adw.ColorScheme.FORCE_DARK


def test_remembering_a_choice_makes_forgetting_available(tmp_path: Path) -> None:
    """The way #170's dialog will store an answer: through the window's one remember path."""
    window = build_window(tmp_path)

    window._remember(window._prefs.with_remembered("import-overwrite", "replace"))

    assert window.lookup_action("forget-remembered").get_enabled() is True
    assert dict(stored(tmp_path).remembered) == {"import-overwrite": "replace"}
