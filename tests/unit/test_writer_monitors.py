"""The monitors and workspace-rules Modules: rendering, goldens, read-back (ADR-0008, #68)."""

from __future__ import annotations

from pathlib import Path

import pytest
from _golden import assert_matches_golden

from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.model.entities import EntitySet, MonitorRule, WorkspaceRule
from hyprtweaker.engine.writer.monitors import (
    parse_monitors_module,
    render_monitor_rule,
    render_monitors_module,
    render_workspace_rule,
    render_workspace_rules_module,
)

VERSION = "0.1.0"

GOLDEN = Path(__file__).parent.parent / "golden" / "writer"

EVERY_ROW_FIELD = MonitorRule(
    output="DP-2",
    fields={
        "mode": "modeline 148.5 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync",
        "scale": 1.6,
        "reserved": {"top": 32, "right": 0, "bottom": 0, "left": 8},
        "cm": "hdredid",
        "sdr_eotf": "gamma22force",
        "sdrbrightness": 1.4,
        "sdrsaturation": 0.9,
        "supports_wide_color": 1,
        "supports_hdr": 0,
        "sdr_min_luminance": 0.005,
        "sdr_max_luminance": 250,
        "min_luminance": 0.05,
        "max_luminance": 1000,
        "max_avg_luminance": 400,
    },
)
"""A rule using every field the Monitors rows added in #193 (the static tier verifies it)."""


class TestRenderMonitorRule:
    def test_identity_comes_first(self) -> None:
        rule = MonitorRule(
            output="DP-1", fields={"mode": "1920x1080@60", "position": "0x0", "scale": 1.5}
        )
        assert render_monitor_rule(rule) == (
            'hl.monitor({ output = "DP-1", mode = "1920x1080@60", '
            'position = "0x0", scale = 1.5 })'
        )

    def test_catch_all_is_a_value(self) -> None:
        """`output = ""` is the "Any other display" rule, emitted, never skipped."""
        rule = MonitorRule(output="", fields={"mode": "preferred"})
        assert render_monitor_rule(rule) == 'hl.monitor({ output = "", mode = "preferred" })'

    def test_desc_identity_survives(self) -> None:
        rule = MonitorRule(output="desc:BOE 0x0791", fields={"scale": "auto"})
        assert (
            render_monitor_rule(rule)
            == 'hl.monitor({ output = "desc:BOE 0x0791", scale = "auto" })'
        )

    def test_reserved_renders_as_named_table(self) -> None:
        rule = MonitorRule(
            output="DP-1", fields={"reserved": {"top": 30, "right": 0, "bottom": 0, "left": 0}}
        )
        assert render_monitor_rule(rule) == (
            'hl.monitor({ output = "DP-1", '
            "reserved = { top = 30, right = 0, bottom = 0, left = 0 } })"
        )

    def test_disabled_is_a_field(self) -> None:
        rule = MonitorRule(output="HDMI-A-1", fields={"disabled": True})
        assert (
            render_monitor_rule(rule) == 'hl.monitor({ output = "HDMI-A-1", disabled = true })'
        )


class TestRenderWorkspaceRule:
    def test_selector_comes_first(self) -> None:
        rule = WorkspaceRule(
            workspace="special:scratch",
            fields={"gapsout": 40, "on_created_empty": "kitty"},
        )
        assert render_workspace_rule(rule) == (
            'hl.workspace_rule({ workspace = "special:scratch", '
            'gapsout = 40, on_created_empty = "kitty" })'
        )


class TestRenderModules:
    def test_empty_lists_render_nothing(self) -> None:
        assert render_monitors_module([], app_version=VERSION) is None
        assert render_workspace_rules_module([], app_version=VERSION) is None

    def test_golden_monitors(self) -> None:
        rules = [
            MonitorRule(
                output="desc:BOE 0x0791",
                fields={"mode": "1920x1080@60", "position": "0x0", "scale": 1.5},
            ),
            MonitorRule(
                output="DP-3",
                fields={
                    "mode": "2560x1440@144",
                    "position": "1280x0",
                    "scale": 1,
                    "transform": 1,
                    "vrr": 1,
                    "bitdepth": 10,
                },
            ),
            MonitorRule(output="HDMI-A-1", fields={"disabled": True}),
            EVERY_ROW_FIELD,
            MonitorRule(output="", fields={"mode": "preferred", "position": "auto"}),
        ]
        text = render_monitors_module(rules, app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "monitors.lua", "monitors.lua")

    def test_golden_workspace_rules_enforce_one_per_selector(self) -> None:
        """The acceptance gate for ADR-0008's merge: duplicates in, one rule out."""
        entities = EntitySet()
        entities.add_workspace_rule(
            WorkspaceRule(workspace="1", fields={"monitor": "DP-1", "default": True})
        )
        entities.add_workspace_rule(
            WorkspaceRule(workspace="special:scratch", fields={"gapsout": 40})
        )
        # The same selector again: Hyprland's replaceOrAdd merges field-wise, so the
        # golden must hold two rules, the first with both its fields.
        entities.add_workspace_rule(WorkspaceRule(workspace="1", fields={"persistent": True}))
        text = render_workspace_rules_module(entities.workspace_rules, app_version=VERSION)
        assert text is not None
        assert text.count("hl.workspace_rule(") == 2
        assert_matches_golden(text, GOLDEN / "workspace_rules.lua", "workspace_rules.lua")


@pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter")
class TestRoundTrip:
    def test_monitor_rules_round_trip(self) -> None:
        rules = [
            MonitorRule(
                output="desc:BOE 0x0791",
                fields={"mode": "1920x1080@60", "position": "0x0", "scale": 1.5},
            ),
            MonitorRule(output="", fields={"mode": "preferred"}),
        ]
        text = render_monitors_module(rules, app_version=VERSION)
        assert text is not None
        parsed = parse_monitors_module(text)
        assert parsed.ok
        assert [rule.output for rule in parsed.monitors] == ["desc:BOE 0x0791", ""]
        assert parsed.monitors[0].fields == {
            "mode": "1920x1080@60",
            "position": "0x0",
            "scale": 1.5,
        }

    def test_every_row_field_round_trips_without_loss(self) -> None:
        text = render_monitors_module([EVERY_ROW_FIELD], app_version=VERSION)
        assert text is not None

        parsed = parse_monitors_module(text)

        assert parsed.ok
        assert parsed.monitors[0].fields == {
            "mode": "modeline 148.5 1920 2008 2052 2200 1080 1084 1089 1125 +hsync +vsync",
            "scale": 1.6,
            "reserved": {"top": 32, "right": 0, "bottom": 0, "left": 8},
            "cm": "hdredid",
            "sdr_eotf": "gamma22force",
            "sdrbrightness": 1.4,
            "sdrsaturation": 0.9,
            "supports_wide_color": 1,
            "supports_hdr": 0,
            "sdr_min_luminance": 0.005,
            "sdr_max_luminance": 250,
            "min_luminance": 0.05,
            "max_luminance": 1000,
            "max_avg_luminance": 400,
        }

    def test_a_uniform_reserved_area_round_trips_as_one_number(self) -> None:
        text = render_monitors_module(
            [MonitorRule(output="DP-2", fields={"reserved": 32})], app_version=VERSION
        )
        assert text is not None

        assert parse_monitors_module(text).monitors[0].fields == {"reserved": 32}

    def test_hand_edited_duplicate_output_merges(self) -> None:
        """A hand edit declaring an output twice comes back as the one merged rule."""
        text = (
            'hl.monitor({ output = "DP-1", mode = "1920x1080@60", scale = 2 })\n'
            'hl.monitor({ output = "DP-1", position = "0x0" })\n'
        )
        parsed = parse_monitors_module(text)
        assert parsed.ok
        assert len(parsed.monitors) == 1
        assert parsed.monitors[0].fields == {
            "mode": "1920x1080@60",
            "scale": 2,
            "position": "0x0",
        }

    def test_hand_edited_duplicate_selector_merges(self) -> None:
        """Selector uniqueness holds through the read-back, not only the render."""
        text = (
            'hl.workspace_rule({ workspace = "1", monitor = "DP-1" })\n'
            'hl.workspace_rule({ workspace = "1", persistent = true })\n'
        )
        parsed = parse_monitors_module(text)
        assert parsed.ok
        assert len(parsed.workspace_rules) == 1
        assert parsed.workspace_rules[0].fields == {"monitor": "DP-1", "persistent": True}

    def test_misfiled_rules_still_come_back(self) -> None:
        """A workspace rule hand-added to `monitors.lua` lands in the workspace list."""
        text = (
            'hl.monitor({ output = "DP-1", mode = "preferred" })\n'
            'hl.workspace_rule({ workspace = "special:x", gapsout = 0 })\n'
        )
        parsed = parse_monitors_module(text)
        assert parsed.ok
        assert len(parsed.monitors) == 1
        assert len(parsed.workspace_rules) == 1
