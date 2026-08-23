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


def test_dead_keysym_blocks_confirm_and_explains() -> None:
    """The acceptance criterion: rejected at capture, with a reason."""
    from hyprtweaker.engine.importer.keysyms import validator_available
    from hyprtweaker.engine.triggers import Trigger

    if not validator_available():
        return
    dialog, _ = make_dialog()
    dialog._settle(Trigger(("SUPER",), "notakey"))
    assert dialog._problem.get_visible()
    assert "notakey" in dialog._problem.get_text()
    assert not dialog._confirm.get_sensitive()


def test_bare_letter_warns_without_blocking() -> None:
    from hyprtweaker.engine.importer.keysyms import validator_available
    from hyprtweaker.engine.triggers import Trigger

    if not validator_available():
        return
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


def test_accept_refuses_a_blocked_trigger() -> None:
    from hyprtweaker.engine.importer.keysyms import validator_available
    from hyprtweaker.engine.triggers import Trigger

    if not validator_available():
        return
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


def test_manual_entry_is_validated_live() -> None:
    from hyprtweaker.engine.importer.keysyms import validator_available

    if not validator_available():
        return
    dialog, recorded = make_dialog()
    dialog._manual.set_text("SUPER + Enter")
    assert dialog._problem.get_visible()
    assert "Return" in dialog._problem.get_text()
    assert not dialog._confirm.get_sensitive()

    dialog._manual.set_text("SUPER + Return")
    assert dialog._confirm.get_sensitive()
    dialog._accept()
    assert recorded == ["SUPER + Return"]


def test_cancel_restores_shortcuts_it_inhibited() -> None:
    """A missed restore leaves the session's own keybinds dead until the app quits."""
    dialog, _ = make_dialog()
    dialog._inhibited = True  # pretend the compositor granted the inhibit
    dialog._cancel()
    assert dialog._inhibited is False


def test_restore_is_idempotent() -> None:
    dialog, _ = make_dialog()
    dialog._inhibited = True
    dialog._restore_shortcuts()
    dialog._restore_shortcuts()
    assert dialog._inhibited is False


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
