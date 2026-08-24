"""The six declarative Entity Modules: rendering, goldens, and the read-back (#70).

One file for six kinds because they share a renderer shell and a reader, and the
properties worth asserting are the same six times: the call shape Hyprland's parser takes,
a deterministic field order, and a round trip that reaches a *fixpoint* -- render, read,
render again, identical bytes. The fixpoint is the one that catches the expensive bug:
these Modules are read back through a Lua recorder that hands fields over in its own key
order, so "emit the fields as held" would spell the same entity two ways and produce a
phantom write on every startup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _golden import assert_matches_golden

from hyprtweaker.engine.importer.lua import lua_binary
from hyprtweaker.engine.model.entities import (
    Animation,
    Curve,
    Device,
    DispatcherCall,
    EnvVar,
    Gesture,
    Permission,
    StartupCommand,
)
from hyprtweaker.engine.writer.animations import (
    render_animation,
    render_animations_module,
    render_curve,
)
from hyprtweaker.engine.writer.declarations import parse_declarations_module
from hyprtweaker.engine.writer.inputs import (
    render_device,
    render_devices_module,
    render_gesture,
    render_gestures_module,
)
from hyprtweaker.engine.writer.sticky import (
    render_autostart_module,
    render_env,
    render_env_module,
    render_permission,
    render_permissions_module,
    render_startup_command,
)

VERSION = "0.1.0"

GOLDEN = Path(__file__).parent.parent / "golden" / "writer"

needs_lua = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter")


# --- one call at a time -------------------------------------------------------------------


class TestRenderCalls:
    def test_a_bezier_curve_names_itself_positionally(self) -> None:
        """`hl.curve` is the one declarative call whose identity is an argument."""
        curve = Curve("easy", {"type": "bezier", "points": [[0.23, 1], [0.32, 1]]})
        assert render_curve(curve) == (
            'hl.curve("easy", { type = "bezier", points = { { 0.23, 1 }, { 0.32, 1 } } })'
        )

    def test_a_spring_curve_keeps_the_wikis_field_order(self) -> None:
        curve = Curve(
            "bouncy", {"dampening": 24.2, "stiffness": 238.1, "type": "spring", "mass": 1}
        )
        assert render_curve(curve) == (
            'hl.curve("bouncy", { type = "spring", mass = 1, stiffness = 238.1, '
            "dampening = 24.2 })"
        )

    def test_an_animation_leads_with_its_leaf(self) -> None:
        animation = Animation("windowsIn", {"speed": 4.1, "bezier": "easy", "enabled": True})
        assert render_animation(animation) == (
            'hl.animation({ leaf = "windowsIn", enabled = true, speed = 4.1, bezier = "easy" })'
        )

    def test_a_disabled_animation_needs_no_curve(self) -> None:
        assert render_animation(Animation("border", {"enabled": False})) == (
            'hl.animation({ leaf = "border", enabled = false })'
        )

    def test_a_gesture_leads_with_its_trigger(self) -> None:
        gesture = Gesture(
            {"scale": 1.5, "action": "workspace", "direction": "horizontal", "fingers": 3}
        )
        assert render_gesture(gesture) == (
            'hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace", '
            "scale = 1.5 })"
        )

    def test_a_device_leads_with_its_name(self) -> None:
        device = Device("epic-mouse-v1", {"sensitivity": -0.5, "natural_scroll": True})
        assert render_device(device) == (
            'hl.device({ name = "epic-mouse-v1", natural_scroll = true, sensitivity = -0.5 })'
        )

    def test_a_plain_variable_uses_the_two_argument_form(self) -> None:
        """The three-argument `dbus` export is source-only; a plain variable must not use it."""
        assert render_env(EnvVar("XCURSOR_SIZE", "24")) == 'hl.env("XCURSOR_SIZE", "24")'

    def test_a_dbus_variable_adds_the_third_argument(self) -> None:
        assert render_env(EnvVar("XDG_CURRENT_DESKTOP", "Hyprland", dbus=True)) == (
            'hl.env("XDG_CURRENT_DESKTOP", "Hyprland", true)'
        )

    def test_a_value_holding_commas_survives_as_one_string(self) -> None:
        """`env = GDK_BACKEND,wayland,x11,*` split on the first comma only in hyprlang."""
        assert render_env(EnvVar("GDK_BACKEND", "wayland,x11,*")) == (
            'hl.env("GDK_BACKEND", "wayland,x11,*")'
        )

    def test_a_permission_says_which_string_is_which(self) -> None:
        permission = Permission("/usr/(bin|local/bin)/grim", "screencopy", "allow")
        assert render_permission(permission) == (
            'hl.permission({ binary = "/usr/(bin|local/bin)/grim", type = "screencopy", '
            'mode = "allow" })'
        )

    def test_a_raw_command_goes_through_exec_raw(self) -> None:
        """`execr`'s whole point: no `[rules] cmd` prefix parsing."""
        assert render_startup_command(StartupCommand("[a] b", raw=True)) == (
            'hl.dispatch(hl.dsp.exec_raw("[a] b"))'
        )


# --- whole Modules ------------------------------------------------------------------------


class TestModuleShape:
    def test_an_empty_kind_renders_no_module(self) -> None:
        """`None` is what makes the writer prune -- absence is how the model spells "none"."""
        assert render_gestures_module([], app_version=VERSION) is None
        assert render_devices_module([], app_version=VERSION) is None
        assert render_env_module([], app_version=VERSION) is None
        assert render_permissions_module([], app_version=VERSION) is None
        assert render_autostart_module([], app_version=VERSION) is None
        assert render_animations_module([], [], app_version=VERSION) is None

    def test_curves_are_declared_before_the_animations_that_name_them(self) -> None:
        """Hyprland refuses `bezier = "easy"` unless the curve already exists (CR:430-441)."""
        text = render_animations_module(
            [Curve("easy", {"type": "bezier", "points": [[0.2, 1], [0.3, 1]]})],
            [Animation("windowsIn", {"enabled": True, "bezier": "easy"})],
            app_version=VERSION,
        )
        assert text is not None
        assert text.index('hl.curve("easy"') < text.index("hl.animation(")

    def test_a_curve_no_animation_uses_is_still_written(self) -> None:
        text = render_animations_module(
            [Curve("spare", {"type": "bezier", "points": [[0, 0], [1, 1]]})],
            [],
            app_version=VERSION,
        )
        assert text is not None
        assert 'hl.curve("spare"' in text

    def test_a_scripted_gesture_is_held_but_not_written(self) -> None:
        """Its action is Lua the app never authored; it lives in `user.lua` (ADR-0007)."""
        scripted = Gesture({"fingers": 4, "direction": "up", "action": {"__fn": 3}})
        plain = Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"})

        text = render_gestures_module([scripted, plain], app_version=VERSION)

        assert text is not None
        assert text.count("hl.gesture(") == 1
        assert "workspace" in text

    def test_a_gesture_module_of_only_scripted_gestures_is_pruned(self) -> None:
        scripted = Gesture({"fingers": 4, "direction": "up", "action": {"__fn": 3}})
        assert render_gestures_module([scripted], app_version=VERSION) is None

    def test_a_device_with_no_overrides_is_still_written(self) -> None:
        """It is how a user says "this device, defaults" while they experiment."""
        text = render_devices_module([Device("my-kb", {})], app_version=VERSION)
        assert text is not None
        assert 'hl.device({ name = "my-kb" })' in text

    def test_run_once_commands_share_one_handler_block(self) -> None:
        text = render_autostart_module(
            [StartupCommand("waybar"), StartupCommand("swaync")], app_version=VERSION
        )
        assert text is not None
        assert text.count('hl.on("hyprland.start"') == 1
        assert text.count("hl.exec_cmd(") == 2

    def test_every_reload_commands_come_first_and_stay_top_level(self) -> None:
        """The surprising timing is the one a reader must not have to hunt for."""
        text = render_autostart_module(
            [StartupCommand("waybar"), StartupCommand("pkill -x foo", event="")],
            app_version=VERSION,
        )
        assert text is not None
        assert text.index('hl.exec_cmd("pkill -x foo")') < text.index('hl.on("hyprland.start"')

    def test_a_shutdown_command_gets_its_own_block(self) -> None:
        text = render_autostart_module(
            [StartupCommand("save", event="hyprland.shutdown")], app_version=VERSION
        )
        assert text is not None
        assert 'hl.on("hyprland.shutdown"' in text


# --- goldens ------------------------------------------------------------------------------


def _sample_animations() -> tuple[list[Curve], list[Animation]]:
    curves = [
        Curve("easeOutQuint", {"type": "bezier", "points": [[0.23, 1], [0.32, 1]]}),
        Curve("easy", {"type": "spring", "mass": 1, "stiffness": 238.1191, "dampening": 24.2}),
    ]
    animations = [
        Animation(
            "windowsIn", {"enabled": True, "speed": 4.1, "spring": "easy", "style": "popin 87%"}
        ),
        Animation("windowsOut", {"enabled": True, "speed": 1.49, "bezier": "easeOutQuint"}),
        Animation("border", {"enabled": False}),
    ]
    return curves, animations


def _sample_gestures() -> list[Gesture]:
    return [
        Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"}),
        Gesture({"fingers": 4, "direction": "pinchin", "action": "close", "mods": "SUPER"}),
        Gesture(
            {
                "fingers": 3,
                "direction": "up",
                "action": "special",
                "workspace_name": "magic",
                "scale": 1.5,
                "disable_inhibit": True,
            }
        ),
    ]


def _sample_devices() -> list[Device]:
    return [
        Device("epic-mouse-v1", {"sensitivity": -0.5, "natural_scroll": True}),
        Device("at-translated-set-2-keyboard", {"kb_layout": "us,de", "repeat_rate": 50}),
        Device("my-tablet", {"region_position": [10, 20], "output": "DP-1"}),
    ]


def _sample_env() -> list[EnvVar]:
    return [
        EnvVar("XCURSOR_SIZE", "24"),
        EnvVar("GDK_BACKEND", "wayland,x11,*"),
        EnvVar("XDG_CURRENT_DESKTOP", "Hyprland", dbus=True),
    ]


def _sample_permissions() -> list[Permission]:
    return [
        Permission("/usr/(bin|local/bin)/grim", "screencopy", "allow"),
        Permission("/usr/bin/hyprlock", "keyboard", "deny"),
    ]


def _sample_startup() -> list[StartupCommand]:
    return [
        StartupCommand("waybar"),
        StartupCommand("swaync"),
        StartupCommand("[workspace 2 silent] kitty", raw=True),
        StartupCommand("pkill -x hyprpaper", event=""),
        StartupCommand("hyprctl-save-state", event="hyprland.shutdown"),
    ]


class TestGoldens:
    def test_golden_animations(self) -> None:
        curves, animations = _sample_animations()
        text = render_animations_module(curves, animations, app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "animations.lua", "animations.lua")

    def test_golden_gestures(self) -> None:
        text = render_gestures_module(_sample_gestures(), app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "gestures.lua", "gestures.lua")

    def test_golden_devices(self) -> None:
        text = render_devices_module(_sample_devices(), app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "devices.lua", "devices.lua")

    def test_golden_env(self) -> None:
        text = render_env_module(_sample_env(), app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "env.lua", "env.lua")

    def test_golden_permissions(self) -> None:
        text = render_permissions_module(_sample_permissions(), app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "permissions.lua", "permissions.lua")

    def test_golden_autostart(self) -> None:
        text = render_autostart_module(_sample_startup(), app_version=VERSION)
        assert text is not None
        assert_matches_golden(text, GOLDEN / "autostart.lua", "autostart.lua")


# --- reading back -------------------------------------------------------------------------


@needs_lua
class TestRoundTrip:
    def test_animations_and_curves_round_trip(self) -> None:
        curves, animations = _sample_animations()
        text = render_animations_module(curves, animations, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="animations.lua")

        assert parsed.ok, parsed.errors
        assert [(c.name, dict(c.spec)) for c in parsed.curves] == [
            (c.name, dict(c.spec)) for c in curves
        ]
        assert [(a.leaf, dict(a.fields)) for a in parsed.animations] == [
            (a.leaf, dict(a.fields)) for a in animations
        ]

    def test_gestures_round_trip(self) -> None:
        gestures = _sample_gestures()
        text = render_gestures_module(gestures, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="gestures.lua")

        assert parsed.ok, parsed.errors
        assert [dict(g.fields) for g in parsed.gestures] == [dict(g.fields) for g in gestures]

    def test_devices_round_trip(self) -> None:
        devices = _sample_devices()
        text = render_devices_module(devices, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="devices.lua")

        assert parsed.ok, parsed.errors
        assert [(d.name, dict(d.fields)) for d in parsed.devices] == [
            (d.name, dict(d.fields)) for d in devices
        ]

    def test_env_round_trips_including_the_dbus_flag(self) -> None:
        variables = _sample_env()
        text = render_env_module(variables, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="env.lua")

        assert parsed.ok, parsed.errors
        assert [(v.name, v.value, v.dbus) for v in parsed.env] == [
            (v.name, v.value, v.dbus) for v in variables
        ]

    def test_permissions_round_trip(self) -> None:
        permissions = _sample_permissions()
        text = render_permissions_module(permissions, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="permissions.lua")

        assert parsed.ok, parsed.errors
        assert [(p.binary, p.kind, p.mode) for p in parsed.permissions] == [
            (p.binary, p.kind, p.mode) for p in permissions
        ]

    def test_a_positional_permission_reads_back_the_same_as_the_table_form(self) -> None:
        """The upstream example uses the positional shape; a hand edit may too."""
        parsed = parse_declarations_module(
            'hl.permission("/usr/bin/grim", "screencopy", "allow")\n',
            module="permissions.lua",
        )

        assert parsed.ok, parsed.errors
        assert [(p.binary, p.kind, p.mode) for p in parsed.permissions] == [
            ("/usr/bin/grim", "screencopy", "allow")
        ]

    def test_autostart_round_trips_through_its_handler_block(self) -> None:
        """The run-once commands are inside an `hl.on` closure the recorder must enter.

        Without that, the model would read back zero startup commands, the writer would
        render no Module, and the prune would delete the user's autostart.
        """
        commands = _sample_startup()
        text = render_autostart_module(commands, app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="autostart.lua")

        assert parsed.ok, parsed.errors
        assert sorted((c.command, c.event, c.raw) for c in parsed.startup) == sorted(
            (c.command, c.event, c.raw) for c in commands
        )

    def test_a_misfiled_entity_comes_back_as_what_it_is(self) -> None:
        """A gesture hand-pasted into `devices.lua` is config that works; it must not vanish."""
        text = (
            'hl.device({ name = "mouse", sensitivity = 0.2 })\n'
            'hl.gesture({ fingers = 3, direction = "horizontal", action = "workspace" })\n'
        )

        parsed = parse_declarations_module(text, module="devices.lua")

        assert parsed.ok, parsed.errors
        assert [d.name for d in parsed.devices] == ["mouse"]
        assert [g.fields["action"] for g in parsed.gestures] == ["workspace"]

    def test_a_leaf_declared_twice_comes_back_as_the_winner(self) -> None:
        """Hyprland keeps the last one, so the model must not hold both."""
        text = (
            'hl.animation({ leaf = "windowsIn", enabled = true, speed = 1 })\n'
            'hl.animation({ leaf = "windowsIn", enabled = true, speed = 9 })\n'
        )

        parsed = parse_declarations_module(text, module="animations.lua")

        assert parsed.ok, parsed.errors
        assert [(a.leaf, a.fields["speed"]) for a in parsed.animations] == [("windowsIn", 9)]

    def test_a_device_declared_twice_merges_field_wise(self) -> None:
        """`hl.device` merges per name (`lua-api-surface.md` §12)."""
        text = (
            'hl.device({ name = "kb", kb_layout = "us" })\n'
            'hl.device({ name = "kb", repeat_rate = 50 })\n'
        )

        parsed = parse_declarations_module(text, module="devices.lua")

        assert parsed.ok, parsed.errors
        assert len(parsed.devices) == 1
        assert dict(parsed.devices[0].fields) == {"kb_layout": "us", "repeat_rate": 50}

    def test_a_module_that_will_not_evaluate_reports_rather_than_reads_empty(self) -> None:
        """Empty would license the prune; an error keeps the Writer's hands off the file."""
        parsed = parse_declarations_module("this is not lua(((\n", module="env.lua")

        assert not parsed.ok
        assert parsed.env == ()


@needs_lua
class TestFixpoint:
    """Render, read, render again: identical bytes, or every startup writes phantom files.

    The Manifest stores a content hash per Module and the Apply transaction skips a write
    whose bytes did not change -- so a renderer whose output depends on how the model was
    filled produces a write, a reload, and a hand-edit warning on a config nobody touched.
    """

    def _fixpoint(self, text: str, module: str, render_again: object) -> None:
        parsed = parse_declarations_module(text, module=module)
        assert parsed.ok, parsed.errors
        again = render_again(parsed)  # type: ignore[operator]
        assert again == text

    def test_animations_reach_a_fixpoint(self) -> None:
        curves, animations = _sample_animations()
        text = render_animations_module(curves, animations, app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text,
            "animations.lua",
            lambda p: render_animations_module(
                list(p.curves), list(p.animations), app_version=VERSION
            ),
        )

    def test_gestures_reach_a_fixpoint(self) -> None:
        text = render_gestures_module(_sample_gestures(), app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text,
            "gestures.lua",
            lambda p: render_gestures_module(list(p.gestures), app_version=VERSION),
        )

    def test_devices_reach_a_fixpoint(self) -> None:
        text = render_devices_module(_sample_devices(), app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text,
            "devices.lua",
            lambda p: render_devices_module(list(p.devices), app_version=VERSION),
        )

    def test_env_reaches_a_fixpoint(self) -> None:
        text = render_env_module(_sample_env(), app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text, "env.lua", lambda p: render_env_module(list(p.env), app_version=VERSION)
        )

    def test_permissions_reach_a_fixpoint(self) -> None:
        text = render_permissions_module(_sample_permissions(), app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text,
            "permissions.lua",
            lambda p: render_permissions_module(list(p.permissions), app_version=VERSION),
        )

    def test_autostart_reaches_a_fixpoint(self) -> None:
        text = render_autostart_module(_sample_startup(), app_version=VERSION)
        assert text is not None
        self._fixpoint(
            text,
            "autostart.lua",
            lambda p: render_autostart_module(list(p.startup), app_version=VERSION),
        )

    def test_field_order_does_not_depend_on_how_the_model_was_filled(self) -> None:
        """The property the fixpoint rests on, asserted directly."""
        one = Device("mouse", {"sensitivity": 0.2, "natural_scroll": True})
        other = Device("mouse", {"natural_scroll": True, "sensitivity": 0.2})

        assert render_device(one) == render_device(other)

    def test_an_integral_float_is_written_the_way_a_person_writes_it(self) -> None:
        """The Importer produces `1.0`; Lua hands `1` back. Emitting `1.0` never settles.

        Left unfixed this is one phantom write, reload and hand-edit warning per Module on
        the first startup after an import -- the corpus caught it, no fixture did.
        """
        assert "{ 0.05, 1 }" in render_curve(
            Curve("c", {"type": "bezier", "points": [[0.05, 1.0], [0.1, 1.0]]})
        )
        assert "speed = 4 }" in render_animation(Animation("fade", {"speed": 4.0}))

    def test_a_genuine_fraction_keeps_its_decimals(self) -> None:
        assert "speed = 4.1 }" in render_animation(Animation("fade", {"speed": 4.1}))

    def test_a_boolean_does_not_become_a_number(self) -> None:
        """`True` is an `int` subclass in Python; canonicalising numbers must not catch it."""
        assert "enabled = true" in render_animation(Animation("fade", {"enabled": True}))


class TestDispatcherGestures:
    """An imported `gesture = …, dispatcher, …` has no string form in Lua (L12).

    The importer parks the parsed call under `dispatch`, which is not a key `hl.gesture`
    accepts: writing it would put an unknown field in the file and take the Module down.
    The only spelling Hyprland has is a callback.
    """

    def test_a_dispatcher_gesture_becomes_a_callback(self) -> None:
        gesture = Gesture(
            {
                "fingers": 4,
                "direction": "up",
                "dispatch": DispatcherCall(path="global", positional=("quickshell:toggle",)),
            }
        )

        rendered = render_gesture(gesture)

        assert "dispatch =" not in rendered
        assert "action = function() hl.dispatch(hl.dsp.global(" in rendered

    @needs_lua
    def test_a_dispatcher_gesture_round_trips_and_stays_editable(self) -> None:
        """Without recovery it would come back opaque, and be un-editable after a restart."""
        gesture = Gesture(
            {
                "fingers": 3,
                "direction": "left",
                "dispatch": DispatcherCall(path="layout", positional=("move +col",)),
            }
        )
        text = render_gestures_module([gesture], app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="gestures.lua")

        assert parsed.ok, parsed.errors
        assert [dict(g.fields) for g in parsed.gestures] == [dict(gesture.fields)]

    @needs_lua
    def test_a_dispatcher_gesture_reaches_a_fixpoint(self) -> None:
        gesture = Gesture(
            {
                "fingers": 3,
                "direction": "right",
                "dispatch": DispatcherCall(path="layout", positional=("move -col",)),
            }
        )
        text = render_gestures_module([gesture], app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="gestures.lua")

        assert render_gestures_module(list(parsed.gestures), app_version=VERSION) == text

    @needs_lua
    def test_the_dispatcher_does_not_leak_out_as_an_autostart_command(self) -> None:
        """`hl.dispatch` inside the callback belongs to the swipe, not to the file."""
        gesture = Gesture(
            {
                "fingers": 3,
                "direction": "up",
                "dispatch": DispatcherCall(path="global", positional=("x",)),
            }
        )
        text = render_gestures_module([gesture], app_version=VERSION)
        assert text is not None

        parsed = parse_declarations_module(text, module="gestures.lua")

        assert parsed.startup == ()
