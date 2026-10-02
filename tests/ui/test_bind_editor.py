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

from hyprtweaker.engine.dispatchers import CATALOG, Dispatcher
from hyprtweaker.engine.importer.keysyms import validator_available
from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


FREE_FORM_NOTE = "This action's arguments are not documented in a form this app can generate"

CURATED = [entry for entry in CATALOG if not entry.free_form and entry.args]
PLAIN = [entry for entry in CATALOG if not entry.free_form and not entry.args]
FREE_FORM = [entry for entry in CATALOG if entry.free_form]


def add_flow(entry: Dispatcher) -> Any:
    """The add dialog after the user picked `entry` in the Hyprland-action picker."""
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    editor = BindEditor(on_done=lambda _bind: None)
    editor._choose(entry)
    return editor


def action_group_description(editor: Any) -> str:
    """The "Action" group's description, found by walking the form page the way a reader
    sees it, not through a handle the editor keeps for itself."""
    from gi.repository import Adw, Gtk

    def walk(widget: Any) -> Any:
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from walk(child)
            child = child.get_next_sibling()

    page = editor._view.get_visible_page()
    groups = [w for w in walk(page) if isinstance(w, Adw.PreferencesGroup)]
    (action,) = [g for g in groups if g.get_title() == "Action"]
    assert isinstance(action, Gtk.Widget)
    return str(action.get_description() or "")


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


def test_a_typed_ampersand_trigger_blocks_even_without_xkb(monkeypatch: Any) -> None:
    """`A&B` fails the whole config on 0.56 whatever xkb says (#199): no validator is no
    excuse to store it enabled."""
    monkeypatch.setattr("hyprtweaker.engine.importer.binds.known_keysym", lambda _name: None)
    monkeypatch.setattr("hyprtweaker.engine.triggers.known_keysym", lambda _name: None)
    editor, saved = open_editor(
        Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="exec_cmd", positional=("x",)))
    )
    editor._trigger.set_text("SUPER + A&B")
    editor._save()

    assert saved == []
    assert editor._error.get_text() == (
        "Hyprland can't load a multi-key trigger joined with &: enabled, this keybind "
        "would stop your whole config from loading. Use a single key."
    )


@needs_xkb
def test_a_dead_key_inside_a_multi_key_trigger_blocks() -> None:
    """`validate_trigger` only warns about `SUPER + Q + notakey` (#198); the dead key in it
    still fails the config, and the one load rule catches it (#199)."""
    editor, saved = open_editor(
        Bind(keys="SUPER + Q", dispatcher=DispatcherCall(path="exec_cmd", positional=("x",)))
    )
    editor._trigger.set_text("SUPER + Q + notakey")
    editor._save()

    assert saved == []
    assert "'notakey' is not a key name xkb knows" in editor._error.get_text()


def test_a_free_form_call_keeps_its_booleans_and_quoted_numbers_through_an_edit() -> None:
    """`movetoworkspacesilent` imports as `window.move{ workspace = "3", follow = false }`.

    The raw table showed Python's `False` and an unquoted `3`, which `_coerce` read back as
    the string "False" and the integer 3: Save changed what Hyprland was told to do (#126).
    """
    call = DispatcherCall(path="window.move", args={"workspace": "3", "follow": False})
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))
    editor._save()

    assert [(b.dispatcher.args, b.dispatcher.positional) for b in saved if b.dispatcher] == [
        ({"workspace": "3", "follow": False}, ())
    ]


SAMPLE = {"string": "abc", "int": 3, "bool": True, "window": "class:foo", "workspace": "2"}


@pytest.mark.parametrize("entry", CURATED, ids=lambda e: e.path)
def test_a_curated_dispatcher_gets_one_labelled_field_per_argument(entry: Dispatcher) -> None:
    """The add flow shows a generated form, not the raw table (#127): a field per
    `ArgSpec`, titled as the catalog says, and no free-form view among them."""
    from gi.repository import Adw

    editor = add_flow(entry)

    assert editor._chosen is entry
    assert set(editor._arg_entries) == {spec.name for spec in entry.args}
    assert all(isinstance(row, Adw.EntryRow) for row in editor._arg_entries.values())
    assert {name: row.get_title() for name, row in editor._arg_entries.items()} == {
        spec.name: spec.title() for spec in entry.args
    }
    assert FREE_FORM_NOTE not in action_group_description(editor)


@pytest.mark.parametrize("entry", PLAIN, ids=lambda e: e.path)
def test_a_plain_dispatcher_says_it_takes_no_arguments(entry: Dispatcher) -> None:
    editor = add_flow(entry)

    assert editor._arg_entries == {}
    assert action_group_description(editor) == "This action takes no arguments."


@pytest.mark.parametrize("entry", FREE_FORM, ids=lambda e: e.path)
def test_a_free_form_dispatcher_keeps_the_raw_table(entry: Dispatcher) -> None:
    from gi.repository import Gtk

    editor = add_flow(entry)

    assert list(editor._arg_entries) == ["__free__"]
    assert isinstance(editor._arg_entries["__free__"], Gtk.TextView)
    assert action_group_description(editor).startswith(FREE_FORM_NOTE)


@pytest.mark.parametrize("entry", CURATED, ids=lambda e: e.path)
def test_every_key_of_a_saved_curated_call_survives_an_edit(entry: Dispatcher) -> None:
    """A curated form rebuilds the call from its `ArgSpec` names alone, so a saved key the
    entry omits is lost on Save. Open a saved call that carries every key of the entry and
    save it untouched: the call must come back equal (#126's key rule, #127)."""
    if entry.positional:
        call = DispatcherCall(path=entry.path, positional=(SAMPLE[entry.args[0].type],))
    else:
        call = DispatcherCall(
            path=entry.path, args={spec.name: SAMPLE[spec.type] for spec in entry.args}
        )
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))
    editor._save()

    assert not editor._error.get_visible(), editor._error.get_text()
    assert [b.dispatcher for b in saved] == [call]
