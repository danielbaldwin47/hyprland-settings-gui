"""UI smoke tier: the two ways a gesture gets taken back, in a real toolkit.

The acceptance question for #59 is "Ctrl+Z **and the toast** undo the last gesture through a
normal transaction". The *transaction* half is settled headless in
`tests/unit/test_session_undo.py`, where a scripted compositor can be asked what it was
actually told. What is left for this tier is the half no headless test can answer: that the
keystroke really is bound, that the toast really carries a button, and that both of them
reach `Session.undo` rather than some other path that happens to look similar.

So the session below is a stub over the real one -- a real `Session` (the Rows need a real
Schema and a real model to build against) with the three undo members overridden, because a
read-only session can never record a gesture and this tier has no compositor to give it one.

The toolkit imports sit inside the test functions on purpose: importing ``gi`` at module
scope would raise during collection on a machine without PyGObject, which pytest reports as
an error rather than the skip this tier is supposed to produce.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"

ROUNDING = "decoration:rounding"

ERROR_LINE = "/home/user/.config/hypr/hyprtweaker/options/general.lua:4: unknown config key"


def build_window(tmp_path: Path) -> Any:
    """A window over a session that already has one gesture to undo."""
    from gi.repository import Adw

    from hyprtweaker.engine.apply import Edit, UndoStep
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    class StubbedUndo(Session):
        """One recorded gesture, and a record of every `undo()` the window asked for."""

        undone: list[bool]

        @property
        def can_undo(self) -> bool:
            return bool(self.last_gesture)

        @property
        def last_gesture(self) -> Any:
            return UndoStep.of([Edit(ROUNDING, 8, 12)])

        def undo(self) -> bool:
            self.undone.append(True)
            return True

    Adw.init()
    session = StubbedUndo(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    session.undone = []
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def a_gesture() -> Any:
    """One recorded gesture, as `Session.on_recorded` hands it over."""
    from hyprtweaker.engine.apply import Edit, UndoStep

    return UndoStep.of([Edit(ROUNDING, 8, 12)])


def failed_result() -> Any:
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

    return ApplyResult(ApplyOutcome.CONFIG_ERRORS, keys=(ROUNDING,), errors=(ERROR_LINE,))


# --- the keystroke ----------------------------------------------------------------------------


def test_control_z_is_bound_to_the_undo_action(tmp_path: Path) -> None:
    """On the window, not on the focused control: the stack is global and linear, and a
    per-widget undo would go silent the moment focus left the Row that changed."""
    from gi.repository import Gtk

    _session, window = build_window(tmp_path)

    bound = [
        (shortcut.get_trigger().to_string(), shortcut.get_action().get_action_name())
        for controller in window.observe_controllers()
        if isinstance(controller, Gtk.ShortcutController)
        for shortcut in controller
        # GTK installs shortcuts of its own on every window, and those carry a
        # `CallbackAction` rather than a named one -- only the named ones are ours.
        if isinstance(shortcut.get_action(), Gtk.NamedAction)
    ]

    assert ("<Control>z", "win.undo") in bound


def test_the_application_advertises_the_accelerator(tmp_path: Path) -> None:
    """What puts "Ctrl+Z" beside the menu item. The controller above is what makes the
    keystroke work; this is what makes it discoverable."""
    _session, window = build_window(tmp_path)

    assert window.get_application().get_accels_for_action("win.undo") == ["<Control>z"]


def test_activating_the_action_asks_the_session_to_undo(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    window.activate_action("win.undo")

    assert session.undone == [True]


# --- the toast --------------------------------------------------------------------------------


def test_a_landed_gesture_is_offered_back(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    window.offer_undo(a_gesture())

    toast = window.undo_toast
    assert toast is not None
    assert toast.get_button_label() == "Undo"
    assert toast.get_title() == f"{session.schema[ROUNDING].title} changed"


def test_the_toasts_button_asks_the_session_to_undo(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    window.offer_undo(a_gesture())

    assert window.undo_toast is not None
    window.undo_toast.emit("button-clicked")

    assert session.undone == [True]


def test_a_second_gesture_replaces_the_offer_rather_than_stacking_one(
    tmp_path: Path,
) -> None:
    """Otherwise the button on the toast the user finally reaches belongs to the *oldest*
    gesture -- an undo that takes back something they have since changed twice."""
    _session, window = build_window(tmp_path)

    window.offer_undo(a_gesture())
    first = window.undo_toast
    window.offer_undo(a_gesture())

    assert window.undo_toast is not None
    assert window.undo_toast is not first


def test_a_failed_apply_withdraws_the_offer(tmp_path: Path) -> None:
    """One toast, never two: an offer to take back a change that did not land would be an
    offer to undo nothing."""
    _session, window = build_window(tmp_path)
    window.offer_undo(a_gesture())

    window.show_result(failed_result())

    assert window.undo_toast is None


# --- auto-revert ------------------------------------------------------------------------------


def test_an_auto_revert_toasts_and_withdraws_the_undo_offer(tmp_path: Path) -> None:
    """The gesture was never recorded (ADR-0016), so an undo offer left on screen would be
    offering to take back the gesture *before* the one the user just made."""
    from hyprtweaker.session import AutoRevert

    _session, window = build_window(tmp_path)
    window.offer_undo(a_gesture())

    window.show_revert(
        AutoRevert(keys=(ROUNDING,), modules=("options/general.lua",), errors=(ERROR_LINE,))
    )

    assert window.undo_toast is None


def test_the_error_dialog_shows_the_lines_verbatim(tmp_path: Path) -> None:
    """The `file:line` prefix is the only evidence of whose file failed, and the only part a
    user can paste into an editor's go-to-line box (ADR-0016)."""
    from gi.repository import Gtk

    from hyprtweaker.engine.apply import plan
    from hyprtweaker.ui.dialogs.errors import error_dialog

    _session, window = build_window(tmp_path)

    dialog = error_dialog(window, plan([ERROR_LINE]))

    child = dialog.get_extra_child()
    assert isinstance(child, Gtk.ScrolledWindow)
    assert _label_text(child) == ERROR_LINE


def _label_text(widget: Any) -> str | None:
    """The text of the first `GtkLabel` under `widget`.

    A walk rather than `get_child()`, because a `GtkScrolledWindow` silently wraps a
    non-scrollable child in a `GtkViewport` -- an implementation detail of the toolkit that
    a test should not be spelling out.
    """
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Label):
        return widget.get_text()
    child = widget.get_first_child()
    while child is not None:
        found = _label_text(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None
