"""UI smoke tier: the Capture dialog assembles and gates on what it captured (#65).

Shallow like the rest of this tier. The grammar and the held-modifier state machine are
settled in `tests/unit/test_triggers.py` without a display; what is left here is whether
the dialog builds, whether a captured Trigger reaches the confirm button in the right
sensitivity, and whether the shortcut-inhibition bookkeeping is balanced -- the last
being the one that matters, because a missed restore leaves the user's keybinds dead.

Synthetic GDK key events cannot be delivered to an unmapped dialog, so the input paths
are driven through the dialog's own settle/refresh entry points, which is where the
controllers land anyway. Toolkit imports sit inside the test functions, as the tier's
conftest requires.
"""

from __future__ import annotations

from typing import Any

import pytest
from started_app import presented

from hyprtweaker.engine.importer.keysyms import validator_available

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


def make_dialog(**kwargs: Any) -> tuple[Any, list[str]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.capture import CaptureDialog

    Adw.init()
    recorded: list[str] = []
    dialog = presented(CaptureDialog(on_done=recorded.append, **kwargs))
    return dialog, recorded


def test_dialog_assembles() -> None:
    dialog, _ = make_dialog()
    assert dialog.get_child() is not None


def test_empty_capture_cannot_be_confirmed() -> None:
    dialog, _ = make_dialog()
    assert not dialog._confirm.get_sensitive()


def test_initial_trigger_prefills_and_is_confirmable() -> None:
    dialog, _ = make_dialog(initial="SUPER + Q")
    assert dialog._manual.get_text() == "SUPER + Q"
    assert dialog._confirm.get_sensitive()


def test_captured_chord_shows_and_enables_confirm() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger(("SHIFT", "SUPER"), "Q"))
    assert dialog._shortcut.get_text() == "SHIFT + SUPER + Q"
    assert dialog._manual.get_text() == "SHIFT + SUPER + Q"
    assert dialog._confirm.get_sensitive()
    assert not dialog._problem.get_visible()


@needs_xkb
def test_dead_keysym_blocks_confirm_and_explains() -> None:
    """The acceptance criterion: rejected at capture, with a reason."""
    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger(("SUPER",), "notakey"))
    assert dialog._problem.get_visible()
    assert "notakey" in dialog._problem.get_text()
    assert not dialog._confirm.get_sensitive()


@needs_xkb
def test_bare_letter_warns_without_blocking() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger((), "Q"))
    assert dialog._problem.get_visible()
    assert dialog._confirm.get_sensitive()


def test_mouse_and_wheel_triggers_capture() -> None:
    dialog, _ = make_dialog()
    dialog._settle(dialog._recorder.button(1))
    assert dialog._manual.get_text() == "mouse:272"
    dialog._settle(dialog._recorder.wheel("down"))
    assert dialog._manual.get_text() == "mouse_down"
    assert dialog._confirm.get_sensitive()


def test_accept_hands_back_the_canonical_string() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, recorded = make_dialog()
    dialog._settle(Trigger(("SUPER", "SHIFT"), "Q"))
    dialog._accept()
    assert recorded == ["SHIFT + SUPER + Q"]


@needs_xkb
def test_accept_refuses_a_blocked_trigger() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, recorded = make_dialog()
    dialog._settle(Trigger(("SUPER",), "notakey"))
    dialog._accept()
    assert recorded == []


def test_cancel_reports_nothing() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, recorded = make_dialog()
    dialog._settle(Trigger(("SUPER",), "Q"))
    dialog._cancel()
    assert recorded == []


def test_clear_resets_to_empty() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger(("SUPER",), "Q"))
    dialog._clear()
    assert dialog._manual.get_text() == ""
    assert not dialog._confirm.get_sensitive()


@needs_xkb
def test_manual_entry_is_validated_live() -> None:
    dialog, recorded = make_dialog()
    dialog._manual.set_text("SUPER + Enter")
    assert dialog._problem.get_visible()
    assert "Return" in dialog._problem.get_text()
    assert not dialog._confirm.get_sensitive()

    dialog._manual.set_text("SUPER + Return")
    assert dialog._confirm.get_sensitive()
    dialog._accept()
    assert recorded == ["SUPER + Return"]


class FakeToplevel:
    """Stands in for the Gdk.Toplevel the headless tier never gets."""

    def __init__(self) -> None:
        self.restores = 0

    def restore_system_shortcuts(self) -> None:
        self.restores += 1


def inhibited_dialog() -> tuple[Any, FakeToplevel]:
    dialog, _ = make_dialog()
    toplevel = FakeToplevel()
    dialog._toplevel = lambda: toplevel  # type: ignore[method-assign]
    dialog._inhibited = True  # the compositor granted the inhibit
    return dialog, toplevel


def test_cancel_restores_shortcuts_it_inhibited() -> None:
    """A missed restore leaves the session's own keybinds dead until the app quits."""
    dialog, toplevel = inhibited_dialog()
    dialog._cancel()
    assert toplevel.restores == 1
    assert dialog._inhibited is False


def test_accept_restores_shortcuts() -> None:
    from hyprtweaker.engine.triggers import Trigger

    dialog, toplevel = inhibited_dialog()
    dialog._settle(Trigger(("SUPER",), "Q"))
    dialog._accept()
    assert toplevel.restores == 1


def test_restore_is_idempotent() -> None:
    """Every exit path calls restore; the compositor must only be told once."""
    dialog, toplevel = inhibited_dialog()
    dialog._restore_shortcuts()
    dialog._restore_shortcuts()
    assert toplevel.restores == 1
    assert dialog._inhibited is False


# --- the review found these the hard way ----------------------------------------------


def test_mouse_capture_cannot_fire_from_the_dialog_buttons() -> None:
    """Regression: a click gesture on the whole dialog also fires for Cancel and Set, so
    clicking Set overwrote the captured chord with mouse:272 and committed that."""
    from gi.repository import Gtk

    dialog, _ = make_dialog()

    def controllers(widget: Any) -> list[Any]:
        return [c for c in widget.observe_controllers()]

    surface_kinds = {type(c) for c in controllers(dialog._surface)}
    assert Gtk.GestureClick in surface_kinds
    assert Gtk.EventControllerScroll in surface_kinds

    dialog_kinds = {type(c) for c in controllers(dialog)}
    assert Gtk.GestureClick not in dialog_kinds
    assert Gtk.EventControllerScroll not in dialog_kinds

    def contains(parent: Any, needle: Any) -> bool:
        child = parent.get_first_child()
        while child is not None:
            if child is needle or contains(child, needle):
                return True
            child = child.get_next_sibling()
        return False

    assert not contains(dialog._surface, dialog._confirm)
    assert not contains(dialog._surface, dialog._manual)


def test_keys_propagate_while_typing_in_the_manual_entry() -> None:
    """Regression: the capture controller swallowed every key, so typing `S` into the
    fallback recorded the trigger `S` instead of writing a letter."""
    dialog, _ = make_dialog()
    dialog._typing_manually = lambda: True  # type: ignore[method-assign]
    handled = dialog._on_key_pressed(None, 0x073, 39, 0)  # `s`
    assert handled is False
    assert dialog._captured is None


def test_delete_is_capturable_rather_than_a_clear_key() -> None:
    """Regression: treating Delete as clear made a bare Delete bind uncapturable."""
    from gi.repository import Gdk

    dialog, _ = make_dialog()
    handled = dialog._on_key_pressed(None, Gdk.KEY_Delete, 119, 0)
    assert handled is True
    assert dialog._captured is not None
    assert dialog._captured.key == "Delete"


def test_backspace_still_clears() -> None:
    from gi.repository import Gdk

    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger(("SUPER",), "Q"))
    dialog._on_key_pressed(None, Gdk.KEY_BackSpace, 22, 0)
    assert dialog._captured is None


def test_keycode_trigger_shows_a_layout_keysym_hint() -> None:
    """ADR-0007: `code:N` displays as "key code N" plus a current-layout keysym hint."""
    from hyprtweaker.engine.triggers import Trigger

    dialog, _ = make_dialog()
    dialog._settle(Trigger((), "code:36"))
    assert dialog._shortcut.get_text() == "key code 36"
    assert "36" in dialog._hint.get_text()
    assert "layout" in dialog._hint.get_text()


def test_bind_editor_canonicalises_a_hand_typed_trigger() -> None:
    """ADR-0007 requires the canonical spelling be what reaches the writer: Hyprland
    matches modifier names case-sensitively, so `win + q` verbatim would not fire."""
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    saved: list[Any] = []
    editor = presented(BindEditor(on_done=saved.append))
    editor._choose(None)
    editor._trigger.set_text("win + shift + q")
    editor._save()
    assert saved and saved[0].keys == "SHIFT + SUPER + q"


def test_bind_editor_offers_capture_on_the_trigger_row() -> None:
    """Capture has to be reachable from the editor, or it is not wired to anything."""
    from gi.repository import Adw, Gtk

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    editor = presented(BindEditor(on_done=lambda _bind: None))
    editor._choose(None)

    def buttons(widget: Any) -> list[Any]:
        found = []
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Button):
                found.append(child)
            found.extend(buttons(child))
            child = child.get_next_sibling()
        return found

    tooltips = [b.get_tooltip_text() for b in buttons(editor._trigger)]
    assert any(t and "Press the keys" in t for t in tooltips)


# --- the switch picker (#107) ---------------------------------------------------------------

LID = {"address": "0x1", "name": "Lid Switch"}
TABLET = {"address": "0x2", "name": "Tablet Mode Switch"}


def answering(*switches: dict[str, str]) -> Any:
    """A `fetch_switches` whose compositor answers at once with these switches."""
    return lambda done: done(tuple(switches))


def nobody_answering(done: Any) -> None:
    done(None)


def choose(dialog: Any, name: str, when: int = 0) -> None:
    """Pick a switch by its listed name, then the qualifier (0 on, 1 off, 2 either way)."""
    names = [
        dialog._switch_row.get_model().get_string(i)
        for i in range(1, 99)
        if dialog._switch_row.get_model().get_string(i)
    ]
    dialog._when_row.set_selected(when)
    dialog._switch_row.set_selected(1 + names.index(name))


def test_the_picker_lists_the_switches_the_compositor_reported() -> None:
    dialog, _ = make_dialog(fetch_switches=answering(LID, TABLET))
    model = dialog._switch_row.get_model()
    listed = [model.get_string(i) for i in range(model.get_n_items())]
    assert listed[1:] == ["Lid Switch", "Tablet Mode Switch"]
    assert dialog._switch_row.get_sensitive()


def test_picking_a_switch_and_turns_on_writes_the_on_trigger_exactly() -> None:
    dialog, recorded = make_dialog(fetch_switches=answering(LID, TABLET))
    choose(dialog, "Tablet Mode Switch", when=0)
    assert dialog._manual.get_text() == "switch:on:Tablet Mode Switch"
    assert dialog._confirm.get_sensitive()
    dialog._accept()
    assert recorded == ["switch:on:Tablet Mode Switch"]


def test_the_qualifier_picks_on_off_or_the_plain_form() -> None:
    dialog, _ = make_dialog(fetch_switches=answering(LID))
    choose(dialog, "Lid Switch", when=1)
    assert dialog._manual.get_text() == "switch:off:Lid Switch"
    dialog._when_row.set_selected(2)
    assert dialog._manual.get_text() == "switch:Lid Switch"
    dialog._when_row.set_selected(0)
    assert dialog._manual.get_text() == "switch:on:Lid Switch"


def test_a_picked_switch_passes_the_one_loadable_predicate() -> None:
    from hyprtweaker.engine.triggers import trigger_load_problem

    dialog, recorded = make_dialog(fetch_switches=answering(LID))
    choose(dialog, "Lid Switch", when=2)
    dialog._accept()
    assert trigger_load_problem(recorded[0]) is None


def test_a_switch_name_the_bind_syntax_would_split_cannot_be_set() -> None:
    odd = {"address": "0x3", "name": "Foo + Bar"}
    dialog, recorded = make_dialog(fetch_switches=answering(odd))
    choose(dialog, "Foo + Bar")
    assert not dialog._confirm.get_sensitive()
    assert "+" in dialog._problem.get_text()
    dialog._accept()
    assert recorded == []


def test_a_refused_pick_clears_the_trigger_it_replaces() -> None:
    """The entry would otherwise show the last trigger beside a disabled Set, as if that
    trigger were the refused one (#151 review, finding 29)."""
    odd = {"address": "0x3", "name": "Foo + Bar"}
    dialog, _ = make_dialog(fetch_switches=answering(LID, odd))
    choose(dialog, "Lid Switch")
    assert dialog._manual.get_text() == "switch:on:Lid Switch"

    choose(dialog, "Foo + Bar")

    assert dialog._manual.get_text() == ""
    assert dialog._problem.get_visible()
    assert not dialog._confirm.get_sensitive()


def test_a_switch_name_with_an_ampersand_cannot_be_set() -> None:
    """`&` passes `validate_trigger` for a switch; only `trigger_load_problem` refuses it,
    so Capture has to ask the predicate and not only the validator (#199)."""
    odd = {"address": "0x3", "name": "Foo & Bar"}
    dialog, recorded = make_dialog(fetch_switches=answering(odd))
    choose(dialog, "Foo & Bar")
    assert not dialog._confirm.get_sensitive()
    assert dialog._problem.get_visible()
    dialog._accept()
    assert recorded == []


def test_two_switches_with_one_name_list_once() -> None:
    dialog, _ = make_dialog(fetch_switches=answering(LID, dict(LID, address="0x9")))
    assert dialog._switch_row.get_model().get_n_items() == 2  # the prompt and one switch


def test_a_compositor_with_no_switch_says_so_and_manual_entry_still_works() -> None:
    dialog, recorded = make_dialog(fetch_switches=answering())
    assert not dialog._switch_row.get_sensitive()
    assert not dialog._when_row.get_sensitive()
    text = dialog._switch_group.get_description()
    assert "no lid or tablet-mode switch" in text
    assert '"switch:"' in text
    dialog._manual.set_text("switch:on:Lid Switch")
    dialog._accept()
    assert recorded == ["switch:on:Lid Switch"]


def test_without_a_compositor_the_picker_says_why_and_manual_entry_still_works() -> None:
    for fetch in (None, nobody_answering):
        dialog, recorded = make_dialog(fetch_switches=fetch)
        assert not dialog._switch_row.get_sensitive()
        text = dialog._switch_group.get_description()
        assert "Not connected to Hyprland" in text
        assert '"switch:"' in text
        dialog._manual.set_text("switch:Lid Switch")
        dialog._accept()
        assert recorded == ["switch:Lid Switch"]


def test_the_picker_says_it_is_looking_until_the_compositor_answers() -> None:
    pending: list[Any] = []
    dialog, _ = make_dialog(fetch_switches=pending.append)
    assert "Looking" in dialog._switch_group.get_description()
    assert not dialog._switch_row.get_sensitive()
    pending[0]((LID,))
    assert dialog._switch_row.get_sensitive()
    assert "exactly" in dialog._switch_group.get_description()


def test_an_answer_after_the_dialog_closed_changes_nothing() -> None:
    pending: list[Any] = []
    dialog, _ = make_dialog(fetch_switches=pending.append)
    dialog.emit("closed")
    pending[0]((LID,))
    assert not dialog._switch_row.get_sensitive()


def test_an_existing_switch_trigger_preselects_its_switch_and_qualifier() -> None:
    dialog, _ = make_dialog(
        initial="switch:off:Tablet Mode Switch", fetch_switches=answering(LID, TABLET)
    )
    assert dialog._switch_row.get_selected() == 2
    assert dialog._when_row.get_selected() == 1
    assert dialog._manual.get_text() == "switch:off:Tablet Mode Switch"


def test_a_switch_the_compositor_does_not_list_keeps_the_typed_trigger() -> None:
    dialog, _ = make_dialog(initial="switch:on:Gone", fetch_switches=answering(LID))
    assert dialog._switch_row.get_selected() == 0
    assert dialog._manual.get_text() == "switch:on:Gone"
    assert dialog._confirm.get_sensitive()


def test_keys_pressed_in_the_picker_are_not_recorded_as_a_trigger() -> None:
    dialog, _ = make_dialog(fetch_switches=answering(LID))
    assert dialog._owns_keys(dialog._switch_row)
    assert dialog._owns_keys(dialog._when_row)
    assert dialog._owns_keys(dialog._manual)
    assert not dialog._owns_keys(dialog._surface)


def test_the_bind_editor_opens_capture_listing_its_switches(monkeypatch: Any) -> None:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs import bind_editor

    Adw.init()
    editor = presented(
        bind_editor.BindEditor(on_done=lambda _bind: None, fetch_switches=answering(LID))
    )
    editor._choose(None)
    opened: list[Any] = []
    real = bind_editor.CaptureDialog

    def keep(**kwargs: Any) -> Any:
        opened.append(real(**kwargs))
        return opened[-1]

    monkeypatch.setattr(bind_editor, "CaptureDialog", keep)
    editor._capture()

    model = opened[0]._switch_row.get_model()
    assert [model.get_string(i) for i in range(1, model.get_n_items())] == ["Lid Switch"]


def test_no_picker_description_holds_markup_characters() -> None:
    """Adw parses a group description as markup: `switch:<name>` is an unclosed tag and the
    sentence renders blank."""
    from hyprtweaker.ui.dialogs import capture

    for text in (
        capture.SWITCH_LOOKING,
        capture.SWITCH_OFFLINE,
        capture.SWITCH_NONE,
        capture.SWITCH_LISTED,
    ):
        assert "<" not in text and "&" not in text
