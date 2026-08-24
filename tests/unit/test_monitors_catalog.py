"""The headless half of the Monitors editor: geometry, identity, matching (#68)."""

from __future__ import annotations

from hyprtweaker.engine.model.entities import MonitorRule
from hyprtweaker.engine.monitors_catalog import (
    connected_rules,
    disconnected_rules,
    format_mode,
    format_position,
    logical_size,
    parse_mode,
    parse_position,
    preferred_identity,
    rule_for,
    rule_matches_output,
    snap_position,
)


class TestLogicalSize:
    def test_scale_divides(self) -> None:
        assert logical_size(1920, 1080, scale=1.5) == (1280, 720)

    def test_rotation_swaps_after_scaling(self) -> None:
        assert logical_size(1920, 1080, scale=1.5, transform=1) == (720, 1280)
        assert logical_size(1920, 1080, scale=1.5, transform=3) == (720, 1280)
        assert logical_size(1920, 1080, scale=1.5, transform=5) == (720, 1280)

    def test_even_transforms_do_not_swap(self) -> None:
        assert logical_size(1920, 1080, transform=2) == (1920, 1080)

    def test_auto_scale_reads_as_one(self) -> None:
        assert logical_size(1920, 1080, scale="auto") == (1920, 1080)

    def test_fractional_scale_rounds(self) -> None:
        # 2256 / 1.566667 = 1440.0004 -> 1440, the value Hyprland itself lands on.
        assert logical_size(2256, 1504, scale=1.566667) == (1440, 960)


class TestModes:
    def test_available_modes_spelling(self) -> None:
        assert parse_mode("1920x1080@60.01Hz") == (1920, 1080, 60.01)

    def test_rule_spelling_without_refresh(self) -> None:
        assert parse_mode("2560x1440") == (2560, 1440, None)

    def test_words_are_not_sizes(self) -> None:
        assert parse_mode("preferred") is None
        assert parse_mode("auto") is None
        assert parse_mode("") is None

    def test_format_trims_the_refresh(self) -> None:
        assert format_mode(1920, 1080, 60.0) == "1920x1080@60"
        assert format_mode(1920, 1080, 59.94) == "1920x1080@59.94"
        assert format_mode(1920, 1080) == "1920x1080"


class TestPositions:
    def test_round_trip(self) -> None:
        assert parse_position("1280x0") == (1280, 0)
        assert parse_position("-1920x-32") == (-1920, -32)
        assert format_position(1280, 0) == "1280x0"

    def test_auto_is_not_a_position(self) -> None:
        assert parse_position("auto") is None
        assert parse_position("auto-right") is None


class TestSnapping:
    def test_snaps_to_abutment(self) -> None:
        # A 1280-wide neighbour at 0x0; dropping at 1270 snaps flush to its right edge.
        assert snap_position(1270, 4, 2560, 1440, [(0, 0, 1280, 720)]) == (1280, 0)

    def test_beyond_threshold_stays_put(self) -> None:
        assert snap_position(1400, 300, 2560, 1440, [(0, 0, 1280, 720)]) == (1400, 300)

    def test_own_right_edge_snaps_too(self) -> None:
        # Dragging left of a neighbour: the moving rect's right edge meets its left.
        assert snap_position(-2550, 10, 2560, 1440, [(0, 0, 1280, 720)]) == (-2560, 0)

    def test_no_neighbours_no_snap(self) -> None:
        assert snap_position(37, 41, 1920, 1080, []) == (37, 41)


class TestIdentity:
    def test_unique_description_wins(self) -> None:
        assert (
            preferred_identity("DP-1", "BOE 0x0791", taken_descriptions=("Dell U2720Q",))
            == "desc:BOE 0x0791"
        )

    def test_duplicate_description_falls_back_to_connector(self) -> None:
        assert (
            preferred_identity("DP-2", "Dell U2720Q", taken_descriptions=("Dell U2720Q",))
            == "DP-2"
        )

    def test_empty_description_falls_back_to_connector(self) -> None:
        assert preferred_identity("eDP-1", "  ") == "eDP-1"


class TestMatching:
    def test_connector_matches_exactly(self) -> None:
        assert rule_matches_output("DP-1", connector="DP-1", description="X")
        assert not rule_matches_output("DP-1", connector="DP-10", description="X")

    def test_desc_matches_by_prefix(self) -> None:
        assert rule_matches_output(
            "desc:BOE", connector="eDP-1", description="BOE 0x0791"
        )
        assert not rule_matches_output(
            "desc:Dell", connector="eDP-1", description="BOE 0x0791"
        )

    def test_catch_all_matches_nothing(self) -> None:
        assert not rule_matches_output("", connector="DP-1", description="X")

    def test_empty_desc_matches_nothing(self) -> None:
        assert not rule_matches_output("desc:", connector="DP-1", description="X")


MONITORS = (
    {"name": "eDP-1", "description": "BOE 0x0791"},
    {"name": "DP-3", "description": "Dell U2720Q"},
)


class TestRuleAssignment:
    def test_connected_rules_key_by_connector(self) -> None:
        rules = [
            MonitorRule(output="desc:BOE 0x0791", fields={"scale": 1.5}),
            MonitorRule(output="", fields={"mode": "preferred"}),
        ]
        assigned = connected_rules(rules, MONITORS)
        assert assigned["eDP-1"] is rules[0]
        assert assigned["DP-3"] is None  # the catch-all is a fallback, not an identity

    def test_rule_for_none_is_the_hotplug_hint(self) -> None:
        assert rule_for([], connector="DP-3", description="Dell U2720Q") is None

    def test_disconnected_excludes_matched_and_catch_all(self) -> None:
        rules = [
            MonitorRule(output="desc:BOE 0x0791", fields={}),
            MonitorRule(output="DP-9", fields={"disabled": True}),  # the dock at work
            MonitorRule(output="", fields={"mode": "preferred"}),
        ]
        leftover = disconnected_rules(rules, MONITORS)
        assert [rule.output for rule in leftover] == ["DP-9"]
