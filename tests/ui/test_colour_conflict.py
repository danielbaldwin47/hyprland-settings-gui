"""UI smoke tier: the colour-owner question when a Preset is applied (#170, ADR-0014).

The engine half -- what each answer does to the Color source, the transaction and undo -- is
settled headless in `tests/unit/test_session_preset_colours.py`. This tier answers what only
a toolkit can: that the window asks exactly when the session says the colours conflict, that
the dialog's buttons and check say what they do, and that "Remember my choice" is stored
through the window's one prefs path and stops the next question, across a restart too.

The session is a real `Session` with `apply_preset` and `presets` overridden: a read-only
session cannot apply, and this tier has no compositor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from started_app import started_application

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> tuple[Any, Any]:
    """A window over a session where a wallpaper (matugen) sets the colours."""
    from gi.repository import Adw

    from hyprtweaker.engine.bridge import Wallpaper
    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.presets import (
        CaptureScope,
        ColorChoice,
        Preset,
        PresetApplied,
        PresetApplyResult,
        PresetColorConflict,
    )
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    nord = Preset(
        name="Nord",
        created=datetime(2026, 10, 2, tzinfo=UTC),
        scopes=frozenset({CaptureScope.COLORS}),
        options={"general:col.inactive_border": "ee33ccff"},
        app_version=APP_VERSION,
        hyprland_version="0.56.2",
    )

    class UnderMatugen(Session):
        applied: list[tuple[str, ColorChoice | None, bool]]

        def presets(self) -> tuple[tuple[str, Preset], ...]:
            return (("nord", nord),)

        def apply_preset(
            self, slug: str, *, colors: ColorChoice | None = None, wallpaper: bool = False
        ) -> PresetApplyResult:
            self.applied.append((slug, colors, wallpaper))
            if colors is None:
                return PresetColorConflict(Wallpaper("matugen"))
            return PresetApplied(applied=("general:col.inactive_border",), skipped=())

    Adw.init()
    session = UnderMatugen(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    session.applied = []
    app = started_application()
    return session, MainWindow(session, application=app)


def test_a_conflict_asks_with_two_answers_and_a_remember_check(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)

    window.apply_preset("nord", wallpaper=True)

    dialog = window.colour_conflict
    assert dialog is not None
    assert dialog.get_heading() == "Use Nord's colors?"
    assert dialog.get_body() == (
        "matugen sets your colors from the wallpaper. Using Nord's colors pauses matugen "
        "until you choose it again on the Theming page. One Ctrl+Z puts everything back."
    )
    assert dialog.get_response_label("use") == "Use preset's colors"
    assert dialog.get_response_label("keep") == "Keep wallpaper colors"
    assert dialog.get_default_response() == "use"
    assert dialog.remember.get_label() == "Remember my choice"
    assert dialog.remember.get_active() is False
    assert session.applied == [("nord", None, True)]


def test_an_answer_applies_and_remember_stops_the_next_question_across_a_restart(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.presets import ColorChoice

    session, window = build_window(tmp_path)
    window.apply_preset("nord", wallpaper=True)
    dialog = window.colour_conflict
    dialog.remember.set_active(True)

    dialog.emit("response", "keep")

    assert session.applied[-1] == ("nord", ColorChoice.KEEP_WALLPAPER, True)
    assert dict(window._prefs.remembered) == {"preset-colors": "keep-wallpaper"}
    assert window.lookup_action("forget-remembered").get_enabled() is True

    session.applied.clear()
    window.apply_preset("nord")
    assert session.applied == [("nord", ColorChoice.KEEP_WALLPAPER, False)]

    restarted_session, restarted = build_window(tmp_path)
    restarted.apply_preset("nord")
    assert restarted.colour_conflict is None
    assert restarted_session.applied == [("nord", ColorChoice.KEEP_WALLPAPER, False)]


def test_an_answer_without_remember_asks_again_and_escape_applies_nothing(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.presets import ColorChoice

    session, window = build_window(tmp_path)
    window.apply_preset("nord")
    window.colour_conflict.emit("response", "use")
    assert session.applied[-1] == ("nord", ColorChoice.USE_PRESET, False)
    assert dict(window._prefs.remembered) == {}

    session.applied.clear()
    window.apply_preset("nord")
    window.colour_conflict.emit("response", "cancel")

    assert session.applied == [("nord", None, False)]


def test_forgetting_remembered_choices_brings_the_question_back(tmp_path: Path) -> None:
    session, window = build_window(tmp_path)
    window.apply_preset("nord")
    window.colour_conflict.remember.set_active(True)
    window.colour_conflict.emit("response", "use")

    window.activate_action("win.forget-remembered", None)
    session.applied.clear()
    window.apply_preset("nord")

    assert session.applied == [("nord", None, False)]
    assert window.colour_conflict is not None


def test_the_embeddable_choice_reads_its_answer(tmp_path: Path) -> None:
    from gi.repository import Adw

    from hyprtweaker.engine.bridge import Several
    from hyprtweaker.engine.presets import ColorChoice
    from hyprtweaker.ui.dialogs.colour_conflict import ColourConflictChoice

    Adw.init()
    choice = ColourConflictChoice("Nord", Several(("matugen", "wallust")))
    assert choice.choice is ColorChoice.USE_PRESET

    choice.keep.set_active(True)
    choice.remember.set_active(True)

    assert choice.choice is ColorChoice.KEEP_WALLPAPER
    assert choice.remember.get_active() is True
