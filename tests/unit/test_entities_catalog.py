"""The declarative Entity catalogue: what Hyprland would refuse, decided offline (#70).

The two findings with teeth are the curve ones. `hl.animation{bezier = "easy"}` is refused
when no curve called `easy` exists, and a refused call takes the whole Module down -- so
"you deleted a curve three animations were using" has to be answerable from the model,
before the write, rather than from `configerrors` afterwards.
"""

from __future__ import annotations

from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.entities_catalog import (
    ANIMATION_LEAVES,
    BUILTIN_CURVES,
    DEVICE_FIELD_SPECS,
    DEVICE_ONLY_FIELDS,
    FieldSpec,
    FieldType,
    animation_findings,
    coerce,
    curve_findings,
    curve_usage,
    dangling_curve_references,
    device_override_options,
    field_text,
    is_scripted,
    missing_curve_references,
    overridden_options,
    unknown_device_fields,
    unknown_leaves,
)
from hyprtweaker.engine.model.entities import Animation, Curve, Device, EntitySet, Gesture
from hyprtweaker.engine.schema import load_schema

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
OPTION_NAMES = tuple(option.name for option in SCHEMA)


def _entities(*, curves: list[Curve] | None = None, animations: list[Animation] | None = None):
    entities = EntitySet()
    entities.curves.extend(curves or [])
    entities.animations.extend(animations or [])
    return entities


# --- dangling curve references ------------------------------------------------------------


class TestDanglingCurves:
    def test_an_animation_naming_a_declared_curve_is_fine(self) -> None:
        entities = _entities(
            curves=[Curve("easy", {"type": "bezier", "points": [[0, 0], [1, 1]]})],
            animations=[Animation("windowsIn", {"enabled": True, "bezier": "easy"})],
        )

        assert dangling_curve_references(entities) == ()

    def test_an_animation_naming_nothing_that_exists_is_surfaced(self) -> None:
        entities = _entities(
            animations=[Animation("windowsIn", {"enabled": True, "bezier": "gone"})]
        )

        findings = dangling_curve_references(entities)

        assert [f.subject for f in findings] == ["windowsIn"]
        assert "gone" in findings[0].message

    def test_a_spring_reference_is_checked_the_same_way(self) -> None:
        entities = _entities(
            animations=[Animation("windowsOut", {"enabled": True, "spring": "bouncy"})]
        )

        assert [f.subject for f in dangling_curve_references(entities)] == ["windowsOut"]

    def test_hyprlands_own_curves_are_not_dangling(self) -> None:
        """The upstream example names `default` before declaring any curve of that name."""
        for builtin in BUILTIN_CURVES:
            entities = _entities(
                animations=[Animation("fade", {"enabled": True, "bezier": builtin})]
            )
            assert dangling_curve_references(entities) == (), builtin

    def test_a_disabled_animation_needs_no_curve_at_all(self) -> None:
        """The parser short-circuits before it looks for one (CR:412-415)."""
        entities = _entities(
            animations=[Animation("border", {"enabled": False, "bezier": "gone"})]
        )

        assert dangling_curve_references(entities) == ()
        assert missing_curve_references(entities) == ()

    def test_an_enabled_animation_with_no_curve_is_surfaced_separately(self) -> None:
        """ "bezier or spring is required" -- a different fix from a dangling name."""
        entities = _entities(animations=[Animation("windowsIn", {"enabled": True, "speed": 3})])

        findings = missing_curve_references(entities)

        assert [f.subject for f in findings] == ["windowsIn"]

    def test_deleting_a_curve_names_the_animations_that_would_break(self) -> None:
        entities = _entities(
            curves=[Curve("easy", {"type": "bezier", "points": [[0, 0], [1, 1]]})],
            animations=[
                Animation("windowsIn", {"enabled": True, "bezier": "easy"}),
                Animation("windowsOut", {"enabled": True, "bezier": "easy"}),
                Animation("fade", {"enabled": True, "bezier": "default"}),
            ],
        )

        usage = curve_usage(entities, "easy")

        assert usage.used
        assert usage.leaves == ("windowsIn", "windowsOut")

    def test_an_unused_curve_reports_no_users(self) -> None:
        entities = _entities(curves=[Curve("spare", {"type": "bezier"})])

        assert not curve_usage(entities, "spare").used


# --- other findings -----------------------------------------------------------------------


class TestFindings:
    def test_a_bezier_needs_exactly_two_points(self) -> None:
        assert curve_findings(Curve("c", {"type": "bezier", "points": [[0, 0]]}))

    def test_a_control_point_outside_the_clamp_range_is_surfaced(self) -> None:
        """Legacy hyprlang accepted any float; the Lua parser clamps to -1..2 (CR:323)."""
        findings = curve_findings(Curve("c", {"type": "bezier", "points": [[0, 0], [1, 5]]}))

        assert findings
        assert "-1.0..2.0" in findings[0].message

    def test_a_valid_bezier_has_nothing_to_say(self) -> None:
        assert (
            curve_findings(Curve("c", {"type": "bezier", "points": [[0.2, 1], [0.3, 1]]})) == ()
        )

    def test_a_spring_needs_all_three_numbers_above_the_floor(self) -> None:
        assert curve_findings(Curve("s", {"type": "spring", "mass": 1, "stiffness": 200}))
        assert curve_findings(
            Curve("s", {"type": "spring", "mass": 0.1, "stiffness": 200, "dampening": 10})
        )
        assert (
            curve_findings(
                Curve("s", {"type": "spring", "mass": 1, "stiffness": 200, "dampening": 10})
            )
            == ()
        )

    def test_a_speed_outside_the_range_is_surfaced(self) -> None:
        assert animation_findings(Animation("fade", {"speed": 500}))
        assert animation_findings(Animation("fade", {"speed": 0}))
        assert animation_findings(Animation("fade", {"speed": 4.1})) == ()

    def test_naming_both_a_bezier_and_a_spring_is_surfaced(self) -> None:
        findings = animation_findings(Animation("fade", {"bezier": "a", "spring": "b"}))

        assert findings
        assert "not both" in findings[0].message

    def test_a_leaf_this_version_does_not_have_is_shown_rather_than_rejected(self) -> None:
        """The ADR-0012 degradation rule: show more, not less."""
        entities = _entities(animations=[Animation("brandNewLeaf", {"enabled": False})])

        assert unknown_leaves(entities) == ("brandNewLeaf",)
        assert animation_findings(Animation("brandNewLeaf", {}))

    def test_every_shipped_leaf_passes_its_own_check(self) -> None:
        for leaf in ANIMATION_LEAVES:
            assert animation_findings(Animation(leaf, {"enabled": False})) == (), leaf

    def test_a_device_key_hyprland_has_no_field_for_is_surfaced(self) -> None:
        """`hl.device` raises "unknown field" and takes the Module down with it."""
        device = Device("mouse", {"sensitivity": 0.2, "eraser_button_mode": 1})

        assert unknown_device_fields(device) == ("eraser_button_mode",)

    def test_a_device_of_known_fields_has_nothing_to_say(self) -> None:
        assert unknown_device_fields(Device("mouse", {"sensitivity": 0.2})) == ()


# --- device overrides ---------------------------------------------------------------------


class TestDeviceOverrides:
    def test_every_device_field_but_the_device_only_ones_shadows_an_option(self) -> None:
        """Derived from the Schema, so a Hyprland rename shows up here rather than rotting."""
        mapping = device_override_options(OPTION_NAMES)
        unmapped = {name for name in DEVICE_FIELD_SPECS if name not in mapping}

        assert unmapped <= DEVICE_ONLY_FIELDS

    def test_a_field_that_exists_in_two_categories_names_both_options(self) -> None:
        """Which one applies depends on the device's class, which the app cannot know."""
        mapping = device_override_options(OPTION_NAMES)

        assert set(mapping["natural_scroll"]) == {
            "input:natural_scroll",
            "input:touchpad:natural_scroll",
        }

    def test_the_two_dashed_options_are_found_under_their_underscore_field_names(self) -> None:
        mapping = device_override_options(OPTION_NAMES)

        assert mapping["tap_to_click"] == ("input:touchpad:tap-to-click",)
        assert mapping["tap_and_drag"] == ("input:touchpad:tap-and-drag",)

    def test_an_override_names_the_options_it_shadows_and_the_device_doing_it(self) -> None:
        devices = [Device("epic-mouse-v1", {"sensitivity": -0.5})]

        hits = overridden_options(devices, OPTION_NAMES)

        assert hits["input:sensitivity"] == ("epic-mouse-v1",)

    def test_two_devices_overriding_one_option_are_both_named(self) -> None:
        devices = [Device("mouse-a", {"sensitivity": 1}), Device("mouse-b", {"sensitivity": 0})]

        hits = overridden_options(devices, OPTION_NAMES)

        assert hits["input:sensitivity"] == ("mouse-a", "mouse-b")

    def test_a_device_only_field_badges_nothing(self) -> None:
        """`tags` and `keybinds` have no global counterpart, so no Row can carry them."""
        hits = overridden_options([Device("kb", {"tags": "a,b"})], OPTION_NAMES)

        assert hits == {}

    def test_no_devices_means_no_badges(self) -> None:
        assert overridden_options([], OPTION_NAMES) == {}


# --- scripted gestures ---------------------------------------------------------------------


def test_a_string_action_is_config_and_a_function_action_is_script() -> None:
    assert not is_scripted(Gesture({"action": "workspace"}))
    assert is_scripted(Gesture({"action": {"__fn": 7}}))
    assert not is_scripted(Gesture({"fingers": 3}))


# --- form values ----------------------------------------------------------------------------


class TestCoercion:
    def test_empty_text_means_leave_the_key_out(self) -> None:
        """An entity table with `speed = nil` and one without it are the same table."""
        assert coerce(FieldSpec("speed", FieldType.FLOAT), "  ") is None

    def test_numbers_come_back_typed(self) -> None:
        assert coerce(FieldSpec("fingers", FieldType.INT), "3") == 3
        assert coerce(FieldSpec("speed", FieldType.FLOAT), "4.1") == 4.1

    def test_a_vec2_takes_either_separator(self) -> None:
        spec = FieldSpec("region_position", FieldType.VEC2)

        assert coerce(spec, "10 20") == [10.0, 20.0]
        assert coerce(spec, "10, 20") == [10.0, 20.0]

    def test_text_that_is_not_a_number_survives_rather_than_being_dropped(self) -> None:
        """Dropping it would lose what the user typed; the finding says it is wrong."""
        assert coerce(FieldSpec("speed", FieldType.FLOAT), "fast") == "fast"

    def test_values_render_back_as_editable_text(self) -> None:
        assert field_text(True) == "true"
        assert field_text(4.10) == "4.1"
        assert field_text([10, 20]) == "10, 20"
        assert field_text(None) == ""
