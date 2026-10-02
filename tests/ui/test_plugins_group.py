"""UI smoke tier: the Plugins group on the Scripting Page (#174).

What the list writes is settled headless (`tests/unit/test_session_plugins.py`,
`test_writer_plugins.py`); here the question is whether the assembled group says what the
list holds -- order, the switch, the loaded marker, the empty state -- and whether its
buttons reach the Session as undoable edits.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _live_window import live_entity_window

BARS = "/usr/lib/hyprland-plugins/libhyprbars.so"
EXPO = "/usr/lib/hyprland-plugins/hyprexpo.so"


def plugin(path: str, *, enabled: bool = True) -> Any:
    from hyprtweaker.engine.model.entities import PluginLoad

    return PluginLoad(path, enabled=enabled)


def existing(tmp_path: Path, name: str) -> str:
    """A `.so` that is really there, so the row is not marked "File not found"."""
    path = tmp_path / "plugins" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF")
    return str(path)


def window_with(tmp_path: Path, *plugins: Any, loaded: tuple[str, ...] | None = None) -> Any:
    session, window, applier = live_entity_window(tmp_path)
    # The tier has no compositor: the answer `hyprctl plugin list` would give is scripted
    # here, and the real query is covered in the unit tier.
    session.fetch_loaded_plugins = lambda done: done(loaded)
    for each in plugins:
        session.add_declaration("plugins", each)
    applier.settle()
    window.scripting_page.refresh()
    return session, window, applier


def group_of(window: Any) -> Any:
    group = window.scripting_page.plugins
    assert group is not None
    return group


def shown(window: Any) -> list[tuple[str, str, bool, str]]:
    """Each row as the user reads it: file name, path, switch, loaded marker."""
    return [
        (
            row.widget.get_title(),
            row.widget.get_subtitle(),
            row.enabled_switch.get_active(),
            row.status.get_text() if row.status.get_visible() else "",
        )
        for row in group_of(window).rows
    ]


def first_group_title(page: Any) -> str:
    from gi.repository import Adw

    def walk(widget: Any) -> Any:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.PreferencesGroup):
                return child
            if (found := walk(child)) is not None:
                return found
            child = child.get_next_sibling()
        return None

    found = walk(page)
    return "" if found is None else found.get_title()


def test_the_group_comes_first_on_the_scripting_page(tmp_path: Path) -> None:
    _session, window, _applier = window_with(tmp_path)

    assert first_group_title(window.scripting_page.page) == "Plugins"


def test_an_empty_list_shows_one_sentence_and_the_add_action(tmp_path: Path) -> None:
    _session, window, _applier = window_with(tmp_path)
    group = group_of(window)

    assert group.rows == []
    assert group.empty_row.get_visible()
    assert group.empty_row.get_title() == (
        "No plugins load from this app. Add a plugin's .so file to load it at startup."
    )
    assert group.add_button.get_label() == "Add plugin"
    assert group.add_button.get_sensitive()


def test_each_entry_is_a_row_in_list_order_with_its_switch_and_loaded_state(
    tmp_path: Path,
) -> None:
    bars = existing(tmp_path, "libhyprbars.so")
    expo = existing(tmp_path, "hyprexpo.so")
    _session, window, _applier = window_with(
        tmp_path,
        plugin(bars),
        plugin(expo, enabled=False),
        plugin("/gone/libborders.so"),
        loaded=("hyprbars", "Hyprspace"),
    )

    assert shown(window) == [
        ("libhyprbars.so", bars, True, "Loaded"),
        ("hyprexpo.so", expo, False, ""),
        ("libborders.so", "/gone/libborders.so", True, "File not found"),
    ]
    group = group_of(window)
    assert not group.empty_row.get_visible()
    assert group.also_loaded.get_visible()
    assert group.also_loaded.get_title() == "Also loaded: Hyprspace"


def test_an_enabled_plugin_hyprland_did_not_load_says_so(tmp_path: Path) -> None:
    bars = existing(tmp_path, "libhyprbars.so")
    _session, window, _applier = window_with(tmp_path, plugin(bars), loaded=())

    assert shown(window) == [("libhyprbars.so", bars, True, "Not loaded")]
    assert not group_of(window).also_loaded.get_visible()


def test_with_no_answer_from_hyprland_no_row_claims_a_loaded_state(tmp_path: Path) -> None:
    bars = existing(tmp_path, "libhyprbars.so")
    _session, window, _applier = window_with(tmp_path, plugin(bars), loaded=None)

    assert shown(window) == [("libhyprbars.so", bars, True, "")]


def test_a_path_with_markup_characters_is_shown_verbatim(tmp_path: Path) -> None:
    path = "/opt/a & <b>/libx.so"
    _session, window, _applier = window_with(tmp_path, plugin(path))

    assert shown(window)[0][:2] == ("libx.so", path)


def test_the_switch_disables_the_plugin_as_one_undoable_step(tmp_path: Path) -> None:
    session, window, applier = window_with(tmp_path, plugin(BARS))

    group_of(window).rows[0].enabled_switch.set_active(False)
    applier.settle()

    assert [p.enabled for p in session.declarations("plugins")] == [False]
    assert window.undo_toast is not None
    assert window.undo_toast.get_title() == "Plugin disabled"
    assert shown(window)[0][2] is False


def test_removing_a_plugin_then_undo_puts_it_back_on_the_page(tmp_path: Path) -> None:
    """AC: remove, then the toast's Undo (Ctrl+Z's action) restores it, through #189."""
    session, window, applier = window_with(tmp_path, plugin(BARS), plugin(EXPO))

    group_of(window).rows[0].remove_button.emit("clicked")
    applier.settle()
    assert [p.path for p in session.declarations("plugins")] == [EXPO]
    assert len(group_of(window).rows) == 1
    toast = window.undo_toast
    assert toast is not None
    assert toast.get_title() == "Plugin removed"

    toast.emit("button-clicked")

    assert [p.path for p in session.declarations("plugins")] == [BARS, EXPO]
    assert [row.widget.get_subtitle() for row in group_of(window).rows] == [BARS, EXPO]


def test_alt_up_and_down_reorder_the_list(tmp_path: Path) -> None:
    from gi.repository import Gtk

    session, window, applier = window_with(tmp_path, plugin(BARS), plugin(EXPO))
    row = group_of(window).rows[0]
    assert row.handle.get_tooltip_text() == "Drag to reorder, or press Alt+Up or Alt+Down"

    pressed = {}
    for controller in row.widget.observe_controllers():
        if isinstance(controller, Gtk.ShortcutController):
            for each in controller:
                pressed[each.get_trigger().to_string()] = each
    pressed["<Alt>Down"].get_action().activate(Gtk.ShortcutActionFlags(0), row.widget, None)
    applier.settle()

    assert [p.path for p in session.declarations("plugins")] == [EXPO, BARS]
    assert [r.widget.get_subtitle() for r in group_of(window).rows] == [EXPO, BARS]
    assert window.undo_toast is not None
    assert window.undo_toast.get_title() == "Plugins reordered"


def test_adding_a_path_already_listed_is_refused_with_a_reason(tmp_path: Path) -> None:
    session, window, _applier = window_with(tmp_path, plugin(BARS))
    toasts: list[str] = []
    window._toasts.add_toast = lambda toast: toasts.append(toast.get_title())

    window.add_plugin_path(BARS)

    assert [p.path for p in session.declarations("plugins")] == [BARS]
    assert toasts == ["libhyprbars.so is already in the list"]


PLUGIN_SETTINGS_FOOTER = (
    "This version of Hyprland does not report the settings a plugin adds, so they are not "
    "shown here. Set them in your user.lua."
)


def test_the_footer_says_plugin_settings_are_not_shown_and_where_to_set_them(
    tmp_path: Path,
) -> None:
    """Hyprland 0.56.2's `descriptions` omits every plugin setting (ADR-0018): without this
    sentence a user who loads a plugin finds no rows for it and no word on why."""
    _session, window, _applier = window_with(tmp_path, plugin(BARS))
    footer = group_of(window).footer

    assert footer.get_text() == PLUGIN_SETTINGS_FOOTER
    assert footer.get_visible()


def test_the_footer_hides_once_hyprland_reports_a_plugins_settings(tmp_path: Path) -> None:
    """A later Hyprland that lists them gives them rows, and the sentence would be false."""
    from hyprtweaker.engine.schema import SupplementKind, supplement

    session, window, _applier = window_with(tmp_path, plugin(BARS))
    record = {"name": "plugin:hyprbars:bar_height", "description": "x", "default": 15}
    session._schema = supplement(
        session.schema, (record,), version="0.58.0", kind=SupplementKind.PLUGIN
    )

    window.scripting_page.refresh()

    assert not group_of(window).footer.get_visible()


def test_a_read_only_session_shows_the_list_but_offers_no_edit(tmp_path: Path) -> None:
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
        app_version="0.0.0-test",
        connect=no_compositor,
    )
    session.model.entities.plugins.append(plugin(BARS))
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    window = MainWindow(session, application=app)
    group = group_of(window)

    assert [row.widget.get_subtitle() for row in group.rows] == [BARS]
    assert not group.add_button.get_sensitive()
    assert not group.rows[0].enabled_switch.get_sensitive()
    assert group.rows[0].remove_button is None
