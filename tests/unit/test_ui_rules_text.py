"""The Rules Page's pure text helpers, headless (#67).

The deliberate split the Binds Page established: every string a Row shows is settled here
with no display, and the smoke tier only checks that widgets assemble.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi", reason="the Rules Page imports gi at module scope")

from hyprtweaker.engine.model.entities import LayerRule, WindowRule
from hyprtweaker.engine.rules_catalog import is_negated, prop_title, strip_negation
from hyprtweaker.ui.pages.rules import (
    effects_text,
    filter_haystack,
    match_text,
    rule_subtitle,
    rule_title,
)


class TestNegation:
    def test_detects_the_prefix(self) -> None:
        assert is_negated("negative:^(kitty)$")
        assert not is_negated("^(kitty)$")
        assert not is_negated(True)

    def test_strips_only_the_prefix(self) -> None:
        assert strip_negation("negative:x") == "x"
        assert strip_negation("x") == "x"


class TestTitles:
    def test_prop_title(self) -> None:
        assert prop_title("initial_class") == "Initial class"
        assert prop_title("class") == "Class"

    def test_label_wins_as_title(self) -> None:
        rule = WindowRule(match={"class": "x"}, effects={"float": True}, name="terminal")
        assert rule_title(rule) == "terminal"
        assert rule_subtitle(rule) == "class x → float"

    def test_anonymous_rule_titles_with_the_summary(self) -> None:
        rule = WindowRule(match={"class": "kitty"}, effects={"float": True, "opacity": "0.9"})
        assert rule_title(rule) == "class kitty → float, opacity 0.9"
        assert rule_subtitle(rule) == ""


class TestSummaries:
    def test_match_text_spells_negation_as_not(self) -> None:
        rule = WindowRule(match={"class": "negative:^(kitty)$", "float": True})
        assert match_text(rule) == "not class ^(kitty)$ · float yes"

    def test_effects_text_bools_by_bare_name(self) -> None:
        rule = WindowRule(
            match={"class": "x"},
            effects={"float": True, "decorate": False, "rounding": 4},
        )
        assert effects_text(rule) == "float, decorate off, rounding 4"

    def test_table_valued_effect_summarises(self) -> None:
        rule = WindowRule(match={"class": "x"}, effects={"size": ["50%", "50%"]})
        assert effects_text(rule) == "size 50% 50%"

    def test_layer_rule_summarises_the_same_way(self) -> None:
        rule = LayerRule(match={"namespace": "rofi"}, effects={"blur": True})
        assert rule_title(rule) == "namespace rofi → blur"


class TestFilterHaystack:
    def test_covers_label_match_and_effects(self) -> None:
        rule = WindowRule(match={"class": "Kitty"}, effects={"opacity": "0.9"}, name="Terminal")
        haystack = filter_haystack(rule)
        for needle in ("terminal", "class", "kitty", "opacity", "0.9"):
            assert needle in haystack
