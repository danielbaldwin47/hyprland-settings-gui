"""UI smoke tier: a change a hand-edited Module refused is said, not claimed (#148 d13).

The session half (refused before the model moves, no undo step, nothing held) is in
`tests/unit/test_session_hand_edits.py`. This tier checks what the user reads: the toast,
the Banner and its button, and the dialog behind both, one per edited file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from _live_window import live_entity_window

BINDS = "binds.lua"


def held_back_bind(tmp_path: Path) -> tuple[Any, Any]:
    """A live window whose last bind edit came back with `binds.lua` skipped as hand-edited:
    the Writer's backstop, for a file edited between the gate and the write."""
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult
    from hyprtweaker.engine.model import Bind, DispatcherCall
    from hyprtweaker.engine.writer import WriteResult

    session, window, applier = live_entity_window(tmp_path)
    session.on_refused = window.show_refused
    session.add_bind(
        Bind(keys="SUPER + B", dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))
    )
    write = WriteResult(
        written=(),
        unchanged=(),
        removed=(),
        entrypoint_written=False,
        hand_edited=(BINDS,),
        skipped=(BINDS,),
    )
    applier.reported = applier.serial
    session._applied(
        ApplyResult(ApplyOutcome.NOTHING_TO_DO, write=write, entities=applier.serial)
    )
    return session, window


def test_the_toast_says_the_change_was_not_saved_and_offers_no_undo(tmp_path: Path) -> None:
    session, window = held_back_bind(tmp_path)

    assert session.model.entities.binds == []
    assert window.undo_toast is None
    assert window._banner.get_revealed()
    assert window._banner.get_title() == (
        "binds.lua was edited outside this app, so changes to it are not saved."
    )
    assert window._banner.get_button_label() == "Details"


def test_details_names_the_change_and_offers_keep_open_and_replace(tmp_path: Path) -> None:
    _session, window = held_back_bind(tmp_path)

    dialog = window.show_edited_file(BINDS, "Keybind added")

    assert dialog.get_heading() == "binds.lua was edited outside this app"
    body = dialog.get_body()
    assert "Keybind added was not saved." in body
    assert "then make the change again" in body
    assert "replacing keeps a copy of your edited file in" in body
    assert "edited-copies" in body
    assert [dialog.get_response_label(r) for r in ("keep", "open", "replace")] == [
        "Keep my file",
        "Open file",
        "Replace file",
    ]
    assert dialog.get_default_response() == "keep"
    dialog.close()


def test_the_toast_names_the_change_and_the_file(tmp_path: Path) -> None:
    session, window, _applier = live_entity_window(tmp_path)

    toast = window.show_refused("Corner rounding", "options/decoration.lua")

    assert toast.get_title() == (
        "Corner rounding was not saved: decoration.lua was edited outside this app"
    )
    assert toast.get_button_label() == "Details"
    assert session.can_undo is False


def test_the_banner_offers_each_edited_file_in_turn_and_keep_lets_it_go(
    tmp_path: Path,
) -> None:
    """R5 of the #148 review: the Banner's Details always opened the first file, and after
    Keep my file nothing led to the second file's choices."""
    import main_loop

    session, window, _applier = live_entity_window(tmp_path)
    session._edited_files = ["options/decoration.lua", "options/general.lua"]
    session._edited_module = lambda modules: next(iter(modules), None)
    window.sync_banner()

    window._on_banner_clicked(window._banner)
    first = window.get_visible_dialog()
    assert first.get_heading() == "decoration.lua was edited outside this app"
    first.emit("response", "keep")
    first.force_close()
    main_loop.settle("the next file to be offered")

    second = window.get_visible_dialog()
    assert second.get_heading() == "general.lua was edited outside this app"
    assert session.health.edited_files == ("options/general.lua",)
    second.force_close()
