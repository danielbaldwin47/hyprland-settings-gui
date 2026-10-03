"""UI smoke tier: a control the user moved into a hand-edited Module goes back (#272).

The session refuses the change before the model moves (ADR-0005); these check that the
window draws the model again, so the control shows what the file holds, and that the user
hears about the refusal once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import main_loop
from _live_window import live_entity_window

RULES = "window_rules.lua"


def refusing(session: Any, module: str) -> None:
    """Make `module` read as edited outside the app, as the Manifest check would."""
    session._edited_module = lambda modules: module if module in set(modules) else None


def test_a_refused_rule_toggle_shows_the_rule_as_the_file_has_it(tmp_path: Path) -> None:
    from hyprtweaker.engine.model import WindowRule

    session, window, applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    session.add_rule("window", WindowRule(match={"class": "foot"}, effects={"float": True}))
    applier.settle()
    window.sync()
    refusing(session, RULES)
    page = window._rules_page("window")
    switch = page.rows[0].enabled_switch
    assert switch.get_active() and switch.get_state()

    switch.set_active(False)

    shown = page.rows[0].enabled_switch
    assert session.model.entities.window_rules[0].enabled is True
    assert (shown.get_active(), shown.get_state()) == (True, True)


MONITORS_MODULE = "monitors.lua"
EDP: dict[str, Any] = {
    "name": "eDP-1",
    "description": "BOE 0x0791",
    "width": 1920,
    "height": 1080,
    "refreshRate": 60.0,
    "x": 0,
    "y": 0,
    "scale": 1.0,
    "transform": 0,
    "availableModes": ["1920x1080@60.00Hz"],
}


def display_window(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, list[Any]]:
    """A live window showing one connected display, whose monitors file is hand-edited."""
    from gi.repository import GLib

    from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog

    session, window, _applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    # Answered on a later turn, as the live helper answers `hyprctl -j monitors`.
    session.fetch_monitors = lambda answer: GLib.idle_add(lambda: answer((EDP,)) and False)
    shown: list[Any] = []
    monkeypatch.setattr(
        ConfirmRevertDialog, "present", lambda self, _parent=None: shown.append(self)
    )
    window._monitors_page.set_connected((EDP,))
    refusing(session, MONITORS_MODULE)
    return session, window, shown


def combo(window: Any, title: str) -> Any:
    """The display page's one combo row titled `title`, as the page has it now."""
    from gi.repository import Adw, Gtk

    found: list[Any] = []

    def walk(widget: Any) -> None:
        if isinstance(widget, Adw.ComboRow) and widget.get_title() == title:
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(window._monitors_page.page)
    assert len(found) == 1, f"{len(found)} rows titled {title!r}"
    assert isinstance(found[0], Gtk.Widget)
    return found[0]


def test_a_refused_benign_display_edit_shows_the_display_as_the_file_has_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, _shown = display_window(tmp_path, monkeypatch)
    assert combo(window, "Variable refresh rate").get_selected() == 0

    combo(window, "Variable refresh rate").set_selected(1)
    main_loop.settle("the page drawn again")

    assert session.monitor_rules == []
    assert combo(window, "Variable refresh rate").get_selected() == 0


def test_a_refused_breaking_display_edit_shows_the_display_as_the_file_has_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, shown = display_window(tmp_path, monkeypatch)
    assert combo(window, "Rotation").get_selected() == 0

    combo(window, "Rotation").set_selected(1)
    window.flush_monitor_edits()
    main_loop.settle("the page drawn again")

    assert session.monitor_rules == []
    assert combo(window, "Rotation").get_selected() == 0
    assert (shown, window.display_confirm) == ([], None)


DECORATION = "options/decoration.lua"


def toasts(window: Any) -> list[Any]:
    """Every toast the window raises from now on, a toast raised again counted once."""
    raised: list[Any] = []
    add_toast = window._toasts.add_toast

    def counted(toast: Any) -> None:
        if toast not in raised:
            raised.append(toast)
        add_toast(toast)

    window._toasts.add_toast = counted
    return raised


def toast_text(toast: Any) -> str:
    from hyprtweaker.ui.shell.window import toast_text as said

    return said(toast)


def test_a_refused_slider_drag_says_so_once(tmp_path: Path) -> None:
    session, window, _applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    refusing(session, DECORATION)
    raised = toasts(window)

    for tick in (4, 5, 6, 7, 8):  # what the Row's Gesture sends: a preview per tick
        session.preview_option("decoration:rounding", tick)
    session.set_option("decoration:rounding", 8)  # and the release

    assert [toast_text(toast) for toast in raised] == [
        "Corner rounding was not saved: decoration.lua was edited outside this app"
    ]


def test_a_held_spin_button_says_so_once(tmp_path: Path) -> None:
    session, window, _applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    refusing(session, DECORATION)
    raised = toasts(window)

    for repeat in range(1, 12):  # a held arrow: each repeat refused, the Row put back
        session.touch_option("decoration:rounding", repeat)

    assert [toast_text(toast) for toast in raised] == [
        "Corner rounding was not saved: decoration.lua was edited outside this app"
    ]


def test_another_refused_change_and_a_saved_one_each_say_so(tmp_path: Path) -> None:
    from hyprtweaker.engine.model import WindowRule

    session, window, applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    refusing(session, DECORATION)
    raised = toasts(window)

    session.set_option("decoration:rounding", 8)
    session.set_option("decoration:active_opacity", 0.9)
    session.add_rule("window", WindowRule(match={"class": "foot"}, effects={"float": True}))
    applier.settle()

    assert [toast_text(toast) for toast in raised] == [
        "Corner rounding was not saved: decoration.lua was edited outside this app",
        "Active window opacity was not saved: decoration.lua was edited outside this app",
        "Window rule added",
    ]


def test_the_same_refusal_after_its_toast_went_says_so_again(tmp_path: Path) -> None:
    session, window, _applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    refusing(session, DECORATION)
    raised = toasts(window)

    session.set_option("decoration:rounding", 8)
    raised[0].dismiss()
    session.set_option("decoration:rounding", 9)

    assert len(raised) == 2
