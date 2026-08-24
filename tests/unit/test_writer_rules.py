"""The rules Modules: rendering, the golden shape, and the read-back (ADR-0008, #67)."""

from __future__ import annotations

from pathlib import Path

import pytest
from _golden import assert_matches_golden

from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.model.entities import LayerRule, WindowRule
from hyprtweaker.engine.writer.rules import (
    parse_rules_module,
    render_layer_rule,
    render_layer_rules_module,
    render_window_rule,
    render_window_rules_module,
)

VERSION = "0.1.0"

GOLDEN = Path(__file__).parent.parent / "golden" / "writer"


class TestRenderWindowRule:
    def test_minimal_rule(self) -> None:
        rule = WindowRule(match={"class": "^(kitty)$"}, effects={"float": True})
        assert (
            render_window_rule(rule)
            == 'hl.window_rule({ match = { class = "^(kitty)$" }, float = true })'
        )

    def test_named_rule_puts_name_first(self) -> None:
        rule = WindowRule(match={"class": "x"}, effects={"opacity": "0.9"}, name="dim")
        assert render_window_rule(rule) == (
            'hl.window_rule({ name = "dim", match = { class = "x" }, opacity = "0.9" })'
        )

    def test_disabled_rule_stays_a_call(self) -> None:
        """Disabling is `enabled = false` in the file, never a comment (ADR-0008)."""
        rule = WindowRule(match={"class": "x"}, effects={"float": True}, enabled=False)
        assert render_window_rule(rule) == (
            'hl.window_rule({ enabled = false, match = { class = "x" }, float = true })'
        )

    def test_enabled_is_not_emitted_when_true(self) -> None:
        assert "enabled" not in render_window_rule(WindowRule(match={"class": "x"}))

    def test_table_valued_effect(self) -> None:
        """`size` may hold a vec2 table from a Lua import; it must stay a table."""
        rule = WindowRule(match={"class": "x"}, effects={"size": ["50%", "50%"]})
        assert (
            render_window_rule(rule)
            == 'hl.window_rule({ match = { class = "x" }, size = { "50%", "50%" } })'
        )

    def test_gradient_effect_renders_as_table(self) -> None:
        rule = WindowRule(
            match={"class": "x"},
            effects={"border_color": {"colors": ["#ff0000", "#00ff00"], "angle": 45}},
        )
        rendered = render_window_rule(rule)
        assert 'border_color = { colors = { "#ff0000", "#00ff00" }, angle = 45 }' in rendered

    def test_unknown_effect_key_needing_quoting(self) -> None:
        """A plugin effect key with a dash cannot be a bare Lua identifier."""
        rule = WindowRule(match={"class": "x"}, effects={"plugin:my-effect": "on"})
        assert '["plugin:my-effect"] = "on"' in render_window_rule(rule)

    def test_regex_with_backslashes_survives(self) -> None:
        rule = WindowRule(match={"class": "^(blueberry\\.py)$"})
        assert "blueberry\\\\.py" in render_window_rule(rule)

    def test_negated_match_value_passes_through(self) -> None:
        rule = WindowRule(match={"class": "negative:^(kitty)$"})
        assert 'class = "negative:^(kitty)$"' in render_window_rule(rule)


class TestRenderLayerRule:
    def test_layer_rule(self) -> None:
        rule = LayerRule(
            match={"namespace": "rofi"}, effects={"blur": True, "ignore_alpha": 0.5}
        )
        assert render_layer_rule(rule) == (
            'hl.layer_rule({ match = { namespace = "rofi" }, blur = true, ignore_alpha = 0.5 })'
        )


class TestRenderModules:
    def test_no_rules_renders_no_module(self) -> None:
        assert render_window_rules_module([], app_version=VERSION) is None
        assert render_layer_rules_module([], app_version=VERSION) is None

    def test_order_is_kept_exactly(self) -> None:
        """The renderer never sorts -- later wins, and position is identity."""
        rules = [
            WindowRule(match={"class": "b"}, effects={"float": True}),
            WindowRule(match={"class": "a"}, effects={"float": False}, name="named"),
        ]
        text = render_window_rules_module(rules, app_version=VERSION)
        assert text is not None
        assert text.index('class = "b"') < text.index('class = "a"')

    def test_golden_window_rules(self) -> None:
        rules = [
            WindowRule(match={"class": "^(kitty)$"}, effects={"float": True, "center": True}),
            WindowRule(
                match={"title": "^(Picture-in-Picture)$", "float": True},
                effects={"pin": True, "opacity": "0.9 override"},
                name="pip",
            ),
            WindowRule(
                match={"class": "negative:^(firefox)$"},
                effects={"no_blur": True},
                enabled=False,
            ),
            WindowRule(
                match={"workspace": "special:scratch"},
                effects={"plugin:hy3:tab": "on", "size": ["50%", "50%"]},
            ),
        ]
        text = render_window_rules_module(rules, app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "rules-window.lua", "window_rules.lua")

    def test_golden_layer_rules(self) -> None:
        rules = [
            LayerRule(match={"namespace": "^(rofi)$"}, effects={"blur": True}),
            LayerRule(
                match={"namespace": "waybar"},
                effects={"ignore_alpha": 0.2, "order": 1},
                name="bar",
                enabled=False,
            ),
        ]
        text = render_layer_rules_module(rules, app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "rules-layer.lua", "layer_rules.lua")


@pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter")
class TestRoundTrip:
    def test_window_rules_round_trip(self) -> None:
        rules = [
            WindowRule(match={"class": "^(kitty)$"}, effects={"float": True}),
            WindowRule(
                match={"title": "x", "float": True},
                effects={"opacity": "0.9", "size": ["50%", "50%"]},
                name="pip",
            ),
            WindowRule(match={"class": "y"}, effects={"no_blur": True}, enabled=False),
        ]
        text = render_window_rules_module(rules, app_version=VERSION)
        assert text is not None
        parsed = parse_rules_module(text)
        assert parsed.ok, parsed.errors
        assert [
            (dict(r.match), dict(r.effects), r.name, r.enabled) for r in parsed.window_rules
        ] == [(dict(r.match), dict(r.effects), r.name, r.enabled) for r in rules]

    def test_unknown_effects_survive_untouched(self) -> None:
        """Acceptance: unknown effects survive edit round-trips (ADR-0008, #67)."""
        rules = [
            WindowRule(
                match={"class": "x"},
                effects={"plugin:hy3:tab": "on", "some_future_effect": 3},
            )
        ]
        text = render_window_rules_module(rules, app_version=VERSION)
        assert text is not None
        parsed = parse_rules_module(text)
        assert parsed.ok, parsed.errors
        assert dict(parsed.window_rules[0].effects) == {
            "plugin:hy3:tab": "on",
            "some_future_effect": 3,
        }

    def test_layer_rules_round_trip(self) -> None:
        rules = [
            LayerRule(match={"namespace": "rofi"}, effects={"blur": True}),
            LayerRule(match={"namespace": "bar"}, effects={"order": 1}, enabled=False),
        ]
        text = render_layer_rules_module(rules, app_version=VERSION)
        assert text is not None
        parsed = parse_rules_module(text, module="layer_rules.lua")
        assert parsed.ok, parsed.errors
        assert [(dict(r.match), dict(r.effects), r.enabled) for r in parsed.layer_rules] == [
            (dict(r.match), dict(r.effects), r.enabled) for r in rules
        ]

    def test_a_hand_written_loop_reads_back(self) -> None:
        """The file is the interface: arbitrary Lua producing rules is adopted, not lost."""
        text = (
            "for _, class in ipairs({ 'a', 'b' }) do\n"
            "  hl.window_rule({ match = { class = class }, float = true })\n"
            "end\n"
        )
        parsed = parse_rules_module(text)
        assert parsed.ok, parsed.errors
        assert [r.match["class"] for r in parsed.window_rules] == ["a", "b"]

    def test_misfiled_layer_rule_still_comes_back(self) -> None:
        """A layer rule hand-added to `window_rules.lua` is adopted as what it is."""
        text = 'hl.layer_rule({ match = { namespace = "rofi" }, blur = true })\n'
        parsed = parse_rules_module(text)
        assert parsed.ok, parsed.errors
        assert len(parsed.layer_rules) == 1
