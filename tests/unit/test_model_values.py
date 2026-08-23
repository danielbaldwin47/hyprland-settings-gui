"""The three Value representations, per Option type.

External behaviour only: a value goes in as display text, comes out as display text, as a
Lua literal, and as a `getoption` parse. The invariant worth the most is the round trip --
`parse_value(display_text(v)) == v` -- because it is what lets the importer, the Schema's
defaults and the UI all speak the same strings without knowing about each other.
"""

from __future__ import annotations

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.model import (
    Color,
    CssGaps,
    FontWeight,
    Gradient,
    Vec2,
    display_text,
    lua_literal,
    lua_string,
    parse_getoption,
    parse_value,
)
from hyprtweaker.engine.schema import OptionType, Schema, load_schema


class TestColor:
    @pytest.mark.parametrize(
        ("text", "argb"),
        [
            ("ff1a2b3c", 0xFF1A2B3C),  # descriptions: bare hex8 is ARGB
            ("0xff1a2b3c", 0xFF1A2B3C),
            ("rgba(1a2b3cff)", 0xFF1A2B3C),  # rgba(): alpha last
            ("rgb(1a2b3c)", 0xFF1A2B3C),
            ("#1a2b3c", 0xFF1A2B3C),
            ("#1a2b3cff", 0xFF1A2B3C),
            ("#abc", 0xFFAABBCC),
            ("rgba(26, 43, 60, 1.0)", 0xFF1A2B3C),
            ("rgb(26, 43, 60)", 0xFF1A2B3C),
        ],
    )
    def test_every_spelling_lands_on_the_same_colour(self, text: str, argb: int) -> None:
        assert Color.parse(text) == Color(argb)

    def test_bare_hex_is_argb_and_rgba_is_not(self) -> None:
        """The one confusion that turns an opaque colour transparent."""
        assert Color.parse("ff444444") != Color.parse("rgba(ff444444)")
        assert Color.parse("ff444444") == Color.parse("rgba(444444ff)")

    def test_display_round_trips(self) -> None:
        assert str(Color.parse("ee1a1a1a")) == "ee1a1a1a"

    def test_lua_is_a_quoted_rgba_string(self) -> None:
        """A gradient stop goes through the string parser, so colours are never numbers."""
        assert Color.parse("ee1a1a1a").lua() == '"rgba(1a1a1aee)"'

    def test_getoption_reads_the_packed_integer(self) -> None:
        assert Color.from_getoption(0xEE1A1A1A) == Color.parse("ee1a1a1a")

    @pytest.mark.parametrize("bad", ["-1", "nope", "#12", "rgba(1,2,3)", True])
    def test_rejects_non_colours(self, bad: object) -> None:
        with pytest.raises(ValueError):
            Color.parse(bad)


class TestGradient:
    def test_parses_the_descriptions_form(self) -> None:
        assert Gradient.parse("ff444444 0deg") == Gradient((Color(0xFF444444),), 0.0)

    def test_parses_several_stops_and_an_angle(self) -> None:
        gradient = Gradient.parse("rgba(33ccffee) rgba(00ff99ee) 45deg")
        assert gradient.angle == 45.0
        assert len(gradient.colors) == 2

    def test_an_angle_is_optional(self) -> None:
        assert Gradient.parse("ff444444").angle == 0.0

    def test_lua_is_a_table_never_the_display_text(self) -> None:
        """ADR-0005's headline constraint: `toString()` text is not readable by Lua."""
        gradient = Gradient.parse("rgba(33ccffee) rgba(00ff99ee) 45deg")
        assert gradient.lua() == (
            '{ colors = { "rgba(33ccffee)", "rgba(00ff99ee)" }, angle = 45 }'
        )

    def test_display_round_trips(self) -> None:
        text = "33ccffee 00ff99ee 45deg"
        assert str(Gradient.parse(text)) == text

    def test_getoption_accepts_both_engines(self) -> None:
        """Structured under the Lua engine, a `custom` string under hyprlang."""
        structured = Gradient.from_getoption({"colors": ["ff000000"], "angle": 90})
        assert structured == Gradient.from_getoption("ff000000 90deg")

    def test_rejects_an_angle_with_no_colours(self) -> None:
        with pytest.raises(ValueError):
            Gradient.parse("45deg")


class TestCssGaps:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("5", CssGaps(5, 5, 5, 5)),
            ("5 10", CssGaps(5, 10, 5, 10)),
            ("5 10 15", CssGaps(5, 10, 15, 10)),
            ("5 10 15 20", CssGaps(5, 10, 15, 20)),
        ],
    )
    def test_css_shorthand(self, text: str, expected: CssGaps) -> None:
        assert CssGaps.parse(text) == expected

    def test_lua_is_always_the_four_key_table(self) -> None:
        """One shape, uniform or not: a value that changes syntax is unreadable in a diff."""
        assert CssGaps.parse("5").lua() == "{ top = 5, right = 5, bottom = 5, left = 5 }"

    def test_display_round_trips(self) -> None:
        assert str(CssGaps.parse("5 10 15 20")) == "5 10 15 20"

    def test_getoption_reads_the_four_sides(self) -> None:
        payload = {"top": 5, "right": 10, "bottom": 15, "left": 20}
        assert CssGaps.from_getoption(payload) == CssGaps(5, 10, 15, 20)

    def test_rejects_five_values(self) -> None:
        with pytest.raises(ValueError):
            CssGaps.parse("1 2 3 4 5")


class TestVec2:
    def test_parses_both_separators_and_the_json_array(self) -> None:
        assert Vec2.parse("1 2") == Vec2.parse("1, 2") == Vec2.parse([1, 2]) == Vec2(1.0, 2.0)

    def test_lua_is_the_array_form(self) -> None:
        """`LuaConfigVec2` reads indices 1 and 2 only; `{x=..., y=...}` silently yields 0,0."""
        assert Vec2(0, 2).lua() == "{ 0, 2 }"

    def test_whole_numbers_lose_the_decimal_point(self) -> None:
        assert str(Vec2(0.0, 2.0)) == "0 2"
        assert Vec2(0.5, 2.0).lua() == "{ 0.5, 2 }"

    def test_getoption_prefers_the_named_fields(self) -> None:
        assert Vec2.from_getoption({"x": 1.0, "y": 2.0}) == Vec2(1.0, 2.0)


class TestFontWeight:
    def test_a_number_stays_a_number_and_a_name_stays_quoted(self) -> None:
        assert FontWeight.parse("400").lua() == "400"
        assert FontWeight.parse("bold").lua() == '"bold"'

    def test_display_round_trips(self) -> None:
        assert str(FontWeight.parse("bold")) == "bold"
        assert str(FontWeight.parse(700)) == "700"


class TestScalars:
    @pytest.mark.parametrize(
        ("option_type", "raw", "expected"),
        [
            (OptionType.BOOL, "true", True),
            (OptionType.BOOL, 0, False),
            (OptionType.INT, "10", 10),
            (OptionType.INT, "0x1f", 31),
            (OptionType.FLOAT, "0.5", 0.5),
            (OptionType.STRING, "dwindle", "dwindle"),
        ],
    )
    def test_parse(self, option_type: OptionType, raw: object, expected: object) -> None:
        assert parse_value(option_type, raw) == expected

    def test_a_whole_float_prints_without_a_decimal_point(self) -> None:
        """`descriptions` prints `1`, so the writer does too -- one value, one spelling."""
        assert display_text(1.0) == "1"
        assert lua_literal(1.0) == "1"
        assert lua_literal(0.5) == "0.5"

    def test_booleans_are_lua_keywords_not_numbers(self) -> None:
        assert lua_literal(True) == "true"

    @pytest.mark.parametrize(
        ("option_type", "bad"),
        [(OptionType.INT, "wide"), (OptionType.BOOL, "maybe"), (OptionType.FLOAT, None)],
    )
    def test_a_wrong_typed_value_is_refused_at_the_door(
        self, option_type: OptionType, bad: object
    ) -> None:
        with pytest.raises(ValueError):
            parse_value(option_type, bad)


class TestLuaStrings:
    def test_quotes_and_backslashes_are_escaped(self) -> None:
        assert lua_string('a"b\\c') == '"a\\"b\\\\c"'

    def test_newlines_do_not_break_the_literal(self) -> None:
        assert lua_string("a\nb") == '"a\\nb"'


class TestGetOptionReplies:
    """The third representation: one `hyprctl -j getoption` reply, keyed by type."""

    @pytest.mark.parametrize(
        ("name", "payload", "expected"),
        [
            ("decoration:rounding", {"int": 10}, 10),
            ("decoration:active_opacity", {"float": 0.95}, 0.95),
            ("general:layout", {"str": "dwindle"}, "dwindle"),
            ("general:resize_on_border", {"int": 1}, True),
            ("group:groupbar:text_color", {"int": 0xFFFF8800}, Color(0xFFFF8800)),
            ("decoration:shadow:offset", {"vec2": {"x": 0.0, "y": 2.0}}, Vec2(0.0, 2.0)),
            (
                "general:gaps_in",
                {"css": {"top": 5, "right": 5, "bottom": 5, "left": 5}},
                CssGaps(5, 5, 5, 5),
            ),
            (
                "general:col.active_border",
                {"gradient": {"colors": ["ff000000"], "angle": 90}},
                Gradient((Color(0xFF000000),), 90.0),
            ),
        ],
    )
    def test_each_type_reads_its_own_key(
        self, schema: Schema, name: str, payload: dict[str, object], expected: object
    ) -> None:
        assert parse_getoption(schema[name], payload) == expected

    def test_the_hyprlang_custom_fallback_still_parses(self, schema: Schema) -> None:
        """Under hyprlang the three complex types answer as `custom`, not as themselves."""
        option = schema["general:col.active_border"]
        assert parse_getoption(option, {"custom": "ff000000 0deg"}) == Gradient.parse(
            "ff000000 0deg"
        )

    def test_a_reply_missing_its_key_is_an_error_not_a_default(self, schema: Schema) -> None:
        """A silent default here would badge a Row "modified" for a value nobody set."""
        with pytest.raises(KeyError):
            parse_getoption(schema["decoration:rounding"], {"set": True})


@pytest.fixture(scope="module")
def schema() -> Schema:
    return load_schema(SAMPLE_VERSION, SCHEMA_DIR)
