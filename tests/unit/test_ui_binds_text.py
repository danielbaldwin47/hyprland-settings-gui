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
    BadgeKind,
    RowConflict,
    action_text,
    bind_badge,
    flag_text,
    ordinal,
    rival_label,
    trigger_text,
)


def exec_bind(keys: str = "SUPER + Q", command: str = "kitty", **kwargs: object) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,  # type: ignore[arg-type]
    )


def submap_bind(keys: str, target: str, **kwargs: object) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="submap", positional=(target,)),
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


@pytest.fixture
def xkb_knows_all_but_notakey(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in for libxkbcommon, so the dead-keysym state is tested without a skip."""
    monkeypatch.setattr(
        "hyprtweaker.engine.importer.binds.known_keysym", lambda name: name != "notakey"
    )


@pytest.mark.usefixtures("xkb_knows_all_but_notakey")
class TestBindBadge:
    def test_an_enabled_ordinary_bind_has_no_badge(self) -> None:
        assert bind_badge(exec_bind()) is None

    def test_a_user_disabled_bind_is_plain_disabled(self) -> None:
        badge = bind_badge(exec_bind(enabled=False))
        assert badge is not None
        assert (badge.kind, badge.text) == (BadgeKind.DISABLED, "Disabled")

    def test_a_disabled_dead_keysym_bind_is_an_error_naming_the_key(self) -> None:
        badge = bind_badge(exec_bind("SUPER + notakey", enabled=False))
        assert badge is not None
        assert (badge.kind, badge.text) == (BadgeKind.ERROR, 'Unknown key "notakey"')

    def test_a_disabled_bind_whose_trigger_cannot_load_offers_a_fix_not_enable(self) -> None:
        """The Session refuses to enable it (#199): a one-click Enable would be a dead end."""
        badge = bind_badge(exec_bind("SUPER + mouse:272 + Q", enabled=False))
        assert badge is not None
        assert (badge.kind, badge.text, badge.tooltip) == (
            BadgeKind.ERROR,
            "Trigger can't load",
            "Mouse, wheel and switch triggers cannot be combined with other keys. "
            "Use just one of: mouse:272. Hyprland can't load this trigger, so this keybind "
            "stays commented out. Record a new trigger to use it.",
        )

    def test_a_multi_key_bind_is_not_mistaken_for_a_dead_keysym(self) -> None:
        """`A&B` is one token xkb does not know; the multi-key reason must win."""
        badge = bind_badge(exec_bind("SUPER + A&B", enabled=False))
        assert badge is not None
        assert (badge.kind, badge.text) == (
            BadgeKind.MULTI_KEY,
            "Multi-key: Hyprland can't load it",
        )

    def test_a_function_action_wins_over_every_other_reason(self) -> None:
        badge = bind_badge(Bind(keys="SUPER + A&B", dispatcher=None, enabled=False))
        assert badge is not None
        assert (badge.kind, badge.text) == (
            BadgeKind.LUA_FUNCTION,
            "Defined by a Lua function in user.lua",
        )

    def test_a_bind_entering_an_empty_submap_warns_with_the_reason(self) -> None:
        badge = bind_badge(
            submap_bind("SUPER + R", "resize"), empty_submaps=frozenset({"resize"})
        )
        assert badge is not None
        assert (badge.kind, badge.text) == (BadgeKind.EMPTY_SUBMAP, "Submap has no binds")
        assert (
            "Hyprland cannot enter a submap with no binds. Add a bind to it." in badge.tooltip
        )
        assert badge.kind.style == "warning"

    def test_the_empty_submap_badge_leaves_the_bind_editable_and_removable(self) -> None:
        """The bind is fine; the submap is what is missing a bind, so nothing is taken away."""
        kind = BadgeKind.EMPTY_SUBMAP
        assert (kind.editable, kind.removable, kind.verb) == (True, True, None)

    def test_a_bind_entering_another_submap_has_no_badge(self) -> None:
        badge = bind_badge(
            submap_bind("SUPER + R", "resize"), empty_submaps=frozenset({"other"})
        )
        assert badge is None

    def test_a_bind_that_is_not_an_entry_has_no_badge(self) -> None:
        assert bind_badge(exec_bind(), empty_submaps=frozenset({"resize"})) is None

    def test_an_earlier_reason_wins_over_the_empty_submap(self) -> None:
        empty = frozenset({"resize"})
        disabled = bind_badge(
            submap_bind("SUPER + R", "resize", enabled=False), empty_submaps=empty
        )
        multi = bind_badge(submap_bind("SUPER + A&B", "resize"), empty_submaps=empty)
        function = bind_badge(Bind(keys="SUPER + R", dispatcher=None), empty_submaps=empty)
        assert disabled is not None and multi is not None and function is not None
        assert (disabled.kind, multi.kind, function.kind) == (
            BadgeKind.DISABLED,
            BadgeKind.MULTI_KEY,
            BadgeKind.LUA_FUNCTION,
        )

    def test_multi_key_and_lua_function_carry_different_reasons(self) -> None:
        multi = bind_badge(exec_bind("SUPER + A&B"))
        function = bind_badge(Bind(keys="SUPER + A", dispatcher=None))
        assert multi is not None and function is not None
        assert multi.tooltip != function.tooltip
        assert "user.lua" in function.tooltip
        assert "A&B" in multi.tooltip


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
