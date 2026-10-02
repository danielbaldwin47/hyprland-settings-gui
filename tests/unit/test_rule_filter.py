"""The Rules list's filter: free text and chips, headless (#113, ADR-0008 filter bar)."""

from __future__ import annotations

from hyprtweaker.engine.model.entities import LayerRule, WindowRule
from hyprtweaker.engine.rule_filter import (
    Chip,
    ChipGroup,
    chips_for,
    filter_haystack,
    filter_rules,
)

KITTY = WindowRule(match={"class": "^(kitty)$", "float": True}, effects={"opacity": "0.9"})
HELIUM = WindowRule(
    match={"class": "^(helium)$", "title": "Setup"}, effects={"float": True, "size": "50% 50%"}
)
PLUGIN = WindowRule(match={"plugin:prop": "x"}, effects={"plugin:effect": 1}, name="Terminal")

LISTED = [KITTY, HELIUM, PLUGIN]


def chip(group: ChipGroup, name: str) -> Chip:
    return Chip(group, name)


def test_chips_come_from_the_rules_in_the_list() -> None:
    assert chips_for("window", LISTED) == (
        chip(ChipGroup.MATCH, "class"),
        chip(ChipGroup.MATCH, "title"),
        chip(ChipGroup.MATCH, "float"),
        chip(ChipGroup.MATCH, "plugin:prop"),
        chip(ChipGroup.EFFECT, "float"),
        chip(ChipGroup.EFFECT, "opacity"),
        chip(ChipGroup.EFFECT, "plugin:effect"),
        chip(ChipGroup.EFFECT, "size"),
    )


def test_match_chips_follow_the_catalog_order_and_unknown_ones_sort_after() -> None:
    rules = [WindowRule(match={"zzz": 1, "xwayland": True, "class": "a", "aaa": 1})]
    assert [c.name for c in chips_for("window", rules) if c.group is ChipGroup.MATCH] == [
        "class",
        "xwayland",
        "aaa",
        "zzz",
    ]


def test_a_list_with_no_rules_has_no_chips() -> None:
    assert chips_for("window", []) == ()


def test_layer_rules_get_their_one_match_chip_and_their_effects() -> None:
    rules = [
        LayerRule(match={"namespace": "waybar"}, effects={"blur": True, "ignore_alpha": 0})
    ]
    assert chips_for("layer", rules) == (
        chip(ChipGroup.MATCH, "namespace"),
        chip(ChipGroup.EFFECT, "blur"),
        chip(ChipGroup.EFFECT, "ignore_alpha"),
    )


def test_a_chip_narrows_to_the_rules_that_have_it() -> None:
    assert filter_rules(LISTED, "", {chip(ChipGroup.MATCH, "title")}) == [1]
    assert filter_rules(LISTED, "", {chip(ChipGroup.EFFECT, "float")}) == [1]
    assert filter_rules(LISTED, "", {chip(ChipGroup.MATCH, "float")}) == [0]


def test_two_chips_narrow_further() -> None:
    both = {chip(ChipGroup.MATCH, "class"), chip(ChipGroup.EFFECT, "float")}
    assert filter_rules(LISTED, "", both) == [1]


def test_chips_and_free_text_compose() -> None:
    by_class = {chip(ChipGroup.MATCH, "class")}
    assert filter_rules(LISTED, "", by_class) == [0, 1]
    assert filter_rules(LISTED, "helium", by_class) == [1]
    assert filter_rules(LISTED, "terminal", by_class) == []


def test_free_text_alone_still_narrows_case_insensitively() -> None:
    assert filter_rules(LISTED, "KITTY", set()) == [0]
    assert filter_rules(LISTED, "", set()) == [0, 1, 2]


def test_the_indexes_returned_are_positions_in_the_whole_list() -> None:
    assert filter_rules(LISTED, "terminal", set()) == [2]


def test_the_haystack_covers_label_match_and_effects() -> None:
    rule = WindowRule(match={"class": "Kitty"}, effects={"opacity": "0.9"}, name="Terminal")
    haystack = filter_haystack(rule)
    for needle in ("terminal", "class", "kitty", "opacity", "0.9"):
        assert needle in haystack


def test_a_chip_is_titled_like_its_row_in_the_editor() -> None:
    assert chip(ChipGroup.MATCH, "initial_class").title == "Initial class"
