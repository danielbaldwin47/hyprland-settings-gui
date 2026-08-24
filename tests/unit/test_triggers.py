"""The Trigger grammar and the Capture state machine (#65, ADR-0007).

Toolkit-free, so all of it runs without a display -- which is the point of keeping the
recorder out of the dialog.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine.importer.keysyms import known_keysym, validator_available
from hyprtweaker.engine.triggers import (
    CATCHALL,
    CaptureRecorder,
    Severity,
    Trigger,
    button_token,
    format_trigger,
    normalise_keysym,
    parse_trigger,
    validate_trigger,
    wheel_token,
)

needs_xkb = pytest.mark.skipif(
    not validator_available(), reason="libxkbcommon is not loadable here"
)


# --- formatting -----------------------------------------------------------------------


def test_modifiers_emit_in_canonical_order_regardless_of_press_order() -> None:
    """Two captures of one chord must produce the same string, or goldens are noise."""
    assert format_trigger(["SUPER", "SHIFT"], "Q") == "SHIFT + SUPER + Q"
    assert format_trigger(["SHIFT", "SUPER"], "Q") == "SHIFT + SUPER + Q"


def test_format_deduplicates_modifiers() -> None:
    assert format_trigger(["SUPER", "SUPER"], "Q") == "SUPER + Q"


def test_format_without_key_is_modifiers_only() -> None:
    assert format_trigger(["CTRL"], "") == "CTRL"


@pytest.mark.parametrize(
    ("text", "mods", "key"),
    [
        ("SUPER + Q", ("SUPER",), "Q"),
        ("super + q", ("SUPER",), "q"),
        ("CONTROL + ALT + Delete", ("CTRL", "ALT"), "Delete"),
        ("MOD1 + Tab", ("ALT",), "Tab"),
        ("WIN+SHIFT+F1", ("SHIFT", "SUPER"), "F1"),
        ("  SUPER  +  Q  ", ("SUPER",), "Q"),
        ("Q", (), "Q"),
        ("catchall", (), "catchall"),
        ("SUPER + mouse:272", ("SUPER",), "mouse:272"),
        ("mouse_down", (), "mouse_down"),
        ("code:36", (), "code:36"),
    ],
)
def test_parse_trigger(text: str, mods: tuple[str, ...], key: str) -> None:
    trigger = parse_trigger(text)
    assert (trigger.mods, trigger.key) == (mods, key)


def test_aliases_normalise_to_canonical_spelling() -> None:
    """LOGO/WIN/MOD4/META are all SUPER, so the writer emits one spelling."""
    for alias in ("WIN", "LOGO", "MOD4", "META", "SUPER"):
        assert parse_trigger(f"{alias} + Q").mods == ("SUPER",)


def test_modifier_after_a_key_is_not_a_modifier() -> None:
    """`parseKeyString` requires modifiers to precede keys, so this is two keys."""
    trigger = parse_trigger("Q + SHIFT")
    assert trigger.mods == ()
    assert trigger.key == "Q + SHIFT"


def test_keycode_display_reads_as_words() -> None:
    assert Trigger(("SUPER",), "code:36").display() == "SUPER + key code 36"
    assert Trigger(("SUPER",), "Q").display() == "SUPER + Q"


def test_trigger_round_trips_through_its_string() -> None:
    for text in ("SHIFT + SUPER + Q", "catchall", "mouse_up", "CTRL + code:36"):
        assert str(parse_trigger(text)) == text


# --- validation -----------------------------------------------------------------------


@needs_xkb
@pytest.mark.parametrize(
    "text",
    [
        "SUPER + Q",
        "SUPER + SHIFT + Return",
        "escape",  # xkb resolves case-insensitively, and so does Hyprland
        "SPACE",
        "print",
        "XF86AudioRaiseVolume",
        "SUPER + mouse:272",
        "mouse_down",
        "SUPER + mouse_up",
        "code:36",
        "switch:on:Lid Switch",
        "SUPER + F1",
    ],
)
def test_valid_triggers_pass(text: str) -> None:
    assert validate_trigger(text) is None


@needs_xkb
def test_dead_keysym_is_blocked() -> None:
    """The whole reason Capture validates: 0.56.2 accepts this bind and never fires it."""
    problem = validate_trigger("SUPER + notakey")
    assert problem is not None
    assert problem.blocking
    assert "notakey" in problem.message


@needs_xkb
def test_dead_keysym_suggests_the_name_that_was_meant() -> None:
    problem = validate_trigger("SUPER + Enter")
    assert problem is not None and problem.blocking
    assert "Return" in problem.hint


@needs_xkb
def test_case_insensitive_names_are_not_called_dead() -> None:
    """A GDK-based check would reject all three; Hyprland accepts all three."""
    for text in ("escape", "SPACE", "print"):
        assert validate_trigger(text) is None, text


def test_empty_is_blocked() -> None:
    problem = validate_trigger("   ")
    assert problem is not None and problem.blocking


@needs_xkb
def test_validation_accepts_a_parsed_trigger_directly() -> None:
    """The dialog holds a Trigger already; it should not have to round-trip a string."""
    assert validate_trigger(Trigger(("SUPER",), "Q")) is None
    dead = validate_trigger(Trigger(("SUPER",), "notakey"))
    assert dead is not None and dead.blocking
    assert validate_trigger(Trigger()) is not None


def test_string_and_trigger_forms_agree() -> None:
    for text in ("SUPER + Q", "SUPER + notakey", "catchall", "SUPER + SHIFT", "mouse_up"):
        from_text = validate_trigger(text)
        from_parsed = validate_trigger(parse_trigger(text))
        assert (from_text is None) == (from_parsed is None), text
        if from_text is not None and from_parsed is not None:
            assert from_text.severity is from_parsed.severity, text


def test_modifier_only_is_blocked() -> None:
    problem = validate_trigger("SUPER + SHIFT")
    assert problem is not None and problem.blocking
    assert "only modifiers" in problem.message


def test_catchall_with_modifiers_is_blocked() -> None:
    """Lua rejects the whole config with Unknown keysym: "catchall"."""
    problem = validate_trigger(f"SUPER + {CATCHALL}", in_submap=True)
    assert problem is not None and problem.blocking


def test_catchall_alone_in_a_submap_is_fine() -> None:
    assert validate_trigger(CATCHALL, in_submap=True) is None


def test_catchall_outside_a_submap_warns_but_does_not_block() -> None:
    problem = validate_trigger(CATCHALL, in_submap=False)
    assert problem is not None
    assert problem.severity is Severity.WARN


@pytest.mark.parametrize(
    "text", ["SUPER + mouse_down + Q", "SUPER + mouse:272 + Q", "switch:on:Lid + Q"]
)
def test_exclusive_sym_combined_with_another_key_is_blocked(text: str) -> None:
    problem = validate_trigger(text)
    assert problem is not None and problem.blocking


def test_keycodes_are_not_exclusive_syms() -> None:
    """`code:N` sits in parseKeyString's "one or more keysyms" branch, not the exclusive
    one -- so a multi-key bind of two key codes is legal and must not be blocked."""
    problem = validate_trigger("SUPER + code:36 + code:37")
    assert problem is not None
    assert problem.severity is Severity.WARN  # multi-key: read-only, not invalid
    assert not problem.blocking


def test_multi_key_warns_rather_than_blocks() -> None:
    """Imported multi-key binds are read-only, not invalid (ADR-0007)."""
    problem = validate_trigger("SUPER + A + B")
    assert problem is not None
    assert problem.severity is Severity.WARN


@needs_xkb
def test_bare_letter_warns_but_is_allowed() -> None:
    """Looser than GNOME on purpose: a modifier-less bind is legal in Hyprland."""
    problem = validate_trigger("Q")
    assert problem is not None
    assert problem.severity is Severity.WARN
    assert not problem.blocking


@pytest.mark.parametrize("text", ["mouse:abc", "code:x", "switch:"])
def test_malformed_special_syms_are_blocked(text: str) -> None:
    problem = validate_trigger(text)
    assert problem is not None and problem.blocking


def test_validation_is_silent_when_xkb_cannot_be_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    """No validator must mean no opinion -- never a guess that files false errors."""
    monkeypatch.setattr("hyprtweaker.engine.triggers.known_keysym", lambda _name: None)
    assert validate_trigger("SUPER + whatever") is None


# --- keysym normalisation -------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ISO_Left_Tab", "Tab"),  # what Shift+Tab actually reports
        ("Sys_Req", "Print"),  # what Alt+Print actually reports
        ("q", "Q"),
        ("Q", "Q"),
        ("Return", "Return"),
        ("1", "1"),
        ("", ""),
    ],
)
def test_normalise_keysym(raw: str, expected: str) -> None:
    assert normalise_keysym(raw) == expected


# --- the tables cannot drift ----------------------------------------------------------


@needs_xkb
def test_every_suggestion_is_checked_both_ways() -> None:
    """The invariant `importer.binds._KEY_RENAMES` documents, applied here too.

    The key must be a name xkb rejects (or the suggestion would never be offered) and the
    value one it accepts (or the suggestion names a keysym as dead as the one it
    replaces). Without this the table quietly becomes a table of guesses.
    """
    from hyprtweaker.engine.triggers import _SUGGESTIONS

    for dead, suggested in _SUGGESTIONS.items():
        assert known_keysym(dead) is False, f"{dead!r} is a real keysym; drop the entry"
        assert known_keysym(suggested) is True, f"{suggested!r} is not a keysym xkb knows"


def test_grammar_tables_agree_with_the_importer() -> None:
    """Two modules describe the same Hyprland grammar; drift means they disagree
    about what binds.

    They stay separate rather than shared because `engine.importer.__init__` imports
    `binds`, and `triggers` imports `importer.keysyms` -- importing the other way round
    would close an import cycle. So the agreement is asserted instead of structural.
    """
    from hyprtweaker.engine.importer import binds as importer_binds
    from hyprtweaker.engine.triggers import MOD_ALIASES, MODIFIERS, WHEEL

    assert CATCHALL == importer_binds.CATCHALL
    assert WHEEL | {CATCHALL} == set(importer_binds._SPECIAL_EXACT)
    assert set(MODIFIERS) == {name for name, _aliases in importer_binds._MOD_ALIASES}
    # Every alias the importer honours must also be one the editor can parse, or a bind
    # imported as SUPER cannot be re-typed as WIN.
    for canonical, aliases in importer_binds._MOD_ALIASES:
        for alias in aliases:
            assert MOD_ALIASES.get(alias) == canonical, alias


# --- tokens ---------------------------------------------------------------------------


def test_button_tokens_use_evdev_codes_not_gdk_numbers() -> None:
    """GDK 2/3 are middle/right; evdev 273/274 are right/middle -- the swap is the trap."""
    assert button_token(1) == "mouse:272"
    assert button_token(2) == "mouse:274"
    assert button_token(3) == "mouse:273"


def test_wheel_tokens() -> None:
    assert wheel_token("up") == "mouse_up"
    assert wheel_token("DOWN") == "mouse_down"


# --- the recorder ---------------------------------------------------------------------


def test_modifier_press_alone_does_not_settle_a_trigger() -> None:
    recorder = CaptureRecorder()
    assert recorder.press("Super_L") is None
    assert recorder.mods == ("SUPER",)


def test_chord_settles_on_the_first_non_modifier_key() -> None:
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    recorder.press("Shift_L")
    trigger = recorder.press("Q")
    assert trigger is not None
    assert str(trigger) == "SHIFT + SUPER + Q"


def test_release_drops_the_modifier() -> None:
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    recorder.press("Shift_L")
    recorder.release("Shift_L")
    assert recorder.mods == ("SUPER",)


def test_modifiers_released_before_the_key_are_still_latched() -> None:
    """The chord must survive a user who lets go of SUPER a moment early."""
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    recorder.release("Super_L")
    trigger = recorder.press("Q")
    assert trigger is not None
    assert str(trigger) == "SUPER + Q"


def test_left_and_right_modifiers_are_the_same_modifier() -> None:
    recorder = CaptureRecorder()
    recorder.press("Control_R")
    assert recorder.mods == ("CTRL",)


@needs_xkb
def test_unknown_keysym_with_a_keycode_falls_back_to_code() -> None:
    """A key with no keysym on this layout is still bindable, by hardware code."""
    recorder = CaptureRecorder()
    trigger = recorder.press("notakey", keycode=99)
    assert trigger is not None
    assert trigger.key == "code:99"


def test_mouse_button_capture_carries_held_modifiers() -> None:
    recorder = CaptureRecorder()
    recorder.press("Alt_L")
    assert str(recorder.button(1)) == "ALT + mouse:272"


def test_wheel_capture_carries_held_modifiers() -> None:
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    assert str(recorder.wheel("down")) == "SUPER + mouse_down"


def test_reset_clears_held_and_latched_state() -> None:
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    recorder.reset()
    assert recorder.mods == ()
    assert recorder.modifier_only() is None
    trigger = recorder.press("Q")
    assert trigger is not None and str(trigger) == "Q"


def test_modifier_only_reports_the_pending_chord() -> None:
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    pending = recorder.modifier_only()
    assert pending is not None and str(pending) == "SUPER"


@needs_xkb
def test_captured_triggers_validate_clean() -> None:
    """End to end: what the recorder produces is what validation accepts."""
    recorder = CaptureRecorder()
    recorder.press("Super_L")
    recorder.press("Shift_L")
    trigger = recorder.press("q")
    assert trigger is not None
    assert validate_trigger(str(trigger)) is None
    assert str(trigger) == "SHIFT + SUPER + Q"
