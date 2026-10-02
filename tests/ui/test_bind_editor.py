"""UI smoke tier: the bind editor's dead-keysym Save block on an edited bind (#108).

The block exists because one live bind on a name xkb does not know fails the whole Lua
config (ADR-0007). An imported dead-keysym bind arrives disabled, and the Writer keeps a
disabled bind commented out, so editing only its description or flags reaches nothing
that could fail; the block fires when this edit is what puts a dead keysym in play.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from typing import Any

import pytest

from hyprtweaker.engine.importer.keysyms import validator_available
from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


def dead_bind(*, enabled: bool = False) -> Bind:
    """A bind as the Importer hands over a `bind = SUPER, notakey, exec, kitty` line."""
    return Bind(
        keys="SUPER + notakey",
        dispatcher=DispatcherCall(path="exec_cmd", positional=("kitty",)),
        options=BindOptions(description="old"),
        enabled=enabled,
        origin="hyprland.conf:2",
    )


def open_editor(bind: Bind) -> tuple[Any, list[Bind]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    saved: list[Bind] = []
    return BindEditor(on_done=saved.append, bind=bind), saved


@needs_xkb
def test_an_untouched_dead_trigger_on_a_disabled_bind_saves_a_description_edit() -> None:
    editor, saved = open_editor(dead_bind())
    editor._description.set_text("open a terminal")
    editor._save()

    assert not editor._error.get_visible(), editor._error.get_text()
    assert [(b.keys, b.options.description, b.enabled, b.origin) for b in saved] == [
        ("SUPER + notakey", "open a terminal", False, "hyprland.conf:2")
    ]


@needs_xkb
def test_an_untouched_dead_trigger_on_a_disabled_bind_saves_a_flag_edit() -> None:
    editor, saved = open_editor(dead_bind())
    editor._flag_switches["repeating"].set_active(True)
    editor._save()

    assert [(b.keys, b.options.repeating, b.enabled) for b in saved] == [
        ("SUPER + notakey", True, False)
    ]


@needs_xkb
def test_a_respelled_but_unchanged_dead_trigger_still_counts_as_untouched() -> None:
    """The editor compares canonical forms: `_save` canonicalises on the way out, so a
    reordered or aliased spelling of the same trigger is not an edit to it."""
    editor, saved = open_editor(dead_bind())
    editor._trigger.set_text("win + notakey")
    editor._save()

    assert [(b.keys, b.enabled) for b in saved] == [("SUPER + notakey", False)]


@needs_xkb
def test_changing_the_trigger_to_a_dead_keysym_still_blocks() -> None:
    editor, saved = open_editor(dead_bind())
    editor._trigger.set_text("SUPER + alsonotakey")
    editor._save()

    assert saved == []
    assert editor._error.get_visible()
    assert "'alsonotakey' is not a key name xkb knows" in editor._error.get_text()


@needs_xkb
def test_fixing_a_dead_trigger_through_edit_enables_the_bind_and_says_so() -> None:
    """The Importer disabled it, not the user: a working key is the fix, as it is through
    the row's "Fix trigger…", and the line above Save keeps the enable from being quiet."""
    editor, saved = open_editor(dead_bind())
    assert not editor._enables_note.get_visible(), "untouched, it stays disabled"

    editor._trigger.set_text("SUPER + q")
    assert editor._enables_note.get_visible()
    assert (
        editor._enables_note.get_text() == "Saving with a working key also enables this bind."
    )
    editor._save()

    assert [(b.keys, b.enabled) for b in saved] == [("SUPER + q", True)]


@needs_xkb
def test_a_bind_disabled_with_a_working_trigger_stays_disabled_after_a_new_one() -> None:
    """The conflict surface's disable is the user's choice; Edit does not undo it."""
    editor, saved = open_editor(
        Bind(
            keys="SUPER + w",
            dispatcher=DispatcherCall(path="exec_cmd", positional=("kitty",)),
            enabled=False,
        )
    )
    editor._trigger.set_text("SUPER + q")
    assert not editor._enables_note.get_visible()
    editor._save()

    assert [(b.keys, b.enabled) for b in saved] == [("SUPER + q", False)]


@needs_xkb
def test_an_enabled_bind_with_a_dead_trigger_still_blocks() -> None:
    """Saving it would write a live dead-keysym bind, which takes the whole config down;
    only a disabled bind is kept out of the compositor's reach by the Writer."""
    editor, saved = open_editor(dead_bind(enabled=True))
    editor._description.set_text("open a terminal")
    editor._save()

    assert saved == []
    assert "'notakey' is not a key name xkb knows" in editor._error.get_text()
