"""The Config view's shape, checked on a machine with no display.

`plan.py` is the half of page generation that has answers worth asserting -- which Options
appear, under which heading, in what order. Keeping it toolkit-free is what makes "did an
Option go missing?" a question about a tuple. The UI smoke tier then only has to ask whether
GTK still assembles what this planned.
"""

from __future__ import annotations

from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.schema import Visibility, load_schema
from hyprtweaker.ui.pages.plan import (
    PagePlan,
    group_title,
    plan_config_view,
    plan_section,
)

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)


def all_options(plan: PagePlan) -> tuple[str, ...]:
    return tuple(option.name for group in plan.groups for option in group.options)


# --- nothing is lost --------------------------------------------------------------------------


def test_every_section_gets_a_page() -> None:
    plans = plan_config_view(SCHEMA)

    assert tuple(plan.section for plan in plans) == SCHEMA.section_names


def test_a_section_whose_options_are_all_advanced_still_gets_a_page() -> None:
    """`debug`, `quirks`, `experimental`, `input-capture` and `opengl` are entirely
    non-default. The sidebar is the map of the config surface; a Section that disappears
    when a switch flips is one the user cannot learn exists."""
    plan = plan_section(SCHEMA, "debug")

    assert plan.groups == ()
    assert plan.withheld == len(SCHEMA.section("debug"))


def test_with_advanced_on_every_option_in_the_schema_is_on_exactly_one_page() -> None:
    placed = [
        option.name
        for plan in plan_config_view(SCHEMA, show_advanced=True)
        for group in plan.groups
        for option in group.options
    ]

    assert sorted(placed) == sorted(option.name for option in SCHEMA)
    assert len(placed) == len(set(placed))


def test_with_advanced_off_exactly_the_non_default_tiers_are_withheld() -> None:
    plans = plan_config_view(SCHEMA)
    shown = {name for plan in plans for name in all_options(plan)}
    expected = {option.name for option in SCHEMA if option.visibility is Visibility.DEFAULT}

    assert shown == expected
    assert sum(plan.withheld for plan in plans) == len(SCHEMA) - len(expected)


# --- shape ------------------------------------------------------------------------------------


def test_a_page_is_titled_from_the_overlay_not_from_the_config_key() -> None:
    assert plan_section(SCHEMA, "misc").title == "Miscellaneous"
    assert plan_section(SCHEMA, "input-capture").title == "Input capture"


def test_sub_prefixed_options_group_under_their_own_heading() -> None:
    plan = plan_section(SCHEMA, "decoration")
    headings = [group.title for group in plan.groups]

    assert headings[0] == "", "the Section's own options lead, in an untitled group"
    assert "Blur" in headings
    assert "Shadow" in headings

    blur = next(group for group in plan.groups if group.title == "Blur")
    assert all(option.name.startswith("decoration:blur:") for option in blur.options)


def test_a_col_prefix_reads_as_a_word_rather_than_as_a_config_key() -> None:
    assert group_title(SCHEMA["general:col.active_border"]) == "Colors"
    assert group_title(SCHEMA["group:groupbar:col.active"]) == "Groupbar · Colors"


def test_groups_and_rows_follow_hyprlands_own_declaration_order() -> None:
    plan = plan_section(SCHEMA, "decoration")

    firsts = [group.options[0].order for group in plan.groups]
    assert firsts == sorted(firsts)

    for group in plan.groups:
        orders = [option.order for option in group.options]
        assert orders == sorted(orders)


def test_option_count_is_what_the_page_actually_shows() -> None:
    plan = plan_section(SCHEMA, "input")

    assert plan.option_count == len(all_options(plan))
    assert plan.option_count + plan.withheld == len(SCHEMA.section("input"))
