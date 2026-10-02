"""The string-grammar effects, parsed and emitted without a toolkit (#155, ADR-0008).

`opacity`, `fullscreen_state` and `suppress_event` are strings in the Lua API: the
compositor parses the text itself. A parse returns `None` for text the typed form cannot
carry unchanged, so the editor opens that value as text instead of mangling it.
"""

from __future__ import annotations

import pytest

from hyprtweaker.engine import rule_grammars as grammars
from hyprtweaker.engine.rule_grammars import (
    FullscreenState,
    Opacity,
    OpacityState,
)


def state(value: float, override: bool = False) -> OpacityState:
    return OpacityState(value, override)


class TestOpacity:
    def test_one_value_copies_to_the_other_two_states(self) -> None:
        assert grammars.parse_opacity("0.9") == Opacity(state(0.9), state(0.9), state(0.9))

    def test_one_value_with_override_copies_the_override_too(self) -> None:
        parsed = grammars.parse_opacity("0.9 override")

        assert parsed == Opacity(state(0.9, True), state(0.9, True), state(0.9, True))

    def test_two_values_leave_fullscreen_at_opaque(self) -> None:
        assert grammars.parse_opacity("0.9 0.7") == Opacity(state(0.9), state(0.7), state(1.0))

    def test_three_values_with_overrides_on_the_second_and_third(self) -> None:
        parsed = grammars.parse_opacity("0.9 0.8 override 0.5 override")

        assert parsed == Opacity(state(0.9), state(0.8, True), state(0.5, True))

    def test_the_real_world_two_override_form(self) -> None:
        parsed = grammars.parse_opacity("0.89 override 0.89 override")

        assert parsed == Opacity(state(0.89, True), state(0.89, True), state(1.0))

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   ",
            "override",
            "override 0.9",
            "0.9 override override",
            "0.9 0.8 0.7 0.6",
            "abc",
            "0.9 $& 0.9 $& 1",
            "1.5",
            "-0.2",
            "0.123",
            "nan",
            "inf",
            "1e-1",
        ],
    )
    def test_text_it_cannot_carry_unchanged_is_none(self, text: str) -> None:
        assert grammars.parse_opacity(text) is None

    @pytest.mark.parametrize("value", [0.9, 1, True, None, ["0.9"], {"a": 1}])
    def test_a_value_that_is_not_text_is_none(self, value: object) -> None:
        assert grammars.parse_opacity(value) is None

    @pytest.mark.parametrize(
        ("typed", "text"),
        [
            (Opacity(state(0.9), state(0.9), state(0.9)), "0.9"),
            (Opacity(state(1.0), state(1.0), state(1.0)), "1"),
            (Opacity(state(0.9, True), state(0.9, True), state(0.9, True)), "0.9 override"),
            (Opacity(state(0.9), state(0.7), state(1.0)), "0.9 0.7"),
            (
                Opacity(state(0.89, True), state(0.89, True), state(1.0)),
                "0.89 override 0.89 override",
            ),
            (
                Opacity(state(0.9), state(0.8, True), state(0.5, True)),
                "0.9 0.8 override 0.5 override",
            ),
            (Opacity(state(0.9), state(0.7), state(0.7)), "0.9 0.7 0.7"),
            (Opacity(state(0.05), state(0.1), state(0.0)), "0.05 0.1 0"),
        ],
    )
    def test_emit_is_the_shortest_text_that_says_the_same(
        self, typed: Opacity, text: str
    ) -> None:
        assert grammars.emit_opacity(typed) == text

    def test_emit_rounds_a_spin_buttons_float_noise(self) -> None:
        typed = Opacity(state(0.1 + 0.2), state(0.7000000000000001), state(1.0))

        assert grammars.emit_opacity(typed) == "0.3 0.7"

    def test_emit_is_a_string_never_a_number(self) -> None:
        assert isinstance(grammars.emit_opacity(Opacity(state(1), state(1), state(1))), str)

    @pytest.mark.parametrize("text", ["0.9", "0.9 override", "0.9 0.7", "0.9 0.8 override 0.5"])
    def test_a_canonical_text_round_trips(self, text: str) -> None:
        parsed = grammars.parse_opacity(text)

        assert parsed is not None
        assert grammars.emit_opacity(parsed) == text


class TestFullscreenState:
    def test_two_numbers(self) -> None:
        assert grammars.parse_fullscreen_state("1 2") == FullscreenState(1, 2)

    def test_extra_spaces_are_fine(self) -> None:
        assert grammars.parse_fullscreen_state(" 3   0 ") == FullscreenState(3, 0)

    @pytest.mark.parametrize(
        "text", ["", "1", "-1", "-1 -1", "4 0", "0 4", "1 2 3", "a b", "1.5 2", "٣ ٣"]
    )
    def test_anything_but_two_numbers_from_0_to_3_is_none(self, text: str) -> None:
        assert grammars.parse_fullscreen_state(text) is None

    @pytest.mark.parametrize("value", [11, None, ["1", "2"]])
    def test_a_value_that_is_not_text_is_none(self, value: object) -> None:
        assert grammars.parse_fullscreen_state(value) is None

    def test_emit(self) -> None:
        assert grammars.emit_fullscreen_state(FullscreenState(2, 0)) == "2 0"

    def test_the_four_values_are_all_representable(self) -> None:
        for internal in range(4):
            for client in range(4):
                text = f"{internal} {client}"
                parsed = grammars.parse_fullscreen_state(text)
                assert parsed is not None
                assert grammars.emit_fullscreen_state(parsed) == text


class TestSuppressEvent:
    def test_the_six_known_events(self) -> None:
        text = "fullscreen maximize activate activatefocus fullscreenoutput x11configurerequest"

        assert grammars.parse_suppress_event(text) == (
            "fullscreen",
            "maximize",
            "activate",
            "activatefocus",
            "fullscreenoutput",
            "x11configurerequest",
        )

    def test_the_parse_lists_events_in_the_checklists_order(self) -> None:
        assert grammars.parse_suppress_event("maximize fullscreen") == (
            "fullscreen",
            "maximize",
        )

    def test_a_repeated_event_is_listed_once(self) -> None:
        assert grammars.parse_suppress_event("activate activate") == ("activate",)

    @pytest.mark.parametrize(
        "text", ["", "  ", "fullscreen bogus", "Fullscreen", "fullscreen,maximize"]
    )
    def test_an_unknown_token_sends_the_whole_value_to_text(self, text: str) -> None:
        assert grammars.parse_suppress_event(text) is None

    @pytest.mark.parametrize("value", [None, True, ["fullscreen"]])
    def test_a_value_that_is_not_text_is_none(self, value: object) -> None:
        assert grammars.parse_suppress_event(value) is None

    def test_emit_is_space_separated_in_the_checklists_order(self) -> None:
        assert grammars.emit_suppress_event(("maximize", "fullscreen")) == "fullscreen maximize"

    def test_emit_of_nothing_is_the_empty_string(self) -> None:
        assert grammars.emit_suppress_event(()) == ""

    def test_the_event_list_is_published_for_the_checklist(self) -> None:
        assert grammars.SUPPRESS_EVENTS == (
            "fullscreen",
            "maximize",
            "activate",
            "activatefocus",
            "fullscreenoutput",
            "x11configurerequest",
        )
