"""The tri-state: Unset, set to a value, set to null.

The distinction the whole writer rests on is Unset vs set-equal-to-default. They render
differently on purpose, so they are asserted here as behaviour rather than as storage.
"""

from __future__ import annotations

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.model import (
    UNSET,
    Color,
    ConfigModel,
    CssGaps,
    Gradient,
    NotNullable,
    UnknownOption,
)
from hyprtweaker.engine.schema import Schema, load_schema


@pytest.fixture(scope="module")
def schema() -> Schema:
    return load_schema(SAMPLE_VERSION, SCHEMA_DIR)


@pytest.fixture
def model(schema: Schema) -> ConfigModel:
    return ConfigModel(schema)


class TestTriState:
    def test_an_untouched_option_is_unset(self, model: ConfigModel) -> None:
        assert model.get("decoration:rounding") is UNSET
        assert not model.is_set("decoration:rounding")

    def test_setting_a_value_equal_to_the_default_still_counts_as_set(
        self, model: ConfigModel
    ) -> None:
        """The point of the tri-state: a deliberate 1 survives an upstream default change."""
        default = model.schema["general:border_size"].default
        model.set("general:border_size", default)

        assert model.is_set("general:border_size")
        assert model.get("general:border_size") == default

    def test_unset_removes_it_again(self, model: ConfigModel) -> None:
        model.set("decoration:rounding", 10)
        model.unset("decoration:rounding")
        assert model.get("decoration:rounding") is UNSET

    def test_unsetting_twice_is_not_an_error(self, model: ConfigModel) -> None:
        model.unset("decoration:rounding")
        model.unset("decoration:rounding")

    def test_explicit_null_is_set_but_has_no_display_text(self, model: ConfigModel) -> None:
        model.set_null("input:kb_variant")

        assert model.is_set("input:kb_variant")
        assert model.get("input:kb_variant") is None
        assert model.display("input:kb_variant") is None

    def test_setting_none_routes_to_null(self, model: ConfigModel) -> None:
        model.set("input:kb_variant", None)
        assert model.get("input:kb_variant") is None

    def test_a_non_nullable_option_refuses_null(self, model: ConfigModel) -> None:
        with pytest.raises(NotNullable):
            model.set_null("decoration:rounding")

    def test_a_null_lua_cannot_spell_becomes_unset(self, model: ConfigModel) -> None:
        """`decoration:shadow:color_inactive` falls back only by *not* being set.

        Its curated null value is `-1`, which is how the C++ declaration says "use the
        related colour" -- `LuaConfigColor` rejects it. Absence is the only Lua spelling,
        so choosing "Same as shadow colour" has to unset.
        """
        model.set_null("decoration:shadow:color_inactive")
        assert model.get("decoration:shadow:color_inactive") is UNSET


class TestTyping:
    def test_values_are_parsed_into_the_option_s_own_type(self, model: ConfigModel) -> None:
        model.set("general:gaps_in", "5 10")
        model.set("general:col.active_border", "ff000000 45deg")
        model.set("group:groupbar:text_color", "#ff8800")

        assert model.get("general:gaps_in") == CssGaps(5, 10, 5, 10)
        assert isinstance(model.get("general:col.active_border"), Gradient)
        assert model.get("group:groupbar:text_color") == Color(0xFFFF8800)

    def test_an_enum_mapped_int_accepts_its_own_name(self, model: ConfigModel) -> None:
        """A `.conf` line and a combo row both speak the name; `descriptions` ships the map."""
        option = model.schema["input:follow_mouse"]
        assert option.map, "expected input:follow_mouse to carry an enum map"
        name, number = next(iter(option.map.items()))

        model.set("input:follow_mouse", name)
        assert model.get("input:follow_mouse") == number

    def test_a_wrong_typed_value_is_refused(self, model: ConfigModel) -> None:
        with pytest.raises(ValueError):
            model.set("decoration:rounding", "very round")

    def test_bounds_are_not_enforced(self, model: ConfigModel) -> None:
        """Deliberate: clamping would silently rewrite a value read from a working config."""
        model.set("decoration:rounding", 9999)
        assert model.get("decoration:rounding") == 9999

    def test_an_unknown_option_is_loud(self, model: ConfigModel) -> None:
        """A typo'd key fails the whole reload with `unknown config key`, not just itself."""
        with pytest.raises(UnknownOption):
            model.set("general:gaps_inn", 5)


class TestOrdering:
    def test_iteration_follows_hyprland_s_declaration_order_not_insertion(
        self, model: ConfigModel
    ) -> None:
        """Content alone decides the output, so two identical configs hash identically."""
        model.set("general:layout", "dwindle")
        model.set("general:border_size", 2)
        model.set("general:gaps_in", 5)

        assert [option.name for option, _ in model.set_options()] == [
            "general:border_size",
            "general:gaps_in",
            "general:layout",
        ]

    def test_sections_lists_only_sections_with_set_options(self, model: ConfigModel) -> None:
        model.set("decoration:rounding", 10)
        model.set("general:gaps_in", 5)

        assert model.sections() == ("general", "decoration")

    def test_section_returns_just_that_section(self, model: ConfigModel) -> None:
        model.set("decoration:rounding", 10)
        model.set("general:gaps_in", 5)

        assert [option.name for option, _ in model.section("decoration")] == [
            "decoration:rounding"
        ]
