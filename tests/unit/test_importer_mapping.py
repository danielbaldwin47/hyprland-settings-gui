"""Keyword mapping: every hyprlang keyword lands in the model, or in the Loss report.

The cases here are the ones where the two engines *disagree* rather than merely differ in
spelling -- an argument split at the wrong comma, a toggle whose empty form flipped
meaning, a workspace rule whose polarity inverted. Those are the failures that produce a
config which loads cleanly and behaves wrongly, which no amount of syntax checking finds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.importer import (
    LossClass,
    LossCode,
    LossReport,
    import_config,
    map_bind,
    map_keywords,
    map_layer_rule,
    map_monitor,
    map_unbind,
    map_window_rule,
    map_workspace_rule,
    parse,
    translate_dispatcher,
)
from hyprtweaker.engine.importer.binds import _KEY_RENAMES
from hyprtweaker.engine.importer.dispatchers import MAX_SCRIPT_BYTES
from hyprtweaker.engine.importer.keysyms import known_keysym, validator_available
from hyprtweaker.engine.schema import load_schema

SCHEMA_VERSION = "0.56.2"


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema(SCHEMA_VERSION)


@pytest.fixture
def report() -> LossReport:
    return LossReport()


def _map(text: str, schema, tmp_path):  # type: ignore[no-untyped-def]
    """Parse a snippet and map it, as the wizard would a whole tree."""
    entry = tmp_path / "hyprland.conf"
    entry.write_text(text, encoding="utf-8")
    return map_keywords(parse(entry, env={}), schema, source=entry)


def _codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(item.code) for item in result.loss}


# --- dispatchers ------------------------------------------------------------------------


class TestDispatcherGrammars:
    """Per-dispatcher argument grammars, which are not guessable from the name."""

    def test_window_selector_is_passed_through_unchanged(self, report: LossReport) -> None:
        call = translate_dispatcher(
            "closewindow", "class:^(kitty)$", origin="x:1", report=report
        )
        assert call is not None
        assert call.path == "window.close"
        assert call.args == {"window": "class:^(kitty)$"}

    def test_active_and_empty_selectors_mean_the_focused_window(
        self, report: LossReport
    ) -> None:
        for args in ("", "active"):
            call = translate_dispatcher("pseudo", args, origin="x:1", report=report)
            assert call is not None and call.args == {}

    def test_movetoworkspace_splits_at_the_last_comma(self, report: LossReport) -> None:
        # The window selector is the *last* field, so the workspace may contain commas --
        # splitting at the first one would move the window to a workspace called "name:my".
        call = translate_dispatcher(
            "movetoworkspace", "name:my,ws,class:^(kitty)$", origin="x:1", report=report
        )
        assert call is not None
        assert call.args["workspace"] == "name:my,ws"
        assert call.args["window"] == "class:^(kitty)$"

    def test_movetoworkspace_without_a_selector_keeps_the_whole_string(
        self, report: LossReport
    ) -> None:
        call = translate_dispatcher("movetoworkspace", "3", origin="x:1", report=report)
        assert call is not None
        assert call.args == {"workspace": "3"}

    def test_signalwindow_splits_at_the_first_comma(self, report: LossReport) -> None:
        call = translate_dispatcher(
            "signalwindow", "class:^(a)$,9", origin="x:1", report=report
        )
        assert call is not None
        assert call.args == {"signal": 9, "window": "class:^(a)$"}

    def test_setprop_takes_the_window_first_then_prop_and_value(
        self, report: LossReport
    ) -> None:
        call = translate_dispatcher(
            "setprop", "class:^(kitty)$ alpha 0.5", origin="x:1", report=report
        )
        assert call is not None
        assert call.args == {"prop": "alpha", "value": "0.5", "window": "class:^(kitty)$"}

    def test_exact_resize_is_absolute_and_a_bare_pair_is_relative(
        self, report: LossReport
    ) -> None:
        absolute = translate_dispatcher(
            "resizeactive", "exact 800 600", origin="x", report=report
        )
        relative = translate_dispatcher("resizeactive", "10 -10", origin="x", report=report)
        assert absolute is not None and absolute.args["relative"] is False
        assert relative is not None and relative.args["relative"] is True

    def test_percentage_resize_is_dropped_rather_than_read_as_pixels(
        self, report: LossReport
    ) -> None:
        """`20%` emitted as `20` would resize by 20 pixels on every monitor -- a bind that
        silently does the wrong thing, which is worse than one that does nothing and says
        so. Same call `fullscreenstate -1` gets."""
        call = translate_dispatcher("resizeactive", "20% 0", origin="x:1", report=report)
        assert call is None
        assert LossCode.RESIZE_PERCENT in {item.code for item in report}
        assert report.of_class(LossClass.BREAKAGE)

    def test_an_unrecognised_action_word_takes_hyprlangs_else_branch(
        self, report: LossReport
    ) -> None:
        """hyprlang's group dispatchers read anything they did not recognise as *disable*;
        Lua reads anything it does not recognise as *toggle*. Passing the word through
        would silently invert it."""
        call = translate_dispatcher("lockgroups", "yes", origin="x:1", report=report)
        assert call is not None
        assert call.args == {"action": "off"}
        assert LossCode.TOGGLE_DEFAULT in {item.code for item in report}

    def test_lockgroups_repeated_as_its_own_argument_still_enables(
        self, report: LossReport
    ) -> None:
        call = translate_dispatcher("lockgroups", "lock", origin="x:1", report=report)
        assert call is not None and call.args == {"action": "on"}

    def test_denywindowfromgroup_tests_the_word_on_not_truthiness(
        self, report: LossReport
    ) -> None:
        """The translator compares against the literal string `on`, so `1` is *off*.
        Reading it as a boolean inverts the rule for anyone who wrote `1`."""
        on = translate_dispatcher("denywindowfromgroup", "on", origin="x", report=report)
        one = translate_dispatcher("denywindowfromgroup", "1", origin="x", report=report)
        assert on is not None and on.args == {"action": "on"}
        assert one is not None and one.args == {"action": "off"}

    def test_changegroupactive_index_zero_is_reported(self, report: LossReport) -> None:
        """0 and below meant "the last window" to hyprlang; Lua's index has no such form."""
        call = translate_dispatcher("changegroupactive", "0", origin="x:1", report=report)
        assert call is not None
        assert LossCode.UNSUPPORTED_KEYWORD in {item.code for item in report}

    def test_movewindow_distinguishes_a_direction_from_a_monitor(
        self, report: LossReport
    ) -> None:
        direction = translate_dispatcher("movewindow", "l", origin="x", report=report)
        monitor = translate_dispatcher(
            "movewindow", "mon:DP-1 silent", origin="x", report=report
        )
        assert direction is not None and direction.args == {"direction": "l"}
        assert monitor is not None
        assert monitor.args == {"monitor": "DP-1", "follow": False}

    def test_togglespecialworkspace_takes_a_bare_string_not_a_table(
        self, report: LossReport
    ) -> None:
        # `hl.dsp.workspace.toggle_special("magic")`, not `{...}` -- passing a table here
        # is a Lua error rather than a wrong result.
        call = translate_dispatcher(
            "togglespecialworkspace", "magic", origin="x", report=report
        )
        assert call is not None
        assert call.positional == ("magic",)
        assert call.args == {}

    def test_empty_dpms_means_off_not_toggle(self, report: LossReport) -> None:
        call = translate_dispatcher("dpms", "", origin="x:1", report=report)
        assert call is not None
        assert call.args == {"action": "off"}
        assert LossCode.TOGGLE_DEFAULT in {item.code for item in report}

    def test_lockgroups_unlock_becomes_off_because_lua_would_toggle(
        self, report: LossReport
    ) -> None:
        call = translate_dispatcher("lockgroups", "unlock", origin="x:1", report=report)
        assert call is not None
        assert call.args == {"action": "off"}

    def test_fullscreenstate_minus_one_is_breakage(self, report: LossReport) -> None:
        call = translate_dispatcher("fullscreenstate", "-1 2", origin="x:1", report=report)
        assert call is None
        assert report.of_class(LossClass.BREAKAGE)

    def test_dead_dispatchers_report_rather_than_raise(self, report: LossReport) -> None:
        for name in ("workspaceopt", "setignoregrouplock", "splitratio"):
            assert translate_dispatcher(name, "", origin="x:1", report=report) is None
        assert len(report) == 3

    def test_unknown_dispatcher_is_reported_not_raised(self, report: LossReport) -> None:
        assert translate_dispatcher("nonsuch", "arg", origin="x:1", report=report) is None
        assert LossCode.DEAD_DISPATCHER in {item.code for item in report}


# --- binds ------------------------------------------------------------------------------


class TestBinds:
    def test_mod_substring_spellings_all_canonicalise(self, report: LossReport) -> None:
        for spelling in ("SUPER_SHIFT", "SUPERSHIFT", "SUPER SHIFT", "super shift"):
            bind = map_bind("", f"{spelling}, Q, killactive", origin="x:1", report=report)
            assert bind is not None
            assert bind.keys == "SHIFT + SUPER + Q", spelling

    def test_empty_mods_leave_a_bare_key(self, report: LossReport) -> None:
        bind = map_bind("", ", Print, exec, grim", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "Print"

    def test_bare_keycode_above_nine_becomes_code_n(self, report: LossReport) -> None:
        bind = map_bind("", "SUPER, 42, killactive", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "SUPER + code:42"
        assert LossCode.BARE_KEYCODE in {item.code for item in report}

    def test_single_digit_keys_stay_keysyms(self, report: LossReport) -> None:
        # hyprlang only treated numbers *above 9* as keycodes; `1` is the digit key.
        bind = map_bind("", "SUPER, 1, workspace, 1", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "SUPER + 1"

    def test_special_syms_are_untouched(self, report: LossReport) -> None:
        for key in ("mouse:272", "mouse_down", "switch:on:Lid", "code:9"):
            bind = map_bind("", f"SUPER, {key}, killactive", origin="x:1", report=report)
            assert bind is not None
            assert bind.keys == f"SUPER + {key}"

    def test_enter_is_renamed_because_lua_rejects_it(self, report: LossReport) -> None:
        bind = map_bind("", "SUPER, Enter, exec, kitty", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "SUPER + Return"
        assert LossCode.UNKNOWN_KEYSYM in {item.code for item in report}

    def test_flag_letters_map_to_their_lua_names(self, report: LossReport) -> None:
        bind = map_bind("le", "SUPER, X, killactive", origin="x:1", report=report)
        assert bind is not None
        assert bind.options.locked is True
        assert bind.options.repeating is True

    def test_flag_order_does_not_matter(self, report: LossReport) -> None:
        one = map_bind("le", "SUPER, X, killactive", origin="x", report=report)
        two = map_bind("el", "SUPER, X, killactive", origin="x", report=report)
        assert one is not None and two is not None
        assert one.options == two.options

    def test_description_flag_consumes_the_third_field(self, report: LossReport) -> None:
        bind = map_bind(
            "d", "SUPER, Q, Close the window, killactive", origin="x:1", report=report
        )
        assert bind is not None
        assert bind.options.description == "Close the window"
        assert bind.dispatcher is not None
        assert bind.dispatcher.path == "window.close"

    def test_device_flag_consumes_a_field_and_reads_the_bang(self, report: LossReport) -> None:
        bind = map_bind(
            "k", "SUPER, Q, !dev-one dev-two, killactive", origin="x:1", report=report
        )
        assert bind is not None
        assert bind.options.device is not None
        assert bind.options.device.inclusive is False
        assert bind.options.device.names == ("dev-one", "dev-two")

    def test_description_and_device_flags_consume_two_fields_in_order(
        self, report: LossReport
    ) -> None:
        bind = map_bind(
            "dk", "SUPER, Q, Close it, dev-one, killactive", origin="x:1", report=report
        )
        assert bind is not None
        assert bind.options.description == "Close it"
        assert bind.options.device is not None
        assert bind.options.device.names == ("dev-one",)
        assert bind.dispatcher is not None and bind.dispatcher.path == "window.close"

    def test_mouse_bind_becomes_a_drag_dispatcher(self, report: LossReport) -> None:
        bind = map_bind("m", "SUPER, mouse:272, movewindow", origin="x:1", report=report)
        assert bind is not None
        assert bind.dispatcher is not None
        assert bind.dispatcher.path == "window.drag"
        assert LossCode.MOUSE_BIND in {item.code for item in report}

    def test_mouse_resize_carries_the_aspect_ratio_argument(self, report: LossReport) -> None:
        bind = map_bind("m", "SUPER, mouse:273, resizewindow 1", origin="x:1", report=report)
        assert bind is not None
        assert bind.dispatcher is not None
        assert bind.dispatcher.args == {"keep_aspect_ratio": True}

    def test_multikey_bind_joins_every_token(self, report: LossReport) -> None:
        bind = map_bind("s", "Control_L&Shift_L, K&J, killactive", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "Control_L + Shift_L + K + J"
        assert LossCode.MULTIKEY_BIND in {item.code for item in report}

    def test_a_bind_whose_dispatcher_dies_is_dropped_not_emitted_empty(
        self, report: LossReport
    ) -> None:
        assert map_bind("", "SUPER, S, splitratio, +0.1", origin="x:1", report=report) is None

    def test_an_unknown_key_name_is_reported_because_lua_refuses_it(
        self, report: LossReport
    ) -> None:
        """hyprlang resolved key names at press time and silently never matched a bad one;
        Lua resolves at bind time and refuses the whole config (ADR-0009, Needs review)."""
        bind = map_bind("", "SUPER, notakey, killactive", origin="x:1", report=report)
        assert bind is not None
        assert LossCode.UNKNOWN_KEYSYM in {item.code for item in report}

    def test_a_real_keysym_raises_nothing(self, report: LossReport) -> None:
        map_bind("", "SUPER, XF86AudioPlay, killactive", origin="x:1", report=report)
        assert LossCode.UNKNOWN_KEYSYM not in {item.code for item in report}

    @pytest.mark.parametrize(("wrong", "right"), sorted(_KEY_RENAMES.items()))
    def test_every_rename_goes_from_a_dead_name_to_a_live_one(
        self, wrong: str, right: str
    ) -> None:
        """Checks the rename table against xkb in both directions, so it cannot drift into
        a table of guesses: the key must be a name xkb rejects, the value one it accepts."""
        if not validator_available():
            pytest.skip("libxkbcommon is not loadable here")
        assert known_keysym(wrong) is False, f"{wrong!r} is a real keysym; do not rename it"
        assert known_keysym(right) is True, f"{right!r} is not a real keysym"

    def test_catchall_drops_its_modifiers(self, report: LossReport) -> None:
        """`catchall` swallows every key in its submap, so modifiers were already
        meaningless -- and carrying them into the key string would make Lua *require* them
        (ADR-0009 lists this under Needs review)."""
        bind = map_bind("", "SUPER, catchall, killactive", origin="x:1", report=report)
        assert bind is not None
        assert bind.keys == "catchall"
        assert LossCode.UNKNOWN_KEYSYM in {item.code for item in report}

    def test_unbind_canonicalises_the_key_string(self, report: LossReport) -> None:
        unbind = map_unbind("SUPER_SHIFT, Q", origin="x:1", report=report)
        assert unbind.keys == "SHIFT + SUPER + Q"
        assert LossCode.UNBIND_BY_STRING in {item.code for item in report}

    def test_unbind_all(self, report: LossReport) -> None:
        unbind = map_unbind("all", origin="x:1", report=report)
        assert unbind.all is True


class TestSubmaps:
    def test_binds_belong_to_the_submap_declared_above_them(self, schema, tmp_path) -> None:
        result = _map(
            """
            bind = SUPER, R, submap, resize
            submap = resize
            bind = , l, resizeactive, 10 0
            submap = reset
            bind = SUPER, Q, killactive
            """,
            schema,
            tmp_path,
        )
        by_key = {bind.keys: bind.submap for bind in result.entities.binds}
        assert by_key["SUPER + R"] is None
        assert by_key["l"] == "resize"
        assert by_key["SUPER + Q"] is None
        assert [s.name for s in result.entities.submaps] == ["resize"]

    def test_a_submap_reset_target_is_kept(self, schema, tmp_path) -> None:
        result = _map("submap = resize, reset\nbind = , l, killactive\n", schema, tmp_path)
        assert result.entities.submaps[0].reset_target == "reset"


# --- rules ------------------------------------------------------------------------------


class TestWindowRules:
    def test_match_props_and_effects_are_separated(self, report: LossReport) -> None:
        rule = map_window_rule("match:class kitty, border_size 10", origin="x:1", report=report)
        assert rule is not None
        assert rule.match == {"class": "kitty"}
        assert rule.effects == {"border_size": 10}

    def test_bool_effects_become_lua_booleans(self, report: LossReport) -> None:
        rule = map_window_rule("match:class a, float on", origin="x:1", report=report)
        assert rule is not None
        assert rule.effects == {"float": True}

    def test_rounding_above_twenty_is_breakage(self, report: LossReport) -> None:
        rule = map_window_rule("match:class a, rounding 30", origin="x:1", report=report)
        assert rule is not None
        assert report.of_class(LossClass.BREAKAGE)

    def test_pre_054_syntax_is_refused_rather_than_guessed(self, report: LossReport) -> None:
        assert map_window_rule("float, ^(kitty)$", origin="x:1", report=report) is None
        assert LossCode.OLD_WINDOWRULE_SYNTAX in {item.code for item in report}
        assert report.of_class(LossClass.BREAKAGE)

    def test_a_named_rule_is_updated_in_place_keeping_its_position(
        self, schema, tmp_path
    ) -> None:
        result = _map(
            """
            windowrule = name pin-it, match:class a, float on
            windowrule = match:class b, opaque on
            windowrule = name pin-it, match:class a, pin on
            """,
            schema,
            tmp_path,
        )
        rules = result.entities.window_rules
        assert len(rules) == 2
        assert rules[0].name == "pin-it"
        assert rules[0].effects == {"float": True, "pin": True}

    def test_named_rules_are_ordered_before_anonymous_ones(self, schema, tmp_path) -> None:
        result = _map(
            """
            windowrule = match:class anon, opaque on
            windowrule = name named, match:class n, float on
            """,
            schema,
            tmp_path,
        )
        ordered = result.entities.ordered_window_rules()
        assert [r.name for r in ordered] == ["named", ""]
        assert LossCode.RULE_PRECEDENCE in _codes(result)

    def test_block_form_reads_the_same_pairs(self, schema, tmp_path) -> None:
        result = _map(
            """
            windowrule {
              name = apply-something
              match:class = my-window
              border_size = 10
            }
            """,
            schema,
            tmp_path,
        )
        rule = result.entities.window_rules[0]
        assert rule.name == "apply-something"
        assert rule.match == {"class": "my-window"}
        assert rule.effects == {"border_size": 10}

    def test_windowrulev2_is_breakage(self, schema, tmp_path) -> None:
        result = _map("windowrulev2 = opacity 0.8, class:^(kitty)$\n", schema, tmp_path)
        assert LossCode.OLD_WINDOWRULE_SYNTAX in _codes(result)
        assert result.loss.of_class(LossClass.BREAKAGE)


class TestLayerRules:
    def test_ignorezero_becomes_ignore_alpha_zero(self, report: LossReport) -> None:
        rule = map_layer_rule(
            "ignorezero on, match:namespace waybar", origin="x:1", report=report
        )
        assert rule is not None
        assert rule.effects == {"ignore_alpha": 0}
        assert LossCode.LAYERRULE_DROPPED in {item.code for item in report}

    def test_ignore_alpha_stays_a_float(self, report: LossReport) -> None:
        rule = map_layer_rule(
            "ignore_alpha 0.5, match:namespace waybar", origin="x:1", report=report
        )
        assert rule is not None
        assert rule.effects == {"ignore_alpha": 0.5}

    def test_above_lock_out_of_range_is_breakage(self, report: LossReport) -> None:
        map_layer_rule("above_lock 5, match:namespace w", origin="x:1", report=report)
        assert report.of_class(LossClass.BREAKAGE)


class TestWorkspaceRules:
    def test_border_shadow_and_rounding_invert(self, report: LossReport) -> None:
        rule = map_workspace_rule(
            "2, border:false, shadow:false, rounding:true", origin="x:1", report=report
        )
        assert rule is not None
        assert rule.fields["no_border"] is True
        assert rule.fields["no_shadow"] is True
        assert rule.fields["no_rounding"] is False

    def test_layoutopt_collects_into_one_table(self, report: LossReport) -> None:
        rule = map_workspace_rule(
            "3, layoutopt:orientation:top, layoutopt:mfact:0.5", origin="x:1", report=report
        )
        assert rule is not None
        assert rule.fields["layout_opts"] == {"orientation": "top", "mfact": "0.5"}

    def test_css_gap_shorthand_expands_to_four_sides(self, report: LossReport) -> None:
        rule = map_workspace_rule("1, gapsout:10 20", origin="x:1", report=report)
        assert rule is not None
        assert rule.fields["gaps_out"] == {"top": 10, "right": 20, "bottom": 10, "left": 20}

    def test_duplicate_selectors_merge_as_hyprland_merges_them(self, schema, tmp_path) -> None:
        result = _map(
            "workspace = 1, monitor:DP-1\nworkspace = 1, default:true\n", schema, tmp_path
        )
        assert len(result.entities.workspace_rules) == 1
        assert result.entities.workspace_rules[0].fields == {
            "monitor": "DP-1",
            "default": True,
        }


# --- monitors ---------------------------------------------------------------------------


class TestMonitors:
    def test_full_line_reads_the_four_positional_fields(self, report: LossReport) -> None:
        mapped = map_monitor("DP-1, 2560x1440@144, 0x0, 1", origin="x:1", report=report)
        assert mapped is not None
        rule, merge = mapped
        assert merge is False
        assert rule.output == "DP-1"
        assert rule.fields["mode"] == "2560x1440@144"
        assert rule.fields["position"] == "0x0"
        assert rule.fields["scale"] == 1

    def test_scale_minus_one_becomes_auto(self, report: LossReport) -> None:
        mapped = map_monitor("DP-1, preferred, auto, -1", origin="x:1", report=report)
        assert mapped is not None
        assert mapped[0].fields["scale"] == "auto"

    def test_trailing_key_value_pairs_are_typed(self, report: LossReport) -> None:
        mapped = map_monitor(
            "DP-1, preferred, auto, 1, bitdepth, 10, vrr, 2, mirror, DP-2",
            origin="x:1",
            report=report,
        )
        assert mapped is not None
        assert mapped[0].fields["bitdepth"] == 10
        assert mapped[0].fields["vrr"] == 2
        assert mapped[0].fields["mirror"] == "DP-2"

    def test_addreserved_reorders_from_tblr_to_named_sides(self, report: LossReport) -> None:
        mapped = map_monitor("DP-1, addreserved, 1, 2, 3, 4", origin="x:1", report=report)
        assert mapped is not None
        rule, merge = mapped
        assert merge is True
        assert rule.fields["reserved"] == {"top": 1, "bottom": 2, "left": 3, "right": 4}

    def test_the_empty_output_catch_all_survives(self, report: LossReport) -> None:
        mapped = map_monitor(", preferred, auto, 1", origin="x:1", report=report)
        assert mapped is not None
        assert mapped[0].output == ""

    def test_transform_shorthand_merges_instead_of_replacing(self, schema, tmp_path) -> None:
        result = _map(
            "monitor = DP-1, 1920x1080, 0x0, 1\nmonitor = DP-1, transform, 3\n",
            schema,
            tmp_path,
        )
        assert len(result.entities.monitors) == 1
        fields = result.entities.monitors[0].fields
        assert fields["transform"] == 3
        assert fields["mode"] == "1920x1080"

    def test_a_second_full_line_replaces_rather_than_merges(self, schema, tmp_path) -> None:
        result = _map(
            "monitor = DP-1, 1920x1080, 0x0, 1, bitdepth, 10\n"
            "monitor = DP-1, 2560x1440, 0x0, 1\n",
            schema,
            tmp_path,
        )
        fields = result.entities.monitors[0].fields
        assert fields["mode"] == "2560x1440"
        assert "bitdepth" not in fields

    def test_monitorv2_block_merges_into_the_same_output(self, schema, tmp_path) -> None:
        result = _map(
            """
            monitor = DP-1, 1920x1080, 0x0, 1
            monitorv2 {
              output = DP-1
              vrr = 1
            }
            """,
            schema,
            tmp_path,
        )
        assert len(result.entities.monitors) == 1
        assert result.entities.monitors[0].fields["vrr"] == 1
        assert result.entities.monitors[0].fields["mode"] == "1920x1080"


# --- everything else ----------------------------------------------------------------------


class TestAnimationsAndCurves:
    def test_bezier_becomes_a_point_pair(self, schema, tmp_path) -> None:
        result = _map("bezier = ease, 0.25, 0.1, 0.25, 1\n", schema, tmp_path)
        curve = result.entities.curves[0]
        assert curve.name == "ease"
        assert curve.spec == {"type": "bezier", "points": [[0.25, 0.1], [0.25, 1.0]]}

    def test_bezier_outside_the_accepted_range_is_breakage(self, schema, tmp_path) -> None:
        result = _map("bezier = wild, 0.1, -3, 0.9, 5\n", schema, tmp_path)
        assert result.loss.of_class(LossClass.BREAKAGE)

    def test_animation_off_needs_no_other_field(self, schema, tmp_path) -> None:
        result = _map("animation = windows, 0\n", schema, tmp_path)
        assert result.entities.animations[0].fields == {"enabled": False}

    def test_animation_curve_is_the_bezier_field(self, schema, tmp_path) -> None:
        result = _map("animation = windows, 1, 4, ease, popin 80%\n", schema, tmp_path)
        fields = result.entities.animations[0].fields
        assert fields == {"enabled": True, "speed": 4.0, "bezier": "ease", "style": "popin 80%"}

    def test_speed_above_one_hundred_is_breakage(self, schema, tmp_path) -> None:
        result = _map("animation = windows, 1, 500, ease\n", schema, tmp_path)
        assert result.loss.of_class(LossClass.BREAKAGE)

    def test_the_last_animation_for_a_leaf_wins(self, schema, tmp_path) -> None:
        result = _map(
            "animation = windows, 1, 4, ease\nanimation = windows, 1, 8, ease\n",
            schema,
            tmp_path,
        )
        assert len(result.entities.animations) == 1
        assert result.entities.animations[0].fields["speed"] == 8.0


class TestGestures:
    def test_plain_action(self, schema, tmp_path) -> None:
        result = _map("gesture = 3, horizontal, workspace\n", schema, tmp_path)
        assert result.entities.gestures[0].fields == {
            "fingers": 3,
            "direction": "horizontal",
            "action": "workspace",
        }

    def test_mod_and_scale_tokens_in_any_order(self, schema, tmp_path) -> None:
        result = _map("gesture = 4, left, scale: 1.5, mod: ALT, close\n", schema, tmp_path)
        fields = result.entities.gestures[0].fields
        assert fields["mods"] == "ALT"
        assert fields["scale"] == 1.5
        assert fields["action"] == "close"

    def test_the_p_flag_is_disable_inhibit(self, schema, tmp_path) -> None:
        result = _map("gesturep = 3, horizontal, workspace\n", schema, tmp_path)
        assert result.entities.gestures[0].fields["disable_inhibit"] is True

    def test_camel_case_actions_are_renamed(self, schema, tmp_path) -> None:
        result = _map("gesture = 3, pinchin, cursorZoom, 2\n", schema, tmp_path)
        fields = result.entities.gestures[0].fields
        assert fields["action"] == "cursor_zoom"
        assert fields["zoom_level"] == 2.0

    def test_dispatcher_action_becomes_a_callback_and_is_reported(
        self, schema, tmp_path
    ) -> None:
        result = _map("gesture = 3, up, dispatcher, killactive\n", schema, tmp_path)
        fields = result.entities.gestures[0].fields
        assert str(fields["dispatch"]) == "hl.dsp.window.close()"
        assert LossCode.GESTURE_DISPATCHER in _codes(result)


class TestDevicesEnvAndPermissions:
    def test_device_block_renames_the_dashed_keys(self, schema, tmp_path) -> None:
        result = _map(
            """
            device {
              name = my mouse
              tap-to-click = 1
              sensitivity = 0.5
            }
            """,
            schema,
            tmp_path,
        )
        device = result.entities.devices[0]
        assert device.name == "my-mouse"
        assert device.fields == {"tap_to_click": "1", "sensitivity": "0.5"}

    def test_tablet_only_fields_are_breakage(self, schema, tmp_path) -> None:
        result = _map(
            "device {\n  name = pen\n  pressure_range_min = 0.1\n}\n", schema, tmp_path
        )
        assert result.loss.of_class(LossClass.BREAKAGE)
        assert "pressure_range_min" not in result.entities.devices[0].fields

    def test_env_splits_at_the_first_comma_only(self, schema, tmp_path) -> None:
        result = _map("env = GDK_BACKEND,wayland,x11,*\n", schema, tmp_path)
        var = result.entities.env[0]
        assert var.name == "GDK_BACKEND"
        assert var.value == "wayland,x11,*"
        assert var.dbus is False

    def test_envd_sets_the_dbus_flag(self, schema, tmp_path) -> None:
        result = _map("envd = XCURSOR_SIZE,24\n", schema, tmp_path)
        assert result.entities.env[0].dbus is True

    def test_permission(self, schema, tmp_path) -> None:
        result = _map("permission = /usr/bin/grim, screencopy, allow\n", schema, tmp_path)
        permission = result.entities.permissions[0]
        assert (permission.binary, permission.kind, permission.mode) == (
            "/usr/bin/grim",
            "screencopy",
            "allow",
        )


class TestStartupCommands:
    def test_exec_once_is_a_start_event(self, schema, tmp_path) -> None:
        result = _map("exec-once = waybar\n", schema, tmp_path)
        command = result.entities.startup[0]
        assert command.event == "hyprland.start"
        assert command.raw is False

    def test_exec_runs_on_every_reload_so_it_has_no_event(self, schema, tmp_path) -> None:
        result = _map("exec = waybar\n", schema, tmp_path)
        assert result.entities.startup[0].event == ""
        assert LossCode.EXEC_TIMING in _codes(result)

    def test_execr_is_raw(self, schema, tmp_path) -> None:
        result = _map("execr-once = waybar\n", schema, tmp_path)
        assert result.entities.startup[0].raw is True

    def test_exec_shutdown_is_a_shutdown_event(self, schema, tmp_path) -> None:
        result = _map("exec-shutdown = sync\n", schema, tmp_path)
        assert result.entities.startup[0].event == "hyprland.shutdown"

    @pytest.mark.parametrize(
        "command",
        [
            "hyprctl dispatch movefocus l",
            "hyprctl keyword general:border_size 3",
            'hyprctl --batch "dispatch cyclenext"',
        ],
    )
    def test_a_command_driving_the_legacy_engine_is_breakage(
        self, schema, tmp_path, command: str
    ) -> None:
        """The engine swap breaks these from outside the config, so nothing the Importer
        writes can fix them -- which is the definition of the Breakage class."""
        result = _map(f"exec-once = {command}\n", schema, tmp_path)
        breakage = result.loss.of_class(LossClass.BREAKAGE)
        assert [str(item.code) for item in breakage] == ["L29"]

    def test_the_same_check_covers_a_bind_that_shells_out(self, schema, tmp_path) -> None:
        result = _map("bind = SUPER, X, exec, hyprctl dispatch exit\n", schema, tmp_path)
        assert [str(i.code) for i in result.loss.of_class(LossClass.BREAKAGE)] == ["L29"]

    def test_an_ordinary_command_is_not_flagged(self, schema, tmp_path) -> None:
        result = _map("exec-once = waybar --config ~/.config/waybar\n", schema, tmp_path)
        assert not result.loss.of_class(LossClass.BREAKAGE)


class TestReferencedScripts:
    """ADR-0009 scopes the breakage grep to "all exec strings *and referenced local
    scripts*" -- and a rice that keeps its dispatches in `scripts/` is the common case."""

    def _rice(self, tmp_path: Path, command: str, script_body: str) -> Path:
        scripts = tmp_path / "scripts"
        scripts.mkdir(exist_ok=True)
        (scripts / "toggle.sh").write_text(script_body, encoding="utf-8")
        entry = tmp_path / "hyprland.conf"
        entry.write_text(f"exec-once = {command}\n", encoding="utf-8")
        return entry

    def _import(self, entry: Path, schema):  # type: ignore[no-untyped-def]
        return import_config(entry, schema, env={"HOME": str(entry.parent)})

    def test_a_script_that_dispatches_is_found(self, schema, tmp_path: Path) -> None:
        entry = self._rice(
            tmp_path,
            "~/scripts/toggle.sh",
            "#!/bin/sh\nhyprctl dispatch togglefloating\n",
        )
        result = self._import(entry, schema)
        breakage = result.loss.of_class(LossClass.BREAKAGE)
        assert [str(item.code) for item in breakage] == ["L29"]
        assert "toggle.sh:2" in breakage[0].message

    def test_a_relative_script_path_resolves_against_the_config_dir(
        self, schema, tmp_path: Path
    ) -> None:
        entry = self._rice(
            tmp_path, "scripts/toggle.sh", "#!/bin/sh\nhyprctl keyword general:border_size 3\n"
        )
        assert self._import(entry, schema).loss.of_class(LossClass.BREAKAGE)

    def test_an_innocent_script_is_not_flagged(self, schema, tmp_path: Path) -> None:
        entry = self._rice(tmp_path, "~/scripts/toggle.sh", "#!/bin/sh\nnotify-send hi\n")
        assert not self._import(entry, schema).loss.of_class(LossClass.BREAKAGE)

    def test_a_commented_out_dispatch_is_not_flagged(self, schema, tmp_path: Path) -> None:
        """A line the shell never runs is not breakage, and flagging it would train users
        to ignore the class."""
        entry = self._rice(
            tmp_path, "~/scripts/toggle.sh", "#!/bin/sh\n# hyprctl dispatch exit\necho hi\n"
        )
        assert not self._import(entry, schema).loss.of_class(LossClass.BREAKAGE)

    def test_a_missing_script_is_not_an_error(self, schema, tmp_path: Path) -> None:
        entry = tmp_path / "hyprland.conf"
        entry.write_text("exec-once = ~/scripts/gone.sh\n", encoding="utf-8")
        assert not self._import(entry, schema).loss.of_class(LossClass.BREAKAGE)

    def test_the_scan_stays_inside_the_home_it_was_given(self, schema, tmp_path: Path) -> None:
        """`~` resolves to the home the config was written for, not the one running the
        import -- the Harness stages a rice under a throwaway home, and previewing someone
        else's dotfiles must not go reading the current user's scripts."""
        other = tmp_path / "elsewhere"
        (other / "scripts").mkdir(parents=True)
        (other / "scripts" / "toggle.sh").write_text(
            "hyprctl dispatch exit\n", encoding="utf-8"
        )
        entry = tmp_path / "hyprland.conf"
        entry.write_text("exec-once = ~/scripts/toggle.sh\n", encoding="utf-8")
        result = import_config(entry, schema, env={"HOME": str(other)})
        assert result.loss.of_class(LossClass.BREAKAGE)
        without_home = import_config(entry, schema, env={})
        assert not without_home.loss.of_class(LossClass.BREAKAGE)

    def test_an_oversized_script_is_skipped_rather_than_read(
        self, schema, tmp_path: Path
    ) -> None:
        body = "# padding\n" * (MAX_SCRIPT_BYTES // 10 + 10) + "hyprctl dispatch exit\n"
        entry = self._rice(tmp_path, "~/scripts/toggle.sh", body)
        assert not self._import(entry, schema).loss.of_class(LossClass.BREAKAGE)


class TestOptions:
    def test_plain_options_reach_the_model(self, schema, tmp_path) -> None:
        result = _map("general {\n  border_size = 3\n}\n", schema, tmp_path)
        assert result.model.get("general:border_size") == 3

    def test_bool_words_are_normalised_and_reported(self, schema, tmp_path) -> None:
        result = _map("general:allow_tearing = yes\n", schema, tmp_path)
        assert result.model.get("general:allow_tearing") is True
        assert LossCode.VALUE_NORMALISED in _codes(result)

    def test_hyprlangs_prefix_rule_is_honoured(self, schema, tmp_path) -> None:
        """`yes, please :)` really is a valid `true` to hyprlang, and rices use it."""
        result = _map("animations:enabled = yes, please :)\n", schema, tmp_path)
        assert result.model.get("animations:enabled") is True
        assert not result.loss.of_class(LossClass.BREAKAGE)

    def test_a_moved_option_is_followed_to_its_new_home(self, schema, tmp_path) -> None:
        result = _map("misc:vfr = false\n", schema, tmp_path)
        assert result.model.get("debug:vfr") is False
        assert LossCode.REMOVED_OPTION in _codes(result)

    def test_a_removed_option_is_dropped_with_a_finding(self, schema, tmp_path) -> None:
        result = _map("debug:watchdog_timeout = 5\n", schema, tmp_path)
        assert "debug:watchdog_timeout" not in result.model
        assert LossCode.REMOVED_OPTION in _codes(result)

    def test_an_unknown_option_is_reported_not_raised(self, schema, tmp_path) -> None:
        result = _map("general:not_a_real_option = 1\n", schema, tmp_path)
        assert LossCode.REMOVED_OPTION in _codes(result)

    def test_an_unresolved_variable_is_named_as_such(self, schema, tmp_path) -> None:
        result = _map("general:col.active_border = $undefined\n", schema, tmp_path)
        assert LossCode.VARIABLE_UNRESOLVED in _codes(result)
        assert not result.loss.of_class(LossClass.BREAKAGE)

    def test_plugin_options_are_guarded_not_emitted(self, schema, tmp_path) -> None:
        result = _map("plugin:hyprbars:bar_height = 20\n", schema, tmp_path)
        assert LossCode.PLUGIN_GUARD in _codes(result)

    def test_the_root_config_is_not_reported_as_a_sourced_file(self, schema, tmp_path) -> None:
        """Nothing `source`d the entrypoint, so there is no `source =` line to convert.

        The root file enters the Keyword stream the same way an included one does, and
        treating the two alike put a spurious "was inlined" finding at the top of every
        report -- the first line a user reads, about a conversion that never happened.
        """
        result = _map("general:border_size = 3\n", schema, tmp_path)
        assert LossCode.SOURCE_REQUIRE not in _codes(result)

    def test_a_genuinely_sourced_file_is_still_reported(self, schema, tmp_path) -> None:
        (tmp_path / "extra.conf").write_text("general:gaps_in = 4\n", encoding="utf-8")
        result = _map("source = extra.conf\n", schema, tmp_path)
        assert LossCode.SOURCE_REQUIRE in _codes(result)
        assert result.model.get("general:gaps_in") is not None

    def test_an_unparsed_line_becomes_a_finding(self, schema, tmp_path) -> None:
        result = _map("just some words\n", schema, tmp_path)
        assert LossCode.UNPARSED_LINE in _codes(result)


class TestProvenance:
    def test_provenance_records_the_source_and_a_tree_hash(self, schema, tmp_path) -> None:
        result = _map("general:border_size = 3\n", schema, tmp_path)
        record = result.provenance()
        assert record["source"].endswith("hyprland.conf")
        assert len(record["tree_hash"]) == 64
        assert record["files"]

    def test_the_tree_hash_follows_the_contents(self, schema, tmp_path) -> None:
        first = _map("general:border_size = 3\n", schema, tmp_path).tree_hash()
        second = _map("general:border_size = 4\n", schema, tmp_path).tree_hash()
        assert first != second
