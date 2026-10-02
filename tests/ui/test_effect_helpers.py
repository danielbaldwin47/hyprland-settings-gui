"""The rule editor's typed effect helpers: `opacity`, `fullscreen_state`, `suppress_event`
(#155, ADR-0008).

Parse/emit is settled headless in `tests/unit/test_rule_grammars.py`. What is left here is
assembly: the editor builds a helper off the catalog's `grammar`, the helper shows what was
imported, an untouched row saves the original value, a touched one saves the string the
compositor accepts, and a value the controls cannot show opens as text, unchanged.
"""

from __future__ import annotations

from typing import Any

import pytest
from started_app import presented


def window_rule(**kwargs: Any) -> Any:
    from hyprtweaker.engine.model.entities import WindowRule

    return WindowRule(**kwargs)


def open_editor(effects: dict[str, Any]) -> tuple[Any, list[Any]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    Adw.init()
    collected: list[Any] = []
    rule = window_rule(match={"class": "x"}, effects=effects)
    return presented(RuleEditor(kind="window", on_done=collected.append, rule=rule)), collected


def row_of(editor: Any, name: str) -> Any:
    return next(entry for entry in editor._effect_entries if entry.name == name)


def saved(editor: Any, collected: list[Any]) -> dict[str, Any]:
    editor._save()
    assert collected, editor._error.get_label()
    return collected[0].effects  # type: ignore[no-any-return]


class TestDispatch:
    @pytest.mark.parametrize("name", ["opacity", "fullscreen_state", "suppress_event"])
    def test_a_grammar_effect_gets_a_helper_row(self, name: str) -> None:
        value = {"opacity": "0.9", "fullscreen_state": "1 2", "suppress_event": "maximize"}
        editor, _ = open_editor({name: value[name]})
        row = row_of(editor, name)

        assert row.helper is not None
        assert row.widget is row.helper.widget
        assert (
            row.widget.get_title()
            == {
                "opacity": "Opacity",
                "fullscreen_state": "Fullscreen state",
                "suppress_event": "Suppress event",
            }[name]
        )

    def test_a_plain_text_effect_stays_an_entry_row(self) -> None:
        from gi.repository import Adw

        editor, _ = open_editor({"animation": "popin 80%"})
        row = row_of(editor, "animation")

        assert row.helper is None
        assert isinstance(row.widget, Adw.EntryRow)

    def test_the_registry_has_exactly_the_grammars_the_catalog_names(self) -> None:
        from hyprtweaker.engine.rules_catalog import LAYER_EFFECTS, WINDOW_EFFECTS
        from hyprtweaker.ui.dialogs.rule_editor import EFFECT_HELPERS

        named = {e.grammar for e in (*WINDOW_EFFECTS, *LAYER_EFFECTS) if e.grammar}

        assert named == set(EFFECT_HELPERS)

    def test_the_title_is_not_parsed_as_markup(self) -> None:
        editor, _ = open_editor({"opacity": "0.9"})

        assert row_of(editor, "opacity").widget.get_use_markup() is False


class TestOpacity:
    def test_an_imported_value_fills_the_controls(self) -> None:
        editor, _ = open_editor({"opacity": "0.9 0.8 override 0.5"})
        helper = row_of(editor, "opacity").helper

        assert [spin.get_value() for spin in helper.spins] == [0.9, 0.8, 0.5]
        assert [check.get_active() for check in helper.overrides] == [False, True, False]
        assert helper.raw_toggle.get_active() is False

    def test_an_untouched_row_saves_the_original_text_verbatim(self) -> None:
        editor, collected = open_editor({"opacity": "0.90  override"})

        assert saved(editor, collected)["opacity"] == "0.90  override"

    def test_a_changed_value_saves_the_string_grammar(self) -> None:
        editor, collected = open_editor({"opacity": "0.9 0.8"})
        helper = row_of(editor, "opacity").helper
        helper.spins[1].set_value(0.65)
        helper.overrides[1].set_active(True)

        value = saved(editor, collected)["opacity"]

        assert value == "0.9 0.65 override"
        assert isinstance(value, str)

    def test_one_value_follows_into_the_other_states_until_they_are_edited(self) -> None:
        editor, collected = open_editor({"opacity": "0.9"})
        helper = row_of(editor, "opacity").helper
        helper.spins[0].set_value(0.7)

        assert [spin.get_value() for spin in helper.spins] == [0.7, 0.7, 0.7]
        assert saved(editor, collected)["opacity"] == "0.7"

    def test_editing_another_state_breaks_the_link(self) -> None:
        editor, collected = open_editor({"opacity": "0.9"})
        helper = row_of(editor, "opacity").helper
        helper.spins[1].set_value(0.6)
        helper.spins[0].set_value(0.8)

        assert [spin.get_value() for spin in helper.spins] == [0.8, 0.6, 0.9]
        assert saved(editor, collected)["opacity"] == "0.8 0.6 0.9"

    def test_a_new_opacity_row_starts_opaque(self) -> None:
        editor, collected = open_editor({})
        editor._add_effect_row("opacity", None)

        assert saved(editor, collected)["opacity"] == "1"

    def test_text_the_controls_cannot_show_opens_as_text_unchanged(self) -> None:
        editor, collected = open_editor({"opacity": "0.90 $& 0.90 $& 1"})
        helper = row_of(editor, "opacity").helper

        assert helper.raw_toggle.get_active() is True
        assert helper.raw_entry.get_text() == "0.90 $& 0.90 $& 1"
        assert saved(editor, collected)["opacity"] == "0.90 $& 0.90 $& 1"

    def test_the_toggle_cannot_leave_text_that_does_not_parse(self) -> None:
        editor, _ = open_editor({"opacity": "0.90 $& 0.90 $& 1"})
        helper = row_of(editor, "opacity").helper

        assert helper.raw_toggle.get_sensitive() is False
        helper.raw_toggle.set_active(False)
        assert helper.raw_toggle.get_active() is True

    def test_fixing_the_text_unlocks_the_toggle_and_fills_the_controls(self) -> None:
        editor, collected = open_editor({"opacity": "0.90 $& 0.90 $& 1"})
        helper = row_of(editor, "opacity").helper
        helper.raw_entry.set_text("0.8 0.6")

        assert helper.raw_toggle.get_sensitive() is True
        helper.raw_toggle.set_active(False)

        assert [spin.get_value() for spin in helper.spins] == [0.8, 0.6, 1.0]
        assert saved(editor, collected)["opacity"] == "0.8 0.6"

    def test_text_edited_by_hand_saves_as_typed(self) -> None:
        editor, collected = open_editor({"opacity": "0.9"})
        helper = row_of(editor, "opacity").helper
        helper.raw_toggle.set_active(True)
        helper.raw_entry.set_text("0.5 override 0.4 override")

        assert saved(editor, collected)["opacity"] == "0.5 override 0.4 override"

    def test_switching_to_text_shows_the_original_spelling(self) -> None:
        editor, _ = open_editor({"opacity": "0.90 0.80"})
        helper = row_of(editor, "opacity").helper
        helper.raw_toggle.set_active(True)

        assert helper.raw_entry.get_text() == "0.90 0.80"

    def test_blank_text_refuses_to_save(self) -> None:
        editor, collected = open_editor({"opacity": "0.9"})
        helper = row_of(editor, "opacity").helper
        helper.raw_toggle.set_active(True)
        helper.raw_entry.set_text("")
        editor._save()

        assert collected == []
        assert "Opacity effect needs a value" in editor._error.get_label()

    def test_a_number_valued_original_survives_untouched(self) -> None:
        editor, collected = open_editor({"opacity": 0.9})

        assert row_of(editor, "opacity").helper.raw_toggle.get_active() is True
        assert saved(editor, collected)["opacity"] == 0.9


class TestFullscreenState:
    def test_an_imported_pair_fills_both_pickers(self) -> None:
        editor, _ = open_editor({"fullscreen_state": "1 2"})
        helper = row_of(editor, "fullscreen_state").helper

        assert (helper.internal.get_selected(), helper.client.get_selected()) == (1, 2)

    def test_untouched_saves_the_original(self) -> None:
        editor, collected = open_editor({"fullscreen_state": "1  2"})

        assert saved(editor, collected)["fullscreen_state"] == "1  2"

    def test_a_picked_value_saves_the_pair(self) -> None:
        editor, collected = open_editor({"fullscreen_state": "1 2"})
        helper = row_of(editor, "fullscreen_state").helper
        helper.client.set_selected(3)

        assert saved(editor, collected)["fullscreen_state"] == "1 3"

    @pytest.mark.parametrize("text", ["1", "-1", "-1 -1", "4 0"])
    def test_anything_but_a_pair_from_0_to_3_opens_as_text(self, text: str) -> None:
        editor, collected = open_editor({"fullscreen_state": text})
        helper = row_of(editor, "fullscreen_state").helper

        assert helper.raw_toggle.get_active() is True
        assert helper.raw_entry.get_text() == text
        assert saved(editor, collected)["fullscreen_state"] == text


class TestSuppressEvent:
    def test_an_imported_list_checks_its_events(self) -> None:
        editor, _ = open_editor({"suppress_event": "maximize activate"})
        helper = row_of(editor, "suppress_event").helper

        assert {name for name, row in helper.switches.items() if row.get_active()} == {
            "maximize",
            "activate",
        }

    def test_untouched_saves_the_original_order_and_spelling(self) -> None:
        editor, collected = open_editor({"suppress_event": "activate  maximize"})

        assert saved(editor, collected)["suppress_event"] == "activate  maximize"

    def test_a_changed_list_saves_in_the_checklists_order(self) -> None:
        editor, collected = open_editor({"suppress_event": "maximize"})
        helper = row_of(editor, "suppress_event").helper
        helper.switches["fullscreen"].set_active(True)

        assert saved(editor, collected)["suppress_event"] == "fullscreen maximize"

    def test_one_unknown_token_sends_the_whole_value_to_text(self) -> None:
        editor, collected = open_editor({"suppress_event": "fullscreen newevent"})
        helper = row_of(editor, "suppress_event").helper

        assert helper.raw_toggle.get_active() is True
        assert saved(editor, collected)["suppress_event"] == "fullscreen newevent"

    def test_nothing_checked_refuses_to_save(self) -> None:
        editor, collected = open_editor({"suppress_event": "maximize"})
        helper = row_of(editor, "suppress_event").helper
        helper.switches["maximize"].set_active(False)
        editor._save()

        assert collected == []
        assert "Suppress event effect needs a value" in editor._error.get_label()

    def test_the_summary_names_the_checked_events(self) -> None:
        editor, _ = open_editor({"suppress_event": "maximize fullscreen"})

        assert row_of(editor, "suppress_event").widget.get_subtitle() == "fullscreen maximize"

    def test_each_event_reads_in_plain_words(self) -> None:
        """#151 review, finding 31: the subtitles were the raw keys (`fullscreenoutput`);
        the keys stay one toggle away, in Edit as text."""
        editor, _ = open_editor({"suppress_event": "maximize"})
        helper = row_of(editor, "suppress_event").helper

        assert [(row.get_title(), row.get_subtitle()) for row in helper.switches.values()] == [
            ("Fullscreen requests", "The window cannot make itself fullscreen"),
            ("Maximize requests", "The window cannot maximize itself"),
            ("Activation requests", "The window cannot bring itself to the front"),
            ("Focus on activation", "The window can ask for attention but not take focus"),
            (
                "Fullscreen monitor choice",
                "The window goes fullscreen where it is, not on the monitor it asks for",
            ),
            (
                "X11 move and resize requests",
                "A floating X11 window cannot move or resize itself",
            ),
        ]


class TestRemove:
    def test_the_remove_button_takes_the_helper_row_out(self) -> None:
        editor, collected = open_editor({"opacity": "0.9", "animation": "slide"})
        row = row_of(editor, "opacity")
        suffix_button = _find_remove_button(row.widget)
        suffix_button.emit("clicked")

        assert saved(editor, collected) == {"animation": "slide"}


def _find_remove_button(widget: Any) -> Any:
    """The trash button the editor adds as a suffix, found by its tooltip."""
    stack = [widget]
    while stack:
        node = stack.pop()
        if getattr(node, "get_tooltip_text", None) and node.get_tooltip_text() == (
            "Remove this effect"
        ):
            return node
        child = node.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    raise AssertionError("no remove button in the helper row")
