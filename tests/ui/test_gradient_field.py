"""The rule editor's `border_color` gradient editor (#156, ADR-0008).

Parse/emit is settled headless in `tests/unit/test_rule_grammars.py`. What is left here is
assembly: the editor builds the gradient row off the catalog's `grammar`, the row shows what
was imported, an untouched row saves the original object, an edited one saves a table, and
the active+inactive pair (which a table cannot say) opens as text, unchanged.
"""

from __future__ import annotations

from typing import Any

TABLE = {"colors": ["#ff0000", "#00ff00"], "angle": 45}
PAIR = "rgba(33ccffee) rgba(595959aa)"


def window_rule(**kwargs: Any) -> Any:
    from hyprtweaker.engine.model.entities import WindowRule

    return WindowRule(**kwargs)


def open_editor(effects: dict[str, Any]) -> tuple[Any, list[Any]]:
    from gi.repository import Adw

    from hyprtweaker.ui.dialogs.rule_editor import RuleEditor

    Adw.init()
    collected: list[Any] = []
    rule = window_rule(match={"class": "x"}, effects=effects)
    return RuleEditor(kind="window", on_done=collected.append, rule=rule), collected


def row_of(editor: Any, name: str) -> Any:
    return next(entry for entry in editor._effect_entries if entry.name == name)


def saved(editor: Any, collected: list[Any]) -> dict[str, Any]:
    editor._save()
    assert collected, editor._error.get_label()
    return collected[0].effects  # type: ignore[no-any-return]


def helper_of(effects: dict[str, Any]) -> tuple[Any, Any, list[Any]]:
    editor, collected = open_editor({"border_color": effects})
    return editor, row_of(editor, "border_color").helper, collected


def pick(button: Any, text: str) -> None:
    """What the colour dialog does on OK: set the colour, which notifies `rgba`."""
    from gi.repository import Gdk

    rgba = Gdk.RGBA()
    assert rgba.parse(text)
    button.set_rgba(rgba)


class TestImportedTable:
    def test_the_stops_and_angle_fill_the_controls(self) -> None:
        _, helper, _ = helper_of(TABLE)

        assert [b.get_rgba().to_string() for b in helper.stop_buttons] == [
            "rgb(255,0,0)",
            "rgb(0,255,0)",
        ]
        assert helper.angle.get_value() == 45
        assert (
            helper.angle.get_adjustment().get_lower(),
            helper.angle.get_adjustment().get_upper(),
        ) == (0, 360)
        assert helper.raw_toggle.get_active() is False

    def test_untouched_saves_the_original_object(self) -> None:
        editor, _, collected = helper_of(TABLE)

        assert saved(editor, collected)["border_color"] is TABLE

    def test_a_stop_picked_saves_a_table_in_the_rgba_spelling(self) -> None:
        editor, helper, collected = helper_of(TABLE)
        pick(helper.stop_buttons[1], "#0000ff80")

        assert saved(editor, collected)["border_color"] == {
            "colors": ["rgba(ff0000ff)", "rgba(0000ff80)"],
            "angle": 45,
        }

    def test_the_saved_angle_is_a_whole_number(self) -> None:
        editor, helper, collected = helper_of(TABLE)
        helper.angle.set_value(90)
        border_color = saved(editor, collected)["border_color"]

        assert border_color["angle"] == 90
        assert type(border_color["angle"]) is int

    def test_the_stops_are_modal_alpha_colour_dialogs(self) -> None:
        _, helper, _ = helper_of(TABLE)

        from gi.repository import Gtk

        for button in helper.stop_buttons:
            assert isinstance(button, Gtk.ColorDialogButton)
            assert button.get_dialog().get_modal() is True
            assert button.get_dialog().get_with_alpha() is True


class TestStops:
    def test_adding_a_stop_saves_one_more_colour(self) -> None:
        editor, helper, collected = helper_of({"colors": ["#ff0000"], "angle": 0})
        helper.add_button.emit("clicked")

        assert len(helper.stop_buttons) == 2
        assert len(saved(editor, collected)["border_color"]["colors"]) == 2

    def test_removing_a_stop_saves_one_fewer(self) -> None:
        editor, helper, collected = helper_of(TABLE)
        helper.remove_buttons[0].emit("clicked")

        assert saved(editor, collected)["border_color"] == {
            "colors": ["rgba(00ff00ff)"],
            "angle": 45,
        }

    def test_the_last_stop_cannot_be_removed(self) -> None:
        _, helper, _ = helper_of({"colors": ["#ff0000"], "angle": 0})

        assert [b.get_sensitive() for b in helper.remove_buttons] == [False]

    def test_the_stops_can_all_be_removed_but_one(self) -> None:
        _, helper, _ = helper_of(TABLE)

        assert [b.get_sensitive() for b in helper.remove_buttons] == [True, True]
        helper.remove_buttons[0].emit("clicked")
        assert [b.get_sensitive() for b in helper.remove_buttons] == [False]

    def test_adding_stops_stops_at_the_most_the_row_shows(self) -> None:
        from hyprtweaker.engine.rule_grammars import MAX_STOPS

        _, helper, _ = helper_of({"colors": ["#ff0000"], "angle": 0})
        for _ in range(MAX_STOPS + 3):
            if helper.add_button.get_sensitive():
                helper.add_button.emit("clicked")

        assert len(helper.stop_buttons) == MAX_STOPS
        assert helper.add_button.get_sensitive() is False


class TestPlainColour:
    def test_a_plain_colour_is_one_stop_and_saves_untouched(self) -> None:
        editor, helper, collected = helper_of("rgba(afc6ffAA)")

        assert len(helper.stop_buttons) == 1
        assert saved(editor, collected)["border_color"] == "rgba(afc6ffAA)"

    def test_a_changed_plain_colour_saves_a_table(self) -> None:
        editor, helper, collected = helper_of("rgba(afc6ffAA)")
        helper.angle.set_value(30)

        assert saved(editor, collected)["border_color"] == {
            "colors": ["rgba(afc6ffaa)"],
            "angle": 30,
        }


class TestPair:
    def test_the_pair_opens_as_text_unchanged(self) -> None:
        _, helper, _ = helper_of(PAIR)

        assert helper.raw_toggle.get_active() is True
        assert helper.raw_entry.get_text() == PAIR
        assert helper.raw_toggle.get_sensitive() is False

    def test_the_controls_behind_the_text_always_hold_a_stop(self) -> None:
        _, helper, _ = helper_of(PAIR)

        assert len(helper.stop_buttons) == 1
        assert helper.angle.get_value() == 0

    def test_the_pair_says_why_the_controls_cannot_show_it(self) -> None:
        """#151 review, finding 30: the generic line gave no reason for the pair."""
        _, helper, _ = helper_of(PAIR)

        assert helper.widget.get_subtitle() == (
            "Two colors without an angle are the active and inactive border. Edit them as text."
        )

    def test_other_text_the_controls_cannot_show_keeps_the_general_line(self) -> None:
        _, helper, _ = helper_of("rgba(33ccffee) 45deg rgba(595959aa)")

        assert helper.widget.get_subtitle() == (
            "The controls cannot show this value. Edit it as text."
        )

    def test_the_pair_saves_verbatim(self) -> None:
        editor, _, collected = helper_of(PAIR)

        assert saved(editor, collected)["border_color"] == PAIR

    def test_an_edited_pair_saves_as_typed(self) -> None:
        editor, helper, collected = helper_of(PAIR)
        helper.raw_entry.set_text("rgba(11223344) rgba(55667788)")

        assert saved(editor, collected)["border_color"] == "rgba(11223344) rgba(55667788)"

    def test_the_toggle_wakes_when_the_text_becomes_one_gradient(self) -> None:
        _, helper, _ = helper_of(PAIR)
        helper.raw_entry.set_text("rgba(33ccffee) rgba(595959aa) 45deg")

        assert helper.raw_toggle.get_sensitive() is True
        helper.raw_toggle.set_active(False)
        assert len(helper.stop_buttons) == 2
        assert helper.angle.get_value() == 45

    def test_an_angle_in_the_middle_is_text_too(self) -> None:
        text = "rgba(33ccffee) 45deg rgba(595959aa)"
        editor, helper, collected = helper_of(text)

        assert helper.raw_toggle.get_active() is True
        assert saved(editor, collected)["border_color"] == text

    def test_a_table_with_a_fractional_angle_opens_as_text_and_saves_untouched(self) -> None:
        table = {"colors": ["#ff0000"], "angle": 45.5}
        editor, helper, collected = helper_of(table)

        assert helper.raw_toggle.get_active() is True
        assert helper.raw_entry.get_text() == "#ff0000 45.5deg"
        assert saved(editor, collected)["border_color"] is table

    def test_the_pair_text_is_not_parsed_as_markup(self) -> None:
        _, helper, _ = helper_of(PAIR)

        assert helper.raw_entry.get_use_markup() is False

    def test_blank_text_refuses_to_save(self) -> None:
        editor, helper, collected = helper_of(PAIR)
        helper.raw_entry.set_text("")
        editor._save()

        assert collected == []
        assert "Border color effect needs a value" in editor._error.get_label()


class TestModes:
    def test_text_mode_after_an_edit_shows_the_legacy_string(self) -> None:
        _, helper, _ = helper_of(TABLE)
        pick(helper.stop_buttons[0], "#0000ff")
        helper.raw_toggle.set_active(True)

        assert helper.raw_entry.get_text() == "rgba(0000ffff) rgba(00ff00ff) 45deg"

    def test_text_mode_untouched_shows_the_original_in_its_own_spelling(self) -> None:
        _, helper, _ = helper_of({"colors": ["#f00", "rgb(0,255,0)"], "angle": 45})
        helper.raw_toggle.set_active(True)

        assert helper.raw_entry.get_text() == "#f00 rgb(0,255,0) 45deg"

    def test_a_new_effect_starts_as_one_white_stop(self) -> None:
        from hyprtweaker.ui.dialogs.rule_editor import EFFECT_HELPERS

        helper = EFFECT_HELPERS["gradient"](None)

        assert [b.get_rgba().to_string() for b in helper.stop_buttons] == ["rgb(255,255,255)"]
        assert helper.angle.get_value() == 0
        assert helper.value() == {"colors": ["rgba(ffffffff)"], "angle": 0}

    def test_the_row_is_titled_by_the_effect_and_not_parsed_as_markup(self) -> None:
        editor, helper, _ = helper_of(TABLE)

        assert row_of(editor, "border_color").widget.get_title() == "Border color"
        assert helper.widget.get_use_markup() is False
