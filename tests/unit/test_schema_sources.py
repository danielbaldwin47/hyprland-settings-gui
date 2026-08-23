"""Parsing the three schema sources, and the two ways it silently goes wrong."""

from __future__ import annotations

import pytest

from hyprtweaker.engine.schema.sources import (
    lua_key_for,
    parse_descriptions,
    parse_source,
    parse_stub_keys,
    parse_stub_types,
)

# A faithful miniature of ConfigValues.cpp: entries span multiple lines, descriptions
# contain the bracket syntax and parentheses, and only some entries carry a validator.
CONFIG_VALUES_CPP = """
    static const std::vector<SConfigOptionDescription> CONFIG_OPTIONS = {
        MS<Int>("general:border_size", "size of the border around windows", 1,
                {.min = 0, .max = 20, .refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<String>("general:layout", "which layout to use. [dwindle/master]", "dwindle",
                   {.refresh = Supplementary::REFRESH_LAYOUTS}),
        MS<String>("input:accel_profile", "Sets the profile. [adaptive/flat]",
                   STRVAL_EMPTY,
                   {.validator = strChoice({"adaptive", "flat", "custom"}),
                    .refresh = Supplementary::REFRESH_INPUT_DEVICES}),
        MS<Gradient>("decoration:shadow:color_inactive", "inactive shadow color (fallback)", -1,
                     {.refresh = Supplementary::REFRESH_WINDOW_STATES}),
        MS<Float>("input:tablettool:pressure_range_min", "minimum pressure, negative = default",
                  -1.0, {.min = -1.0, .max = 1.0}),
        MS<Vec2>("decoration:shadow:offset", "shadow's rendering offset.", Config::VEC2{},
                 {.validator = vec2Range(-250, -250, 250, 250)}),
        MS<Color>("misc:background_color", "change the background color.", 0xff111111),
    };
"""

CONFIG_VALUES_HPP = """
    inline const std::vector<std::string> CONFIG_DEVICE_VALUE_NAMES = {
        "input:sensitivity",
        "input:accel_profile",
    };
"""

STUB = """
---@alias HL.ConfigKey
---| "general.border_size"
---| "general.layout"
---| "input.touchpad.tap_to_click"

---@class HL.ConfigValueTypes
---@field ['general.border_size'] integer|boolean
---@field ['general.layout'] string
---@field ['input.touchpad.tap_to_click'] boolean

local __HL_ConfigValueTypes = {}
"""


def test_validators_attach_to_their_own_option() -> None:
    """The regression this parser exists for.

    `MS<...>\\("([^"]+)".*?strChoice` looks reasonable and is wrong: the non-greedy run
    crosses entry boundaries, so the first option *before* a validator collects it. On
    0.56.2 that hands `general:layout` the acceleration profiles, and the app then offers
    `flat` as a tiling layout. Only balanced-delimiter splitting associates them correctly.
    """
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)

    assert facts.str_choices == {"input:accel_profile": ("adaptive", "flat", "custom")}
    assert "general:layout" not in facts.str_choices

    assert list(facts.vec2_ranges) == ["decoration:shadow:offset"]
    assert facts.vec2_ranges["decoration:shadow:offset"].max_x == 250.0


def test_ms_types_cover_every_entry() -> None:
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)
    assert facts.ms_type["misc:background_color"] == "Color"
    assert facts.ms_type["general:border_size"] == "Int"
    assert len(facts.ms_type) == 7


@pytest.mark.parametrize(
    "name",
    ["decoration:shadow:color_inactive", "input:tablettool:pressure_range_min"],
)
def test_negative_one_defaults_are_found_in_every_spelling(name: str) -> None:
    """`-1` and `-1.0` are the same sentinel; only one of them is spelled `-1`."""
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)
    assert name in facts.negative_one_defaults


def test_a_plain_default_is_not_a_sentinel() -> None:
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)
    assert "general:border_size" not in facts.negative_one_defaults
    assert "misc:background_color" not in facts.negative_one_defaults


def test_refresh_bits_are_deduplicated_and_sorted() -> None:
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)
    assert facts.refresh["general:border_size"] == ("REFRESH_WINDOW_STATES",)


def test_device_overridable_names_come_from_the_header() -> None:
    facts = parse_source(CONFIG_VALUES_CPP, CONFIG_VALUES_HPP)
    assert facts.device_overridable == frozenset({"input:sensitivity", "input:accel_profile"})


def test_parse_source_rejects_a_file_with_no_options() -> None:
    """A silently empty SourceFacts degrades every Color to string without saying so."""
    with pytest.raises(ValueError, match=r"ConfigValues\.cpp"):
        parse_source("int main() { return 0; }", CONFIG_VALUES_HPP)


def test_stub_types_and_keys() -> None:
    assert parse_stub_types(STUB)["general.border_size"] == "integer|boolean"
    assert "input.touchpad.tap_to_click" in parse_stub_keys(STUB)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("general:border_size", "general.border_size"),
        ("input:touchpad:tap-to-click", "input.touchpad.tap_to_click"),
        ("input-capture:capture_modifiers", "input_capture.capture_modifiers"),
    ],
)
def test_lua_key_rewrite(source: str, expected: str) -> None:
    """Only four keys on 0.56.2 contain a dash, which is why this is easy to get wrong."""
    assert lua_key_for(source) == expected


def test_descriptions_must_be_a_non_empty_list() -> None:
    with pytest.raises(ValueError):
        parse_descriptions("[]")
    with pytest.raises(ValueError):
        parse_descriptions('{"name": "x"}')


def test_descriptions_entries_must_have_a_name() -> None:
    with pytest.raises(ValueError, match="malformed"):
        parse_descriptions('[{"default": 1}]')
