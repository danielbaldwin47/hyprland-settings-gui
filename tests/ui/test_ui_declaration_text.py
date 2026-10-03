"""What a declarative Entity's row says, decided without a display (#70).

The same seam `test_ui_rules_text` keeps for rules: a row's title, its summary line, and
whether a form is complete enough to save are answers about strings, so they are asserted
here rather than through a widget tree. What the UI tier then has to check is only that GTK
hangs the right widgets off these answers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("gi")

from hyprtweaker.engine.entities_catalog import FieldSpec, FieldType
from hyprtweaker.engine.model.entities import (
    Animation,
    Curve,
    Device,
    EnvVar,
    Gesture,
    Permission,
    StartupCommand,
)
from hyprtweaker.ui.pages.declaration_kinds import (
    BY_KIND,
    BY_SECTION,
    KINDS,
    choice_label,
    filter_haystack,
    missing_required,
    read_only,
    row_subtitle,
    row_title,
)


class TestTheCatalogueItself:
    def test_every_kind_names_an_entity_set_list(self) -> None:
        """The names are `EntitySet`'s own, so the two cannot drift into disagreeing."""
        from hyprtweaker.engine.model.entities import EntitySet

        entities = EntitySet()
        for descriptor in KINDS:
            assert hasattr(entities, descriptor.kind), descriptor.kind

    def test_sections_and_kinds_are_both_unique(self) -> None:
        assert len(BY_KIND) == len(KINDS)
        assert len(BY_SECTION) == len(KINDS)

    def test_every_kind_has_copy_for_an_empty_page(self) -> None:
        for descriptor in KINDS:
            assert descriptor.title and descriptor.empty_hint and descriptor.description

    def test_the_two_kinds_hyprland_does_not_reset_say_so(self) -> None:
        """Removing a variable or a permission needs a restart; a Page that stayed silent
        would let a user believe a delete had taken effect when it had not."""
        assert "restart" in BY_KIND["env"].note
        assert "restart" in BY_KIND["permissions"].note


class TestRowText:
    def test_a_curve_reads_as_its_shape(self) -> None:
        curve = Curve("easy", {"type": "bezier", "points": [[0.23, 1], [0.32, 1]]})

        assert row_title("curves", curve) == "easy"
        assert row_subtitle("curves", curve) == "Bezier · 0.23, 1, 0.32, 1"

    def test_a_spring_says_which_numbers_it_holds(self) -> None:
        curve = Curve(
            "bouncy", {"type": "spring", "mass": 1, "stiffness": 200, "dampening": 10}
        )

        assert row_subtitle("curves", curve).startswith("Spring · mass 1")

    def test_an_animation_leads_with_its_curve_and_speed(self) -> None:
        animation = Animation("windowsIn", {"enabled": True, "speed": 4.1, "bezier": "easy"})

        assert row_title("animations", animation) == "windowsIn"
        assert row_subtitle("animations", animation) == "easy · speed 4.1"

    def test_a_disabled_animation_says_off_rather_than_listing_dead_values(self) -> None:
        assert row_subtitle("animations", Animation("border", {"enabled": False})) == "Off"

    def test_a_gesture_reads_as_its_trigger_then_its_effect(self) -> None:
        gesture = Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"})

        assert row_title("gestures", gesture) == "3 fingers · horizontal"
        assert row_subtitle("gestures", gesture) == "workspace"

    def test_a_scripted_gesture_says_where_its_action_came_from(self) -> None:
        gesture = Gesture({"fingers": 4, "direction": "up", "action": {"__fn": 2}})

        assert read_only("gestures", gesture)
        assert "Lua" in row_subtitle("gestures", gesture)

    def test_a_string_action_gesture_is_editable(self) -> None:
        assert not read_only("gestures", Gesture({"action": "close"}))

    def test_a_device_lists_what_it_overrides(self) -> None:
        device = Device("epic-mouse-v1", {"sensitivity": -0.5, "natural_scroll": True})

        assert row_title("devices", device) == "epic-mouse-v1"
        assert row_subtitle("devices", device) == "natural_scroll true, sensitivity -0.5"

    def test_a_device_with_nothing_set_says_so_rather_than_showing_blank(self) -> None:
        assert row_subtitle("devices", Device("kb", {})) == "No settings yet"

    def test_a_variable_shows_its_value_and_flags_the_dbus_export(self) -> None:
        assert row_subtitle("env", EnvVar("XCURSOR_SIZE", "24")) == "24"
        assert "D-Bus" in row_subtitle("env", EnvVar("X", "y", dbus=True))

    def test_a_permission_reads_as_a_decision(self) -> None:
        permission = Permission("/usr/bin/grim", "screencopy", "allow")

        assert row_title("permissions", permission) == "/usr/bin/grim"
        assert row_subtitle("permissions", permission) == "allow screencopy"

    def test_an_autostart_command_says_when_it_runs(self) -> None:
        assert row_subtitle("startup", StartupCommand("waybar")) == "Once, when Hyprland starts"
        assert (
            row_subtitle("startup", StartupCommand("waybar", event=""))
            == "Every time the config reloads"
        )
        assert "shuts down" in row_subtitle(
            "startup", StartupCommand("save", event="hyprland.shutdown")
        )

    def test_a_raw_command_says_that_too(self) -> None:
        assert "without rule parsing" in row_subtitle(
            "startup", StartupCommand("[a] b", raw=True)
        )

    def test_the_filter_matches_both_lines_of_a_row(self) -> None:
        gesture = Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"})

        haystack = filter_haystack("gestures", gesture)

        assert "horizontal" in haystack
        assert "workspace" in haystack
        assert haystack == haystack.lower()


class TestFormCompleteness:
    def test_a_form_missing_a_required_field_names_it(self) -> None:
        missing = missing_required("env", {"name": "XCURSOR_SIZE"})

        assert missing == ("Value",)

    def test_a_complete_form_is_complete(self) -> None:
        assert missing_required("env", {"name": "X", "value": "1"}) == ()

    def test_whitespace_is_not_a_value(self) -> None:
        assert "Value" in missing_required("env", {"name": "X", "value": "  "})

    def test_a_bezier_asks_for_its_four_numbers(self) -> None:
        missing = missing_required("curves", {"name": "easy", "type": "bezier"})

        assert missing == ("First point X", "First point Y", "Second point X", "Second point Y")

    def test_a_spring_asks_for_its_three_instead(self) -> None:
        """The point fields are irrelevant to a spring, and vice versa."""
        missing = missing_required("curves", {"name": "s", "type": "spring"})

        assert missing == ("Mass", "Stiffness", "Dampening")

    def test_a_complete_bezier_needs_nothing_more(self) -> None:
        values = {"name": "easy", "type": "bezier", "x0": 0.2, "y0": 1, "x1": 0.3, "y1": 1}

        assert missing_required("curves", values) == ()

    def test_zero_counts_as_a_value(self) -> None:
        """The obvious falsiness bug: a control point at the origin is a real point."""
        values = {"name": "linearish", "type": "bezier", "x0": 0, "y0": 0, "x1": 1, "y1": 1}

        assert missing_required("curves", values) == ()

    def test_a_gesture_needs_its_three_trigger_fields(self) -> None:
        assert set(missing_required("gestures", {})) == {"Fingers", "Direction", "Action"}


class TestFormToEntityAndBack:
    def test_every_kind_survives_a_form_round_trip(self) -> None:
        """The editor opens on `to_form` and saves through `from_form`; opening and saving
        an untouched row must give back the row."""
        samples = {
            "curves": Curve("easy", {"type": "bezier", "points": [[0.2, 1.0], [0.3, 1.0]]}),
            "animations": Animation("windowsIn", {"enabled": True, "bezier": "easy"}),
            "gestures": Gesture({"fingers": 3, "direction": "up", "action": "close"}),
            "devices": Device("mouse", {"sensitivity": -0.5}),
            "env": EnvVar("XCURSOR_SIZE", "24"),
            "permissions": Permission("/usr/bin/grim", "screencopy", "allow"),
            "startup": StartupCommand("waybar"),
        }
        for kind, entity in samples.items():
            descriptor = BY_KIND[kind]
            rebuilt = descriptor.from_form(descriptor.to_form(entity), entity)
            assert rebuilt == entity, kind

    def test_picking_a_spring_drops_the_animations_bezier(self) -> None:
        """The parser refuses a table carrying both (`animation_findings`)."""
        descriptor = BY_KIND["animations"]

        built = descriptor.from_form(
            {"leaf": "fade", "enabled": True, "bezier": "a", "spring": "b"}, None
        )

        assert "spring" not in built.fields
        assert built.fields["bezier"] == "a"


class TestChoiceLabels:
    def test_an_ordinary_enum_shows_the_word_the_wiki_uses(self) -> None:
        spec = FieldSpec("kind", FieldType.ENUM, choices=("screencopy",))

        assert choice_label(spec, "screencopy") == "screencopy"

    def test_the_autostart_event_is_the_one_value_with_no_reading(self) -> None:
        """`""` means "no event at all"; a blank dropdown row says nothing."""
        spec = FieldSpec("event", FieldType.ENUM)

        assert choice_label(spec, "") == "Every time the config reloads"
        assert choice_label(spec, "hyprland.start") == "Once, when Hyprland starts"
