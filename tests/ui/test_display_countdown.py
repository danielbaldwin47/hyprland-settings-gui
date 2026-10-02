"""UI tier: one Confirm-or-revert countdown for every display-breaking change (#192).

ADR-0008 batches display-breaking edits: those arriving within the debounce window land as
one patch, one transaction and one dialog, and a later breaking change -- another edit, a
profile activation, an undo -- joins the open countdown and restarts its clock rather than
stacking a second dialog. Revert puts the display back and keeps benign edits made while
the clock ran. The session is real and live (`live_entity_window`), so the undo stack these
tests read is the one Ctrl+Z walks; `present` is recorded rather than shown, so "one
dialog" is counted, not looked for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _live_window import live_entity_window

MONITORS: tuple[dict[str, Any], ...] = (
    {
        "name": "eDP-1",
        "description": "BOE 0x0791",
        "width": 1920,
        "height": 1080,
        "refreshRate": 60.0,
        "x": 0,
        "y": 0,
        "scale": 1.0,
        "transform": 0,
        "availableModes": ["1920x1080@60.00Hz", "1920x1080@48.00Hz"],
    },
)


def countdown_window(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Any, list[Any]]:
    from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog

    session, window, applier = live_entity_window(tmp_path)
    shown: list[Any] = []
    monkeypatch.setattr(
        ConfirmRevertDialog, "present", lambda self, _parent=None: shown.append(self)
    )
    return session, window, applier, shown


def rules(session: Any) -> list[tuple[str, dict[str, Any]]]:
    return [(rule.output, dict(rule.fields)) for rule in session.monitor_rules]


def breaking(window: Any, applier: Any, output: str, fields: dict[str, Any]) -> None:
    """One breaking edit, its debounce run out, its transaction reported."""
    window._apply_monitor_breaking(output, fields)
    window.flush_monitor_edits()
    applier.settle()


def test_three_quick_breaking_edits_are_one_transaction_and_one_dialog(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)

    window._apply_monitor_breaking("eDP-1", {"mode": "1920x1080@60"})
    window._apply_monitor_breaking("eDP-1", {"scale": 1.5})
    window._apply_monitor_breaking("eDP-1", {"mode": "1920x1080@48"})
    assert shown == []
    assert rules(session) == [], "an edit applied before its debounce ran out"

    window.flush_monitor_edits()

    assert applier.serial == 1
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@48", "scale": 1.5})]
    assert len(shown) == 1
    assert window.display_confirm is shown[0]


def test_the_debounce_runs_out_by_itself(tmp_path: Path, monkeypatch: Any) -> None:
    import time

    from gi.repository import GLib

    session, window, _applier, shown = countdown_window(tmp_path, monkeypatch)

    window._apply_monitor_breaking("eDP-1", {"scale": 2})
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 3
    while not shown and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)

    assert len(shown) == 1
    assert rules(session) == [("eDP-1", {"scale": 2})]


def test_a_breaking_edit_joins_the_open_countdown_and_revert_restores_the_start(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)
    breaking(window, applier, "eDP-1", {"mode": "1920x1080@60"})
    (dialog,) = shown
    for _ in range(5):
        dialog.tick()
    assert dialog.remaining == 10

    breaking(window, applier, "DP-3", {"position": "1920x0"})

    assert shown == [dialog]
    assert dialog.remaining == 15
    assert "in 15 s" in dialog.get_body()

    dialog._on_response(dialog, "revert")
    applier.settle()
    assert rules(session) == []
    assert session.last_gesture is None
    assert window.display_confirm is None


def test_keep_leaves_one_step_and_ctrl_z_restores_the_rules_before_the_batch(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60", "vrr": 0})
    applier.settle()
    breaking(window, applier, "eDP-1", {"mode": "1920x1080@48"})
    breaking(window, applier, "eDP-1", {"scale": 2})
    (dialog,) = shown

    dialog._on_response(dialog, "keep")

    assert window.undo_toast is not None
    assert window.undo_toast.get_title() == "Display settings changed"
    window.activate_action("win.undo")
    applier.settle()
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60", "vrr": 0})]
    # Putting a mode back can black-screen as surely as choosing one: the undo stands
    # behind a countdown of its own, and keeping it records nothing new.
    assert len(shown) == 2
    shown[1]._on_response(shown[1], "keep")
    assert session.last_gesture is not None
    assert session.last_gesture.title == "Monitor rule changed"


def test_reverting_an_undo_records_a_step_so_ctrl_z_can_try_again(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    applier.settle()

    window.activate_action("win.undo")
    applier.settle()
    assert rules(session) == []
    (dialog,) = shown
    dialog._on_response(dialog, "revert")
    applier.settle()

    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60"})]
    assert session.last_gesture is not None
    window.activate_action("win.undo")
    applier.settle()
    assert rules(session) == []
    assert len(shown) == 2


def test_a_benign_edit_mid_countdown_applies_at_once_and_survives_revert(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60", "vrr": 0})
    applier.settle()
    toast = window.undo_toast
    breaking(window, applier, "eDP-1", {"mode": "1920x1080@48"})
    (dialog,) = shown

    window._apply_monitor_benign("eDP-1", {"vrr": 1})
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@48", "vrr": 1})]
    applier.settle()
    assert shown == [dialog]
    assert window.undo_toast is toast, "a held benign step raised a toast mid-countdown"

    dialog._on_response(dialog, "revert")
    applier.settle()

    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60", "vrr": 1})]
    assert session.last_gesture is not None
    assert session.last_gesture.title == "Monitor rule changed"
    window.activate_action("win.undo")
    applier.settle()
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60", "vrr": 0})]
    assert shown == [dialog], "undoing a benign edit opened a countdown"


def profile_session(tmp_path: Path, monkeypatch: Any) -> tuple[Any, Any, Any, list[Any], str]:
    """A profile at @60, and the display at @50 when the test starts."""
    session, window, applier, shown = countdown_window(tmp_path, monkeypatch)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    slug = session.save_monitor_profile("Docked", MONITORS)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@50"})
    applier.settle()
    return session, window, applier, shown, slug


def test_activating_a_profile_joins_the_open_countdown_and_revert_puts_all_back(
    tmp_path: Path, monkeypatch: Any
) -> None:
    session, window, applier, shown, slug = profile_session(tmp_path, monkeypatch)
    breaking(window, applier, "eDP-1", {"scale": 2})
    (dialog,) = shown
    for _ in range(3):
        dialog.tick()

    window._activate_monitor_profile(slug)
    applier.settle()

    assert shown == [dialog]
    assert dialog.remaining == 15
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60"})]
    active = session.active_monitor_profile()
    assert active is not None and active[0] == slug

    dialog._on_response(dialog, "revert")
    applier.settle()
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@50"})]
    assert session.active_monitor_profile() is None


def test_a_breaking_edit_joins_a_profile_countdown(tmp_path: Path, monkeypatch: Any) -> None:
    session, window, applier, shown, slug = profile_session(tmp_path, monkeypatch)
    window._activate_monitor_profile(slug)
    applier.settle()
    (dialog,) = shown
    for _ in range(4):
        dialog.tick()

    breaking(window, applier, "eDP-1", {"scale": 2})

    assert shown == [dialog]
    assert dialog.remaining == 15
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@60", "scale": 2})]
    dialog._on_response(dialog, "revert")
    applier.settle()
    assert rules(session) == [("eDP-1", {"mode": "1920x1080@50"})]
    assert session.active_monitor_profile() is None


def test_restart_gives_the_countdown_back_in_full() -> None:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog

    Adw.init()
    dialog = ConfirmRevertDialog(on_keep=lambda: None, on_revert=lambda: None, seconds=5)
    dialog.tick()
    dialog.tick()
    assert dialog.remaining == 3

    dialog.restart()

    assert dialog.remaining == 5
    assert "in 5 s" in dialog.get_body()
    dialog.tick()
    assert dialog.remaining == 4
