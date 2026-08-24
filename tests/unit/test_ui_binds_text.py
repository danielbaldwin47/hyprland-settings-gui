"""What a Bind Row says, settled without a display (#64).

The Binds Page module imports `gi` at module scope like the rest of the UI, so these tests
skip where PyGObject is absent -- but the functions under test are pure string work, and
asserting them here rather than in the smoke tier is what keeps that tier shallow.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine.model.entities import Bind, BindOptions, DispatcherCall

pytest.importorskip("gi", reason="the Binds Page imports gi at module scope")

from hyprtweaker.ui.pages.binds import (
    RowConflict,
    action_text,
    flag_text,
    ordinal,
    read_only_reason,
    rival_label,
    trigger_text,
)


def exec_bind(keys: str = "SUPER + Q", command: str = "kitty", **kwargs: object) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,  # type: ignore[arg-type]
    )


class TestTrigger:
    def test_plain_keys_read_back_as_written(self) -> None:
        assert trigger_text(exec_bind("SUPER + SHIFT + Q")) == "SUPER + SHIFT + Q"

    def test_key_codes_are_spelled_out(self) -> None:
        """The one Trigger a user cannot recognise, and the one IPC will not identify."""
        assert trigger_text(exec_bind("SUPER + code:10")) == "SUPER + key code 10"

    def test_spacing_is_normalised(self) -> None:
        assert trigger_text(exec_bind("SUPER+Q")) == "SUPER + Q"

    def test_a_lone_key_survives(self) -> None:
        assert trigger_text(exec_bind("XF86AudioPlay")) == "XF86AudioPlay"


class TestAction:
    def test_exec_shows_the_command_itself(self) -> None:
        """Exec is the majority bind; the command is the useful thing to show."""
        assert action_text(exec_bind(command="kitty --title x")) == "kitty --title x"

    def test_a_known_dispatcher_shows_its_label(self) -> None:
        bind = Bind(keys="A", dispatcher=DispatcherCall(path="window.close"))
        assert action_text(bind) == "Close the window"

    def test_arguments_are_appended(self) -> None:
        bind = Bind(keys="A", dispatcher=DispatcherCall(path="window.tag", args={"tag": "x"}))
        assert action_text(bind) == "Tag the window (tag: x)"

    def test_an_unknown_dispatcher_shows_its_call(self) -> None:
        """A plugin or a newer Hyprland: showing the call beats showing nothing."""
        bind = Bind(keys="A", dispatcher=DispatcherCall(path="plugin.thing"))
        assert action_text(bind) == "hl.dsp.plugin.thing"

    def test_a_function_action_says_so(self) -> None:
        assert action_text(Bind(keys="A", dispatcher=None)) == "Runs a Lua function"


class TestFlags:
    def test_set_flags_are_named(self) -> None:
        bind = exec_bind(options=BindOptions(locked=True, repeating=True))
        assert flag_text(bind) == "locked, repeating"

    def test_no_flags_is_empty(self) -> None:
        assert flag_text(exec_bind()) == ""

    def test_the_description_is_not_a_flag(self) -> None:
        assert flag_text(exec_bind(options=BindOptions(description="hi"))) == ""


class TestReadOnlyReason:
    def test_an_ordinary_bind_is_editable(self) -> None:
        assert read_only_reason(exec_bind()) == ""

    def test_a_function_action_is_not(self) -> None:
        assert "user.lua" in read_only_reason(Bind(keys="A", dispatcher=None))

    def test_a_multi_key_bind_is_not(self) -> None:
        """0 uses in the corpus and only approximate under Lua (ADR-0007)."""
        assert read_only_reason(exec_bind("SUPER + A&B")) != ""


class TestConflictText:
    """The conflict badge states fire order, not just existence (ADR-0007, #66)."""

    def test_ordinals(self) -> None:
        assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 21)] == [
            "1st",
            "2nd",
            "3rd",
            "4th",
            "11th",
            "12th",
            "21st",
        ]

    def test_the_badge_states_fire_order(self) -> None:
        conflict = RowConflict(order=1, total=2, rivals=())
        assert "fires 1st of 2" in conflict.badge_text
        assert conflict.short_text == "1st of 2"

    def test_a_cross_submap_conflict_claims_no_order(self) -> None:
        # A universal bind and a submap bind never share a firing sequence -- the writer
        # emits root binds before any submap block, so list order is not file order there.
        conflict = RowConflict(order=1, total=1, rivals=())
        assert "fires" not in conflict.badge_text
        assert "another submap" in conflict.badge_text
        assert conflict.short_text == "duplicate"

    def test_a_rival_line_says_what_and_where(self) -> None:
        line = rival_label(exec_bind(command="kitty"), order=2)
        assert "2nd" in line
        assert "kitty" in line
        assert "root keybinds" in line

    def test_a_rival_in_a_submap_names_it(self) -> None:
        line = rival_label(exec_bind(command="grow", submap="resize"), order=1)
        assert "submap resize" in line

    def test_a_cross_submap_rival_gets_no_number(self) -> None:
        line = rival_label(exec_bind(command="grow", submap="resize"), order=None)
        assert "1st" not in line
        assert line.startswith("grow")
