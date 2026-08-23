"""Recorded calls -> the model, the Entities, and `legacy.lua`.

The three-way split of ADR-0009 is what these assert: declarative calls land in the model,
script constructs land verbatim in `legacy.lua`, and a declarative call carrying a closure
is kept whole rather than half-imported.
"""

from __future__ import annotations

import pytest
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.importer.lua import Consent, import_lua, lua_binary
from hyprtweaker.engine.model.values import Color, CssGaps, Gradient, Vec2
from hyprtweaker.engine.schema import load_schema

pytestmark = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

GRANTED = Consent(evaluate=True)


@pytest.fixture(scope="module")
def schema():  # type: ignore[no-untyped-def]
    return load_schema(SAMPLE_VERSION, SCHEMA_DIR)


@pytest.fixture
def imported(tmp_path, schema):  # type: ignore[no-untyped-def]
    def run(body: str):  # type: ignore[no-untyped-def]
        entry = tmp_path / "hyprland.lua"
        entry.write_text(body, encoding="utf-8")
        return import_lua(entry, schema, consent=GRANTED)

    return run


def codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {item.code.value for item in result.loss}


# --- options ------------------------------------------------------------------------------


def test_a_nested_config_table_lands_on_the_right_options(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        "hl.config({ decoration = { rounding = 10 }, general = { border_size = 3 } })\n"
    )

    assert result.model.get("decoration:rounding") == 10
    assert result.model.get("general:border_size") == 3
    assert result.loss.clean


def test_the_two_ways_hyprland_spells_a_nested_key_both_resolve(imported) -> None:  # type: ignore[no-untyped-def]
    """`general:col.active_border` and `decoration:shadow:offset` nest identically in Lua
    and are spelled differently as Option names. The mapper resolves by the Option's own
    path, so it never has to know which separator a given key uses."""
    result = imported(
        """
        hl.config({
          general = { col = { active_border = { colors = { "rgba(33ccffee)" }, angle = 45 } } },
          decoration = { shadow = { offset = { 2, 4 } } },
        })
        """
    )

    assert result.model.get("general:col.active_border") == Gradient(
        colors=(Color.parse("rgba(33ccffee)"),), angle=45.0
    )
    assert result.model.get("decoration:shadow:offset") == Vec2(2.0, 4.0)


def test_the_complex_value_shapes_come_back_as_their_own_types(imported) -> None:  # type: ignore[no-untyped-def]
    """The inverse of what the writer emits: a css-gap is a four-key table, never text."""
    result = imported(
        "hl.config({ general = { gaps_in = { top = 4, right = 8, bottom = 4, left = 8 } } })\n"
    )

    assert result.model.get("general:gaps_in") == CssGaps(4, 8, 4, 8)


def test_a_setting_this_build_does_not_have_is_reported_not_dropped(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported("hl.config({ general = { invented_setting = 1 } })\n")

    assert "L30" in codes(result)
    assert any("invented_setting" in item.message for item in result.loss)


def test_a_value_the_option_will_not_take_is_reported_not_dropped(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported('hl.config({ decoration = { rounding = "not a number" } })\n')

    assert not result.model.is_set("decoration:rounding")
    assert "L14" in codes(result)


def test_a_loop_that_declares_options_is_just_declarations(imported) -> None:  # type: ignore[no-untyped-def]
    """The whole reason this importer evaluates instead of parsing."""
    result = imported(
        """
        local sizes = { rounding = 6, border_size = 2 }
        hl.config({ decoration = { rounding = sizes.rounding } })
        hl.config({ general = { border_size = sizes.border_size } })
        """
    )

    assert result.model.get("decoration:rounding") == 6
    assert result.model.get("general:border_size") == 2


# --- entities -----------------------------------------------------------------------------


def test_a_bind_with_a_dispatcher_becomes_a_bind_entity(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        'hl.bind("SUPER + Q", hl.dsp.window.close(),\n'
        '        { repeating = true, description = "close" })\n'
    )

    bind = result.entities.binds[0]
    assert bind.keys == "SUPER + Q"
    assert bind.dispatcher is not None
    assert bind.dispatcher.path == "window.close"
    assert bind.options.repeating is True
    assert bind.options.description == "close"


def test_a_dispatcher_keeps_its_arguments(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported('hl.bind("SUPER + E", hl.dsp.exec_cmd("kitty"))\n')

    dispatcher = result.entities.binds[0].dispatcher
    assert dispatcher is not None
    assert dispatcher.path == "exec_cmd"
    assert dispatcher.positional == ("kitty",)


def test_binds_inside_a_submap_belong_to_it(imported) -> None:  # type: ignore[no-untyped-def]
    """`define_submap` runs its body, so submap membership is a fact of the run rather
    than something the mapper has to infer from position."""
    result = imported(
        """
        hl.bind("SUPER + R", hl.dsp.submap("resize"))
        hl.define_submap("resize", function()
          hl.bind("right", hl.dsp.window.resize({ x = 10, y = 0 }))
          hl.bind("escape", hl.dsp.submap("reset"))
        end)
        """
    )

    assert [bind.submap for bind in result.entities.binds] == [None, "resize", "resize"]
    assert result.entities.submaps[0].name == "resize"


def test_rules_split_into_match_and_effects(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        'hl.window_rule({ name = "float-pavu",\n'
        '                 match = { class = "pavucontrol" }, float = true })\n'
    )

    rule = result.entities.window_rules[0]
    assert rule.match == {"class": "pavucontrol"}
    assert rule.effects == {"float": True}
    assert rule.name == "float-pavu"


def test_each_entity_kind_reaches_its_own_list(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        """
        hl.monitor({ output = "DP-1", mode = "1920x1080@144" })
        hl.workspace_rule({ workspace = "name:code", monitor = "DP-1" })
        hl.layer_rule({ match = { namespace = "rofi" }, blur = true })
        hl.curve("easy", { type = "spring", mass = 1, stiffness = 238, dampening = 24 })
        hl.animation({ leaf = "windowsIn", enabled = true, speed = 4 })
        hl.device({ name = "epic-mouse", sensitivity = -0.5 })
        hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })
        hl.env("XCURSOR_SIZE", "24")
        hl.permission("/usr/bin/grim", "screencopy", "allow")
        hl.plugin.load("/tmp/hyprbars.so")
        hl.exec_cmd("waybar")
        """
    )

    counts = result.entities.counts()
    assert counts["monitors"] == 1
    assert counts["workspace_rules"] == 1
    assert counts["layer_rules"] == 1
    assert counts["curves"] == 1
    assert counts["animations"] == 1
    assert counts["devices"] == 1
    assert counts["gestures"] == 1
    assert counts["env"] == 1
    assert counts["permissions"] == 1
    assert counts["plugins"] == 1
    assert counts["startup"] == 1
    assert result.entities.env[0].value == "24"
    assert result.entities.monitors[0].output == "DP-1"


def test_a_permission_written_as_a_table_reads_the_same(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        'hl.permission({ binary = "/usr/bin/grim", type = "screencopy", mode = "allow" })\n'
    )

    permission = result.entities.permissions[0]
    assert (permission.binary, permission.kind, permission.mode) == (
        "/usr/bin/grim",
        "screencopy",
        "allow",
    )


# --- script constructs --------------------------------------------------------------------


def test_an_event_handler_is_kept_verbatim(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        """
        hl.on("hyprland.start", function()
          -- a comment inside the handler
          hl.exec_cmd("waybar")
        end)
        """
    )

    assert "L32" in codes(result)
    assert 'hl.on("hyprland.start"' in result.legacy
    assert "-- a comment inside the handler" in result.legacy, "kept verbatim means verbatim"
    assert not result.entities.startup, "the exec inside a handler is not a startup command"


def test_a_declarative_call_carrying_a_closure_is_kept_whole(imported) -> None:  # type: ignore[no-untyped-def]
    """The hybrid case. Half of this bind fits the model; importing half would drop the
    behaviour the closure *is*, and leave a Row claiming the bind does nothing."""
    result = imported('hl.bind("SUPER + X", function() hl.exec_cmd("thing") end)\n')

    assert "L32" in codes(result)
    assert "hl.bind" in result.legacy
    assert not result.entities.binds, "a function-valued Action is not a modelled Bind"


def test_a_lifted_closure_takes_the_values_it_closed_over_with_it(imported) -> None:  # type: ignore[no-untyped-def]
    """Source text alone is not a closure: without its upvalues the lifted copy is a
    dangling reference to a local that no longer exists."""
    result = imported(
        """
        local terminal = "kitty"
        hl.on("hyprland.start", function() hl.exec_cmd(terminal) end)
        """
    )

    assert 'local terminal = "kitty"' in result.legacy


def test_a_custom_layout_is_kept_and_never_authored(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported(
        'hl.layout.register("columns", { recalculate = function(ctx) return ctx end })\n'
    )

    assert "L32" in codes(result)
    assert "hl.layout.register" in result.legacy


def test_an_unmodelled_call_is_kept_rather_than_lost(imported) -> None:  # type: ignore[no-untyped-def]
    result = imported('hl.notification.error("something")\n')

    assert "L38" in codes(result)
    assert "hl.notification" in result.legacy


# --- what running the config cost ---------------------------------------------------------


def test_shelling_out_at_load_time_is_breakage_not_a_note(imported) -> None:  # type: ignore[no-untyped-def]
    """A theme engine that discovers its colours by running a command produces a config
    that will not re-discover them once imported. Nothing can fix that, so it is reported
    as Breakage rather than quietly succeeding."""
    result = imported(
        """
        local p = io.popen("cat ~/.cache/theme")
        p:close()
        hl.config({ decoration = { rounding = 5 } })
        """
    )

    assert "L34" in codes(result)
    assert result.loss.breakage
    assert not result.loss.clean


def test_asking_the_compositor_at_load_time_needs_review(imported) -> None:  # type: ignore[no-untyped-def]
    """There is no compositor here, so whatever the config decided from the answer is
    baked -- the same treatment ADR-0009 gives a baked `# hyprlang if`."""
    result = imported(
        """
        local monitors = hl.get_monitors()
        hl.config({ decoration = { rounding = #monitors } })
        """
    )

    assert "L37" in codes(result)
    assert result.model.get("decoration:rounding") == 0


def test_a_plugin_namespace_is_absent_until_it_is_loaded(imported) -> None:  # type: ignore[no-untyped-def]
    """Configs guard on this, and a stub answering truthy bakes the wrong branch -- which
    produces output the engine then rejects."""
    result = imported(
        """
        if hl.plugin.hyprbars ~= nil then
          hl.config({ decoration = { rounding = 99 } })
        else
          hl.config({ decoration = { rounding = 1 } })
        end
        """
    )

    assert result.model.get("decoration:rounding") == 1


def test_a_passthrough_run_says_so_in_the_report(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """The user agreed to it, and the report is where that decision is recorded."""
    entry = tmp_path / "hyprland.lua"
    entry.write_text("hl.config({ decoration = { rounding = 1 } })\n", encoding="utf-8")

    result = import_lua(entry, schema, consent=Consent(evaluate=True, passthrough=True))

    assert "L35" in codes(result)


def test_a_function_inside_hl_config_is_kept_not_blamed_on_a_typo(imported) -> None:  # type: ignore[no-untyped-def]
    """`hl.config` is exempt from the hybrid rule because a config table is walked, not
    mapped whole. Without a case for it the walk descends into the recorded closure and
    reports the literal key `__fn` as a setting this Hyprland does not have -- a script
    construct dropped in silence and blamed on the user's spelling.
    """
    result = imported("hl.config({ general = { layout = function() return 1 end } })\n")

    assert "L32" in codes(result), "the closure should be kept, not reported as a typo"
    assert "L30" not in codes(result), "reported as an unknown setting"
    assert "__fn" not in result.legacy and "__fn" not in str(
        [item.message for item in result.loss]
    ), "the recorder's internal marker leaked into user-facing output"
    assert "hl.config" in result.legacy


def test_reading_state_outside_the_config_tree_is_reported(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """The theme-engine case: a config that discovers its colours from a cache file has
    baked them, and the imported copy will never read that file again."""
    outside = tmp_path / "cache"
    outside.mkdir()
    (outside / "theme.txt").write_text("7", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    entry = config / "hyprland.lua"
    entry.write_text(
        f'local f = io.open("{outside / "theme.txt"}", "r")\n'
        "local v = tonumber(f:read('a'))\nf:close()\n"
        "hl.config({ decoration = { rounding = v } })\n",
        encoding="utf-8",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert result.model.get("decoration:rounding") == 7, "the value still imports"
    assert "L34" in codes(result), "but the import does not pretend it will re-read it"


def test_reading_its_own_modules_is_not_a_finding(tmp_path, schema) -> None:  # type: ignore[no-untyped-def]
    """Reading files inside the config tree is how a config is written -- reporting it
    would bury the real finding under noise."""
    entry = tmp_path / "hyprland.lua"
    (tmp_path / "colors.txt").write_text("9", encoding="utf-8")
    entry.write_text(
        'local f = io.open("colors.txt", "r")\n'
        "local v = tonumber(f:read('a'))\nf:close()\n"
        "hl.config({ decoration = { rounding = v } })\n",
        encoding="utf-8",
    )

    result = import_lua(entry, schema, consent=GRANTED)

    assert result.model.get("decoration:rounding") == 9
    assert "L34" not in codes(result)
