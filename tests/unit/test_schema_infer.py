"""Type and widget inference (rules R1-R13) over `descriptions`-shaped records."""

from __future__ import annotations

from typing import Any

import pytest

from hyprtweaker.engine.schema import CurationFlag, OptionType, Vec2Range, Widget
from hyprtweaker.engine.schema.infer import (
    INT32_MAX,
    build_option,
    curation_flags,
    getoption_key,
    infer_type,
    infer_widget,
    normalise_default,
)
from hyprtweaker.engine.schema.sources import SourceFacts


def record(name: str = "sec:opt", **fields: Any) -> dict[str, Any]:
    return {"name": name, "description": "", **fields}


# --- type inference --------------------------------------------------------------------


def test_bool_beats_int_because_python_bools_are_ints() -> None:
    assert infer_type(record(default=True), "boolean", "Bool") is OptionType.BOOL


def test_int_is_identified_by_the_map_key_not_the_number_type() -> None:
    assert infer_type(record(default=1, min=0, max=20, map=None), None, "Int") is OptionType.INT


def test_a_whole_number_float_is_still_a_float() -> None:
    """The 21-option trap: Floats with whole-number defaults print as `1`, not `1.0`.

    `decoration:shadow:scale` defaults to 1 and is a Float; typing it on the JSON number
    type makes it an int spinner that rounds every fractional value the user picks. The
    `map` key -- present for Int, absent for Float -- is the only reliable discriminator.
    """
    shape = record("decoration:shadow:scale", default=1, min=0, max=1)
    assert infer_type(shape, "number|boolean", "Float") is OptionType.FLOAT
    assert infer_widget(OptionType.FLOAT, shape, None) is Widget.FLOAT_RANGE


def test_vec2_and_css_gaps() -> None:
    assert infer_type(record(default=[0, 0]), "HL.Vec2Like", "Vec2") is OptionType.VEC2
    gaps = record(default="5 5 5 5", min=None, max=None)
    assert infer_type(gaps, "integer|HL.CssGap", "CssGap") is OptionType.CSS_GAPS


@pytest.mark.parametrize(
    ("stub_type", "ms_type", "expected"),
    [
        ("string|HL.Gradient", "Gradient", OptionType.GRADIENT),
        ("integer|string", "FontWeight", OptionType.FONT_WEIGHT),
        ("string", "Color", OptionType.COLOR),
        ("string", "String", OptionType.STRING),
    ],
)
def test_the_four_types_that_print_as_a_bare_string(
    stub_type: str, ms_type: str, expected: OptionType
) -> None:
    assert infer_type(record(default="x"), stub_type, ms_type) is expected


def test_color_degrades_to_string_without_the_source() -> None:
    """`MS<Color>` is the only place Color is distinguishable; the Overlay covers the gap."""
    assert infer_type(record(default="ff111111"), "string", None) is OptionType.STRING


def test_an_unrecognised_default_shape_is_an_error() -> None:
    with pytest.raises(ValueError, match="unrecognised default shape"):
        infer_type(record(default={"unexpected": True}), None, None)


# --- widget inference ------------------------------------------------------------------


def test_enum_map_wins_over_range_for_ints_with_a_map() -> None:
    shape = record(default=0, min=0, max=3, map=[{"off": 0}, {"on": 1}])
    assert infer_widget(OptionType.INT, shape, None) is Widget.ENUM_MAP


def test_an_int_max_ceiling_is_not_a_slider() -> None:
    shape = record(default=0, min=0, max=INT32_MAX, map=None)
    assert infer_widget(OptionType.INT, shape, None) is Widget.FREE_INT


def test_an_unbounded_int_is_not_a_slider() -> None:
    assert infer_widget(OptionType.INT, record(default=0, min=0, max=None, map=None), None) is (
        Widget.FREE_INT
    )


def test_a_string_with_choices_becomes_a_combo() -> None:
    assert (
        infer_widget(OptionType.STRING, record(default="a"), ("a", "b")) is Widget.ENUM_STRING
    )
    assert infer_widget(OptionType.STRING, record(default="a"), None) is Widget.STRING


# --- sentinels -------------------------------------------------------------------------


@pytest.mark.parametrize("sentinel", ["[[EMPTY]]", "[[Auto]]"])
def test_string_sentinels_resolve_to_no_value(sentinel: str) -> None:
    assert normalise_default(OptionType.STRING, sentinel, False) == (None, True)


def test_a_colour_sentinel_prints_as_minus_one() -> None:
    assert normalise_default(OptionType.COLOR, "-1", False) == (None, True)


def test_a_gradient_sentinel_is_invisible_without_the_source() -> None:
    """`MS<Gradient>(..., -1, ...)` prints a plausible colour that is not the default."""
    assert normalise_default(OptionType.GRADIENT, "ffffffff 0deg", False) == (
        "ffffffff 0deg",
        False,
    )
    assert normalise_default(OptionType.GRADIENT, "ffffffff 0deg", True) == (None, True)


def test_a_numeric_minus_one_is_flagged_rather_than_resolved() -> None:
    """-1 means "unset" for pressure_range and "random" for force_default_wallpaper.

    Nothing machine-readable separates those, so inference refuses to guess and the
    Overlay decides.
    """
    assert normalise_default(OptionType.FLOAT, -1, True) == (-1, False)

    flags = curation_flags(
        OptionType.FLOAT,
        Widget.FLOAT_RANGE,
        record(default=-1, min=-1, max=1),
        sentinel_default=False,
        declared_negative_one=True,
        vec2_range=None,
    )
    assert CurationFlag.NEEDS_NULLABLE in flags


def test_a_numeric_minus_one_is_flagged_from_the_record_alone() -> None:
    """So a degraded run -- no C++ consulted -- still raises the question."""
    flags = curation_flags(
        OptionType.INT,
        Widget.INT_RANGE,
        record(default=-1, min=-1, max=2, map=None),
        sentinel_default=False,
        declared_negative_one=False,
        vec2_range=None,
    )
    assert CurationFlag.NEEDS_NULLABLE in flags


# --- curation flags --------------------------------------------------------------------


def test_a_narrow_map_less_int_is_an_enum_in_disguise() -> None:
    flags = curation_flags(
        OptionType.INT,
        Widget.INT_RANGE,
        record(default=0, min=0, max=2, map=None),
        False,
        False,
        None,
    )
    assert CurationFlag.NEEDS_LABELS in flags


def test_a_wide_int_range_is_a_quantity_not_an_enum() -> None:
    flags = curation_flags(
        OptionType.INT,
        Widget.INT_RANGE,
        record(default=1, min=0, max=20, map=None),
        False,
        False,
        None,
    )
    assert CurationFlag.NEEDS_LABELS not in flags


def test_an_int_with_a_map_needs_no_labels() -> None:
    """Enum maps were the one widget class prototype #8 measured at a perfect score."""
    flags = curation_flags(
        OptionType.INT,
        Widget.ENUM_MAP,
        record(default=0, min=0, max=2, map=[{"off": 0}]),
        False,
        False,
        None,
    )
    assert CurationFlag.NEEDS_LABELS not in flags


def test_every_plain_string_row_asks_for_a_decision() -> None:
    """25 of the 28 plain strings are really pickers (research #3 §3.3)."""
    flags = curation_flags(
        OptionType.STRING, Widget.STRING, record(default="x"), False, False, None
    )
    assert CurationFlag.NEEDS_WIDGET in flags


def test_a_vec2_without_source_bounds_needs_a_range() -> None:
    shape = record(default=[0, 0])
    assert CurationFlag.NEEDS_RANGE in curation_flags(
        OptionType.VEC2, Widget.VEC2, shape, False, False, None
    )
    assert CurationFlag.NEEDS_RANGE not in curation_flags(
        OptionType.VEC2, Widget.VEC2, shape, False, False, Vec2Range(-1, -1, 1, 1)
    )


# --- getoption keys --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("option_type", "expected"),
    [
        (OptionType.BOOL, "int"),
        (OptionType.COLOR, "int"),
        (OptionType.FLOAT, "float"),
        (OptionType.STRING, "str"),
        (OptionType.VEC2, "vec2"),
        (OptionType.GRADIENT, "gradient"),
        (OptionType.CSS_GAPS, "css"),
        (OptionType.FONT_WEIGHT, "font_weight"),
    ],
)
def test_getoption_keys_are_the_lua_engines(option_type: OptionType, expected: str) -> None:
    """Under hyprlang these three come back as `custom`; under Lua they do fire."""
    assert getoption_key(option_type) == expected


# --- build_option ----------------------------------------------------------------------


def test_enum_maps_are_sorted_by_value() -> None:
    """`descriptions` emits the map in unordered_map order, so combo order would drift."""
    option = build_option(
        record("misc:vrr", default=0, min=0, max=3, map=[{"on": 1}, {"off": 0}, {"fs": 2}]),
        order=0,
        version="0.56.2",
        stub_types={},
        facts=SourceFacts(),
    )
    assert option.map is not None
    assert list(option.map.items()) == [("off", 0), ("on", 1), ("fs", 2)]


def test_choices_fall_back_to_the_description_bracket_list() -> None:
    option = build_option(
        record(
            "general:layout", default="dwindle", description="use. [dwindle/master/monocle]"
        ),
        order=0,
        version="0.56.2",
        stub_types={},
        facts=SourceFacts(),
    )
    assert option.choices == ("dwindle", "master", "monocle")
    assert option.widget is Widget.ENUM_STRING


def test_source_choices_beat_the_description() -> None:
    option = build_option(
        record("input:accel_profile", default="x", description="[a/b]"),
        order=0,
        version="0.56.2",
        stub_types={},
        facts=SourceFacts(str_choices={"input:accel_profile": ("adaptive", "flat")}),
    )
    assert option.choices == ("adaptive", "flat")


def test_path_and_section_come_from_the_lua_key() -> None:
    option = build_option(
        record("input:touchpad:tap-to-click", default=True),
        order=7,
        version="0.56.2",
        stub_types={},
        facts=SourceFacts(),
    )
    assert option.section == "input"
    assert option.path == ("input", "touchpad", "tap_to_click")
    assert option.lua_key == "input.touchpad.tap_to_click"
    assert option.order == 7
