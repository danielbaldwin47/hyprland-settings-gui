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

from hyprtweaker.engine.dispatchers import CATALOG, Dispatcher, lookup
from hyprtweaker.engine.importer.keysyms import validator_available
from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall
from hyprtweaker.engine.writer.binds import render_dispatcher

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


FREE_FORM_NOTE = "Type each setting as key = value, one per line."

CURATED = [entry for entry in CATALOG if entry.free_form_reason is None and entry.args]
PLAIN = [entry for entry in CATALOG if entry.free_form_reason is None and not entry.args]
FREE_FORM = [entry for entry in CATALOG if entry.free_form_reason is not None]


def add_flow(entry: Dispatcher | None) -> Any:
    """The add dialog after the user picked `entry` in the Hyprland-action picker."""
    return add_flow_saving(entry)[0]


def add_flow_saving(entry: Dispatcher | None) -> tuple[Any, list[Bind]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.bind_editor import BindEditor

    Adw.init()
    saved: list[Bind] = []
    editor = BindEditor(on_done=saved.append)
    editor._choose(entry)
    return editor, saved


def action_group_description(editor: Any) -> str:
    """The "Action" group's description, found by walking the form page the way a reader
    sees it, not through a handle the editor keeps for itself."""
    from gi.repository import Gtk

    (action,) = groups_titled(editor, "Action")
    assert isinstance(action, Gtk.Widget)
    return str(action.get_description() or "")


def walk(widget: Any) -> Any:
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def groups_titled(editor: Any, title: str) -> list[Any]:
    """The form page's preferences groups with this title, as a reader finds them."""
    from gi.repository import Adw

    page = editor._view.get_visible_page()
    return [
        w for w in walk(page) if isinstance(w, Adw.PreferencesGroup) and w.get_title() == title
    ]


def kept_lines(editor: Any) -> list[str]:
    """The rows of the "Also kept from your config" group, top to bottom; [] without it."""
    from gi.repository import Adw

    groups = groups_titled(editor, "Also kept from your config")
    if not groups:
        return []
    (group,) = groups
    return [str(w.get_title()) for w in walk(group) if isinstance(w, Adw.ActionRow)]


def saved_lua(saved: list[Bind]) -> list[str]:
    """What the Writer emits for each saved bind's action: the text Hyprland is told."""
    return [render_dispatcher(b.dispatcher) for b in saved if b.dispatcher]


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
    Compared as the emitted Lua, because `False == 0` and `1 == 1.0` in Python.
    """
    call = DispatcherCall(
        path="window.move",
        args={"workspace": "3", "follow": False, "on": True, "x": 3, "y": 1.5},
    )
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))
    editor._save()

    assert saved_lua(saved) == [
        'hl.dsp.window.move{ workspace = "3", follow = false, on = true, x = 3, y = 1.5 }'
    ]


def test_a_number_with_a_decimal_point_typed_in_the_raw_table_saves_as_a_number() -> None:
    call = DispatcherCall(path="window.resize", args={"x": 10, "y": 20})
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))
    editor._arg_entries["__free__"].get_buffer().set_text('x = 10.5\ny = -2.25\nz = "1.5"')
    editor._save()

    assert saved_lua(saved) == ['hl.dsp.window.resize{ x = 10.5, y = -2.25, z = "1.5" }']


def test_a_table_value_in_a_free_form_call_is_kept_and_shown_read_only() -> None:
    """The raw table cannot spell a nested table back, so it is listed under the form as
    Lua and saved unchanged, never as Python's repr."""
    call = DispatcherCall(path="window.move", args={"x": 1, "extra": {"a": 1, "b": "c"}})
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))
    buffer = editor._arg_entries["__free__"].get_buffer()

    assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False) == "x = 1"
    assert kept_lines(editor) == ['extra = { a = 1, b = "c" }']
    editor._save()
    assert saved_lua(saved) == ['hl.dsp.window.move{ x = 1, extra = { a = 1, b = "c" } }']


def test_the_raw_table_says_what_the_action_takes_in_the_users_words() -> None:
    editor = add_flow(lookup("focus"))

    assert action_group_description(editor) == (
        "Give exactly one of: direction, monitor, workspace, window, urgent_or_last or "
        "last. Type each setting as key = value, one per line."
    )


def test_an_unconfirmed_action_says_why_it_has_no_form() -> None:
    editor = add_flow(lookup("group.lock"))

    assert action_group_description(editor) == (
        "Hyprland does not say which settings this action reads, so the app has no form "
        "for it. Type each setting as key = value, one per line."
    )


def test_an_action_this_version_does_not_know_keeps_its_raw_table() -> None:
    call = DispatcherCall(path="plugin.thing", args={"speed": 2})
    editor, saved = open_editor(Bind(keys="SUPER + w", dispatcher=call))

    assert action_group_description(editor) == (
        "This version of the app does not know this action. "
        "Type each setting as key = value, one per line."
    )
    editor._save()
    assert saved_lua(saved) == ["hl.dsp.plugin.thing{ speed = 2 }"]


def cycle_next(**args: object) -> tuple[Any, list[Bind]]:
    return open_editor(
        Bind(keys="SUPER + w", dispatcher=DispatcherCall(path="window.cycle_next", args=args))
    )


def test_a_yes_or_no_field_refuses_a_word_it_cannot_read() -> None:
    """`forward` once saved as `next = false`: the opposite of what was typed."""
    editor, saved = cycle_next()
    editor._arg_entries["next"].set_text("forward")
    editor._save()

    assert saved == []
    assert editor._error.get_text() == "Forwards must be true or false."


def test_a_whole_number_field_refuses_a_fraction() -> None:
    editor, saved = add_flow_saving(lookup("force_idle"))
    editor._trigger.set_text("SUPER + i")
    editor._arg_entries["seconds"].set_text("2.5")
    editor._save()

    assert saved == []
    assert editor._error.get_text() == "Seconds must be a whole number."


def test_a_saved_yes_or_no_reads_as_true_or_false_and_saves_back() -> None:
    editor, saved = cycle_next(next=False, tiled=True)

    assert editor._arg_entries["next"].get_text() == "false"
    assert editor._arg_entries["tiled"].get_text() == "true"
    editor._arg_entries["floating"].set_text("True")
    editor._save()
    assert saved_lua(saved) == [
        "hl.dsp.window.cycle_next{ next = false, tiled = true, floating = true }"
    ]


def fullscreen_state_bind() -> Bind:
    """`layout_aware` is hand-written: the curated form has no row for it (#126 owner call
    4, decided 2026-10-02)."""
    return Bind(
        keys="SUPER + f",
        dispatcher=DispatcherCall(
            path="window.fullscreen_state",
            args={"internal": 2, "client": 0, "layout_aware": True},
        ),
    )


def test_a_key_the_form_does_not_show_is_kept_and_listed() -> None:
    editor, saved = open_editor(fullscreen_state_bind())

    assert kept_lines(editor) == ["layout_aware = true"]
    editor._save()
    assert saved_lua(saved) == [
        "hl.dsp.window.fullscreen_state{ internal = 2, client = 0, layout_aware = true }"
    ]


def test_picking_another_action_drops_the_keys_of_the_old_one() -> None:
    editor, saved = open_editor(fullscreen_state_bind())
    editor._choose(lookup("window.close"))

    assert kept_lines(editor) == []
    editor._save()
    assert saved_lua(saved) == ["hl.dsp.window.close()"]


def test_a_form_with_nothing_extra_shows_no_kept_group() -> None:
    editor, _saved = cycle_next(next=True)

    assert kept_lines(editor) == []


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
    assert action_group_description(editor) == f"{entry.free_form_reason} {FREE_FORM_NOTE}"


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


# --- #105: click, drag and auto_consuming, with ADR-0007's constraints in the form --------


def term_bind(**flags: bool) -> Bind:
    """A saved bind on a working key, as the editor opens it, with these flags on."""
    return Bind(
        keys="SUPER + T",
        dispatcher=DispatcherCall(path="exec_cmd", positional=("kitty",)),
        options=BindOptions(**flags),
    )


def saved_options(bind: Bind, *flip: str) -> tuple[Any, list[Bind]]:
    """Open `bind`, turn each flag in `flip` on, save; the editor and what it handed back."""
    editor, saved = open_editor(bind)
    for name in flip:
        editor._flag_switches[name].set_active(True)
    editor._save()
    return editor, saved


def refusal(editor: Any) -> str:
    return str(editor._error.get_text()) if editor._error.get_visible() else ""


def test_the_auto_consuming_switch_says_what_the_flag_does() -> None:
    """Hyprland 0.56.2 `KeybindManager.cpp`: an auto-consuming bind keeps the key from the
    app only when its dispatcher succeeds (#151 review, finding 31)."""
    editor, _ = open_editor(term_bind())
    row = editor._flag_switches["auto_consuming"]

    assert (row.get_title(), row.get_subtitle()) == (
        "Lets the key through if the action fails",
        "The app gets the key when the action could not run",
    )


@pytest.mark.parametrize("name", ["click", "drag", "auto_consuming"])
def test_each_new_flag_has_a_switch_and_reaches_the_saved_bind(name: str) -> None:
    editor, saved = saved_options(term_bind(), name)

    assert refusal(editor) == ""
    assert [getattr(b.options, name) for b in saved] == [True]


@pytest.mark.parametrize("name", ["click", "drag"])
def test_click_and_drag_save_release_without_the_user_setting_it(name: str) -> None:
    editor, saved = saved_options(term_bind(), name)

    assert not editor._error.get_visible()
    assert [(b.options.release, getattr(b.options, name)) for b in saved] == [(True, True)]


@pytest.mark.parametrize(("name", "caption"), [("click", "Click"), ("drag", "Drag")])
def test_the_release_switch_shows_on_and_locked_while_click_or_drag_is_on(
    name: str, caption: str
) -> None:
    editor, _ = open_editor(term_bind())
    release = editor._flag_switches["release"]
    assert (release.get_active(), release.get_sensitive(), release.get_subtitle()) == (
        False,
        True,
        "",
    )

    editor._flag_switches[name].set_active(True)
    assert (release.get_active(), release.get_sensitive(), release.get_subtitle()) == (
        True,
        False,
        f"Set by {caption}",
    )


def test_turning_click_off_gives_release_back_to_the_users_own_value() -> None:
    editor, saved = open_editor(term_bind())
    release, click = editor._flag_switches["release"], editor._flag_switches["click"]

    click.set_active(True)
    click.set_active(False)
    assert (release.get_active(), release.get_sensitive(), release.get_subtitle()) == (
        False,
        True,
        "",
    )
    editor._save()
    assert [(b.options.release, b.options.click) for b in saved] == [(False, False)]


def test_a_release_the_user_set_survives_click_going_on_and_off() -> None:
    editor, saved = open_editor(term_bind(release=True))
    click = editor._flag_switches["click"]

    click.set_active(True)
    click.set_active(False)

    assert editor._flag_switches["release"].get_active()
    editor._save()
    assert [(b.options.release, b.options.click) for b in saved] == [(True, False)]


def test_an_imported_click_bind_opens_with_release_locked_on_and_saves_unchanged() -> None:
    """The Importer sets release on every click bind, so release is not the user's own."""
    editor, saved = open_editor(term_bind(click=True, release=True))
    release = editor._flag_switches["release"]

    assert (release.get_active(), release.get_sensitive(), release.get_subtitle()) == (
        True,
        False,
        "Set by Click",
    )
    editor._save()
    assert [b.options for b in saved] == [BindOptions(click=True, release=True)]


def test_turning_an_imported_click_off_does_not_leave_a_release_the_user_never_chose() -> None:
    editor, saved = open_editor(term_bind(click=True, release=True))
    editor._flag_switches["click"].set_active(False)

    assert not editor._flag_switches["release"].get_active()
    editor._save()
    assert [b.options for b in saved] == [BindOptions()]


@pytest.mark.parametrize(
    ("flags", "message"),
    [
        (("click", "drag"), "Click and Drag can't both be on."),
        (("click", "repeating"), "Click fires on release, so it can't repeat."),
        (("drag", "repeating"), "Drag fires on release, so it can't repeat."),
        (("long_press", "repeating"), "Long press can't repeat."),
        (("release", "repeating"), "Release can't repeat."),
    ],
)
def test_an_invalid_flag_combination_is_refused_in_plain_words_and_not_saved(
    flags: tuple[str, ...], message: str
) -> None:
    editor, saved = saved_options(term_bind(), *flags)

    assert refusal(editor) == message
    assert saved == []


def test_a_flag_conflict_shows_as_the_switch_flips_and_clears_when_resolved() -> None:
    """#151 review, finding 41 (owner call 10 overturned): the refusal shows where the
    user acts, not only once Save is pressed."""
    editor, saved = open_editor(term_bind())
    editor._flag_switches["click"].set_active(True)
    assert refusal(editor) == ""

    editor._flag_switches["repeating"].set_active(True)
    assert refusal(editor) == "Click fires on release, so it can't repeat."

    editor._flag_switches["repeating"].set_active(False)
    assert not editor._error.get_visible()
    assert saved == []


def test_an_imported_conflict_shows_as_the_editor_opens() -> None:
    editor, _ = open_editor(term_bind(long_press=True, repeating=True))

    assert refusal(editor) == "Long press can't repeat."


def test_flipping_a_flag_leaves_a_save_refusal_about_something_else() -> None:
    editor, _ = open_editor(term_bind())
    editor._trigger.set_text("")
    editor._save()
    assert refusal(editor) == "A keybind needs a trigger."

    editor._flag_switches["locked"].set_active(True)

    assert refusal(editor) == "A keybind needs a trigger."


def test_an_imported_click_bind_that_repeats_is_refused_naming_click_not_release() -> None:
    editor, saved = saved_options(term_bind(click=True, release=True), "repeating")

    assert refusal(editor) == "Click fires on release, so it can't repeat."
    assert saved == []


@pytest.mark.parametrize(
    "flags", [("click", "long_press"), ("auto_consuming", "non_consuming")]
)
def test_pairs_hyprland_loads_are_not_refused(flags: tuple[str, ...]) -> None:
    """`Hyprland --verify-config` accepts both pairs (probed on 0.56.2, #105)."""
    editor, saved = saved_options(term_bind(), *flags)

    assert refusal(editor) == ""
    assert len(saved) == 1


def test_a_bind_device_survives_a_click_edit() -> None:
    from hyprtweaker.engine.model.entities import BindDevice

    device = BindDevice(inclusive=False, names=("kbd",))
    bind = Bind(
        keys="SUPER + T",
        dispatcher=DispatcherCall(path="exec_cmd", positional=("kitty",)),
        options=BindOptions(device=device),
    )
    _, saved = saved_options(bind, "click")

    assert [b.options.device for b in saved] == [device]
