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

from hyprtweaker.engine.importer.keysyms import validator_available

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


def make_dialog(**kwargs: Any) -> tuple[Any, list[str]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.capture import CaptureDialog

    Adw.init()
    recorded: list[str] = []
    dialog = CaptureDialog(on_done=recorded.append, **kwargs)
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
    editor = BindEditor(on_done=saved.append)
    editor._choose(None)
    editor._trigger.set_text("win + shift + q")
    editor._save()
    assert saved and saved[0].keys == "SHIFT + SUPER + q"


def test_bind_editor_offers_capture_on_the_trigger_row() -> None:
    """Capture has to be reachable from the editor, or it is not wired to anything."""
    from gi.repository import Adw, Gtk

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    editor = BindEditor(on_done=lambda _bind: None)
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
