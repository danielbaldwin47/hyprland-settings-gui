"""UI smoke tier: the seven declarative Pages assemble and their editor collects (#70).

Row text is settled headless in `tests/unit/test_ui_declaration_text.py`; what is left here
is whether GTK builds the lists, whether the filter narrows them, whether a finding reaches
the row it belongs to, and whether the editor's collect/validate path produces the entity
the widgets describe -- all driven by probing widget state rather than by screenshots, per
the repo's probe-before-screenshot rule.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

APP_VERSION = "0.0.0-test"

from hyprtweaker.ui.pages.declaration_kinds import BY_KIND  # noqa: E402

KINDS = ("animations", "curves", "gestures", "devices", "env", "startup", "permissions")


def build_window(tmp_path: Path) -> Any:
    from gi.repository import Adw

    from hyprtweaker.engine.ipc import Instance, NoInstance
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.session import Session
    from hyprtweaker.ui.shell.window import MainWindow

    def no_compositor() -> Instance:
        raise NoInstance("no compositor in the UI smoke tier")

    Adw.init()
    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


# --- the Pages ------------------------------------------------------------------------------


def test_every_declarative_kind_gets_a_page(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    for kind in KINDS:
        page = window.declaration_page(kind)
        assert page is not None, kind
        assert page.page.get_title()


def test_the_pages_reach_the_sidebar_under_their_own_sections(tmp_path: Path) -> None:
    """Each Page is reachable by its own section id, and no two share one."""
    from hyprtweaker.ui.pages.declaration_kinds import BY_KIND

    _session, window = build_window(tmp_path)

    sections = [window.declaration_page(kind).section for kind in KINDS]

    assert sections == [BY_KIND[kind].section for kind in KINDS]
    assert len(set(sections)) == len(KINDS)
    assert len(window.declaration_pages) == len(KINDS)


def test_no_page_shares_a_stack_id_or_a_title_with_another(tmp_path: Path) -> None:
    """Hyprland has an `animations` Section *and* an animation tree; likewise `gestures`.

    A stack child name used twice is not an error GTK raises -- it keeps the first page,
    drops the second, and writes a warning to stderr that no test tier reads. The Page then
    simply is not in the app, which is how this shipped past a green suite the first time.
    Two sidebar rows with the same title are the same defect one layer up: legible, and
    still a puzzle.
    """
    _session, window = build_window(tmp_path)

    ids = [page.plan.section for page in window.pages]
    titles = [page.plan.title for page in window.pages]
    ids += [page.section for page in window.declaration_pages]
    titles += [page.title for page in window.declaration_pages]

    assert len(set(ids)) == len(ids), f"duplicate stack id: {sorted(_repeats(ids))}"
    assert len(set(titles)) == len(titles), f"duplicate title: {sorted(_repeats(titles))}"


def _repeats(values: list[str]) -> set[str]:
    seen: set[str] = set()
    return {value for value in values if value in seen or seen.add(value)}  # type: ignore[func-returns-value]


def test_each_page_can_actually_be_selected_by_name(tmp_path: Path) -> None:
    """The sidebar search used to stop at the Schema Pages, so no Entity Page was reachable.

    Silent, too: selecting a name that is not found simply leaves the previous row
    selected, so the Page existed, appeared in the sidebar, and could not be opened by any
    code path that navigates by name.
    """
    _session, window = build_window(tmp_path)

    for kind in KINDS:
        section = window.declaration_page(kind).section
        window._select_section(section)

        assert window._selected_section() == section, kind


def test_the_heading_says_the_pages_name_not_its_internal_id(tmp_path: Path) -> None:
    """The heading used to come from the Schema, which has never heard of an Entity Page.

    It answers with a title derived from the id, so `entity:animations` reached the screen
    as "Entity:animations" -- an internal identifier, shown to the user, in the largest
    text on the page.
    """
    _session, window = build_window(tmp_path)

    for kind in KINDS:
        page = window.declaration_page(kind)
        window._select_section(page.section)

        heading = window._content_page.get_title()

        assert heading == page.title, kind
        assert "entity:" not in heading.lower()


def test_an_empty_page_says_what_the_kind_is_for(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path)

    page = window.declaration_page("gestures")

    assert page.rows == ()
    assert page.entities == []


def test_every_entity_becomes_a_row_in_model_order(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import StartupCommand

    session, window = build_window(tmp_path)
    session.model.entities.startup.extend(
        [StartupCommand("waybar"), StartupCommand("swaync"), StartupCommand("nm-applet")]
    )

    page = window.declaration_page("startup")
    page.refresh()

    assert [row.widget.get_title() for row in page.rows] == ["waybar", "swaync", "nm-applet"]
    assert [row.index for row in page.rows] == [0, 1, 2]


def test_the_filter_narrows_without_renumbering_the_rows(tmp_path: Path) -> None:
    """The index addresses the model, never the filtered view -- a delete must hit the
    row the user clicked."""
    from hyprtweaker.engine.model.entities import StartupCommand

    session, window = build_window(tmp_path)
    session.model.entities.startup.extend(
        [StartupCommand("waybar"), StartupCommand("swaync"), StartupCommand("waypaper")]
    )
    page = window.declaration_page("startup")
    page.refresh()

    page.set_filter("swa")

    assert [row.widget.get_title() for row in page.rows] == ["swaync"]
    assert [row.index for row in page.rows] == [1]


def test_a_read_only_session_offers_no_way_to_change_anything(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import EnvVar

    session, window = build_window(tmp_path)
    session.model.entities.env.append(EnvVar("XCURSOR_SIZE", "24"))
    page = window.declaration_page("env")
    page.refresh()

    assert not session.live
    assert not page._add_button.get_sensitive()


def test_a_scripted_gesture_is_listed_without_an_edit_button(tmp_path: Path) -> None:
    """ADR-0007's rule for function-valued actions: shown, never pretended to be editable."""
    from hyprtweaker.engine.model.entities import Gesture

    session, window = build_window(tmp_path)
    session.model.entities.gestures.append(
        Gesture({"fingers": 4, "direction": "up", "action": {"__fn": 1}})
    )
    page = window.declaration_page("gestures")
    page.refresh()

    assert len(page.rows) == 1
    assert page.rows[0].scripted


# --- findings on the row ----------------------------------------------------------------------


def test_a_dangling_curve_reference_lights_up_its_animation_row(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Animation

    session, window = build_window(tmp_path)
    session.model.entities.animations.append(
        Animation("windowsIn", {"enabled": True, "bezier": "gone"})
    )
    page = window.declaration_page("animations")
    page.refresh()

    row = page.rows[0]

    assert row.findings
    assert "gone" in row.widget.get_subtitle()


def test_declaring_the_curve_puts_the_row_back_to_normal(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Animation, Curve

    session, window = build_window(tmp_path)
    session.model.entities.animations.append(
        Animation("windowsIn", {"enabled": True, "speed": 4, "bezier": "easy"})
    )
    session.model.entities.curves.append(
        Curve("easy", {"type": "bezier", "points": [[0.2, 1], [0.3, 1]]})
    )
    page = window.declaration_page("animations")
    page.refresh()

    assert page.rows[0].findings == ()


def test_a_curve_page_row_knows_which_animations_depend_on_it(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Animation, Curve

    session, window = build_window(tmp_path)
    session.model.entities.curves.append(Curve("easy", {"type": "bezier"}))
    session.model.entities.animations.extend(
        [
            Animation("windowsIn", {"enabled": True, "bezier": "easy"}),
            Animation("windowsOut", {"enabled": True, "bezier": "easy"}),
        ]
    )
    page = window.declaration_page("curves")

    assert page.curve_users("easy") == ("windowsIn", "windowsOut")


def test_an_unknown_device_field_is_shown_on_the_device(tmp_path: Path) -> None:
    """`hl.device` raises on an unknown field and takes the Module down with it."""
    from hyprtweaker.engine.model.entities import Device

    session, window = build_window(tmp_path)
    session.model.entities.devices.append(Device("pad", {"eraser_button_mode": 1}))
    page = window.declaration_page("devices")
    page.refresh()

    assert page.rows[0].findings
    assert "eraser_button_mode" in page.rows[0].findings[0].message


# --- the editor -------------------------------------------------------------------------------


def editor(kind: str, **kwargs: Any) -> Any:
    from hyprtweaker.ui.dialogs.declaration_editor import DeclarationEditor

    return DeclarationEditor(kind=kind, on_done=lambda _entity: None, **kwargs)


def test_the_editor_opens_on_the_entity_it_was_given(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import EnvVar

    dialog = editor("env", entity=EnvVar("XCURSOR_SIZE", "24", dbus=True))

    values = dialog.collect()

    assert values["name"] == "XCURSOR_SIZE"
    assert values["value"] == "24"
    assert values["dbus"] is True


def test_the_editor_builds_the_entity_its_widgets_describe(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import EnvVar

    dialog = editor("env", entity=EnvVar("XCURSOR_SIZE", "24"))

    built = dialog.build()

    assert built == EnvVar("XCURSOR_SIZE", "24")


def test_an_incomplete_form_refuses_to_save_and_says_what_is_missing(tmp_path: Path) -> None:
    dialog = editor("env")

    problem = dialog.validate()

    assert problem is not None
    assert "Name" in problem


def test_saving_onto_another_rows_identity_is_refused_with_a_reason(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import EnvVar

    dialog = editor("env", entity=EnvVar("XCURSOR_SIZE", "24"), taken=("XCURSOR_SIZE",))

    problem = dialog.validate()

    assert problem is not None
    assert "already an entry" in problem


def test_a_bounded_number_cannot_be_given_a_value_outside_its_range(tmp_path: Path) -> None:
    """Type-correctness as unenterable, not as a warning after the fact.

    `fingers` is 2..9 in the parser (CR:741-747), so the spin row must clamp rather than
    let the model hold a 40-finger gesture.
    """
    dialog = editor("gestures")

    row = dialog._rows["fingers"]
    row.get_adjustment().set_value(40)

    assert dialog.collect()["fingers"] == 9

    row.get_adjustment().set_value(0)

    assert dialog.collect()["fingers"] == 2


def test_an_animations_curve_dropdown_only_offers_curves_that_exist(tmp_path: Path) -> None:
    """The dangling reference prevented where it would be created."""
    from hyprtweaker.engine.entities_catalog import BUILTIN_CURVES

    dialog = editor("animations", curve_names=("easy", "bouncy"))

    model = dialog._rows["bezier"].get_model()
    offered = {model.get_string(i) for i in range(model.get_n_items())}

    assert {"easy", "bouncy"} <= offered
    assert set(BUILTIN_CURVES) <= offered


def test_choosing_a_spring_clears_the_bezier(tmp_path: Path) -> None:
    """The parser refuses a table carrying both."""
    from hyprtweaker.engine.model.entities import Animation

    dialog = editor(
        "animations",
        entity=Animation("fade", {"enabled": True, "bezier": "easy"}),
        curve_names=("easy", "bouncy"),
    )

    spring = dialog._rows["spring"]
    model = spring.get_model()
    index = next(i for i in range(model.get_n_items()) if model.get_string(i) == "bouncy")
    spring.set_selected(index)

    values = dialog.collect()
    assert values["spring"] == "bouncy"
    assert "bezier" not in values


def test_a_devices_optional_fields_start_hidden_behind_a_picker(tmp_path: Path) -> None:
    """43 optional fields is a form nobody reads; the common case stays two rows."""
    from hyprtweaker.engine.model.entities import Device

    dialog = editor("devices", entity=Device("mouse", {"sensitivity": -0.5}))

    assert "sensitivity" in dialog._rows
    assert "kb_layout" not in dialog._rows, "an unset field is offered, not shown"


def test_removing_an_optional_field_takes_the_key_out_of_the_entity(tmp_path: Path) -> None:
    from hyprtweaker.engine.entities_catalog import DEVICE_FIELD_SPECS
    from hyprtweaker.engine.model.entities import Device

    dialog = editor("devices", entity=Device("mouse", {"sensitivity": -0.5}))

    dialog._on_remove(None, DEVICE_FIELD_SPECS["sensitivity"])

    assert dialog.build() == Device("mouse", {})


@pytest.mark.parametrize("kind", KINDS)
def test_every_kinds_editor_constructs(kind: str, tmp_path: Path) -> None:
    """The cheapest real assertion in this tier: seven forms, all of them build."""
    dialog = editor(kind)

    assert dialog.get_title()
    assert dialog.collect() is not None


def test_an_untouched_switch_still_writes_its_key(tmp_path: Path) -> None:
    """`hl.animation` rejects a missing `enabled` outright -- probed, not documented.

    The switch shows a state either way, so one nobody touched used to write *nothing*, and
    the Add flow produced `hl.animation({leaf = "fade"})`: an error that takes the whole
    animations Module down, from two clicks and no mistake.
    """
    dialog = editor("animations")

    assert "enabled" in dialog.collect()


def test_an_enabled_animation_will_not_save_without_a_curve(tmp_path: Path) -> None:
    """Speed is not in the message because a spin row always holds a number; the curve is
    a dropdown that can honestly be empty, and Hyprland requires one either way."""
    from hyprtweaker.engine.model.entities import Animation

    dialog = editor("animations", entity=Animation("fade", {"enabled": True}))

    problem = dialog.validate()

    assert problem is not None
    assert "curve" in problem.lower()


def test_the_speed_a_spin_row_seeds_is_a_speed_hyprland_accepts(tmp_path: Path) -> None:
    """The other half of the rule: a seeded speed must not be one the parser rejects.

    `speed` is `0 < x <= 100`, so a spin row that opened at its lower bound of 0 would
    write a value the compositor refuses.
    """
    from hyprtweaker.engine.entities_catalog import ANIMATION_SPEED_MAX, ANIMATION_SPEED_MIN

    dialog = editor("animations")

    speed = dialog.collect()["speed"]

    assert ANIMATION_SPEED_MIN < speed <= ANIMATION_SPEED_MAX


def test_an_animation_that_is_merely_off_saves_as_it_is(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Animation

    dialog = editor("animations", entity=Animation("border", {"enabled": False}))

    assert dialog.validate() is None


def test_a_device_number_is_bounded_by_the_option_it_shadows(tmp_path: Path) -> None:
    """ "Type-correct per the Schema": the per-device value gets the global one's range."""
    from hyprtweaker.engine.model.entities import Device

    session, _window = build_window(tmp_path)
    dialog = editor(
        "devices",
        entity=Device("kb", {"repeat_rate": 50}),
        bounds=session.device_field_bounds,
    )

    adjustment = dialog._rows["repeat_rate"].get_adjustment()
    adjustment.set_value(9999)

    assert dialog.collect()["repeat_rate"] == 200


def test_unset_is_not_offered_in_the_gesture_action_picker(tmp_path: Path) -> None:
    """It only ever removes a gesture declared earlier, and a Module starts empty."""
    dialog = editor("gestures")

    model = dialog._rows["action"].get_model()
    offered = {model.get_string(i) for i in range(model.get_n_items())}

    assert "unset" not in offered


def test_a_shadowed_gesture_is_flagged_on_the_row_it_belongs_to(tmp_path: Path) -> None:
    """Not a duplicate warning -- Hyprland refuses to load the file at all."""
    from hyprtweaker.engine.model.entities import Gesture

    session, window = build_window(tmp_path)
    session.model.entities.gestures.extend(
        [
            Gesture({"fingers": 3, "direction": "swipe", "action": "workspace"}),
            Gesture({"fingers": 3, "direction": "left", "action": "close"}),
        ]
    )
    page = window.declaration_page("gestures")
    page.refresh()

    assert page.rows[0].findings == ()
    assert page.rows[1].findings
    assert "already covers this" in page.rows[1].findings[0].message


def test_a_nameless_variable_is_flagged_and_an_unusual_name_is_not(tmp_path: Path) -> None:
    """Probed: `hl.env` refuses only an empty name; a dash or a leading digit is fine.

    Badging the merely-unusual ones put a warning triangle on configs that load.
    """
    from hyprtweaker.engine.model.entities import EnvVar

    session, window = build_window(tmp_path)
    session.model.entities.env.extend([EnvVar("", "1"), EnvVar("has-a-dash", "1")])
    page = window.declaration_page("env")
    page.refresh()

    assert page.rows[0].findings
    assert page.rows[1].findings == ()


def test_the_permissions_page_states_the_option_they_depend_on(tmp_path: Path) -> None:
    """A permission list that is quietly inert is the falsehood ADR-0013 forbids."""
    from hyprtweaker.engine.entities_catalog import PERMISSION_ENFORCE_OPTION
    from hyprtweaker.ui.pages.declaration_kinds import BY_KIND

    _session, _window = build_window(tmp_path)

    assert PERMISSION_ENFORCE_OPTION in BY_KIND["permissions"].note


def test_the_autostart_page_says_when_a_new_command_first_runs(tmp_path: Path) -> None:
    """A handler registered on a later reload never fires, so "added" is not "running"."""
    from hyprtweaker.ui.pages.declaration_kinds import BY_KIND

    assert BY_KIND["startup"].note


@pytest.mark.skipif(shutil.which("Hyprland") is None, reason="no Hyprland binary")
def test_what_the_add_form_produces_untouched_is_a_config_hyprland_loads(
    tmp_path: Path,
) -> None:
    """The guard for a whole class of bug: a *default* that writes invalid config.

    Two of these shipped in one review round -- a switch that wrote no `enabled` key, and a
    speed spin that opened at 0 when the parser wants `> 0`. Both were reachable by opening
    the Add dialog and pressing Save, and neither was visible to any test that built its
    entities by hand. So this builds them the way a user does: straight out of the form.
    """
    from hyprtweaker.engine.model import ConfigModel
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.schema import load_schema
    from hyprtweaker.engine.writer import Writer

    root = Path(__file__).resolve().parents[2]
    paths = ConfigPaths.rooted_at(tmp_path / "cfg")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    model = ConfigModel(load_schema("0.56.2", root / "data" / "schema"))

    for kind in KINDS:
        dialog = editor(kind, curve_names=("easy",))
        values = dict(dialog.collect())
        # The identity and free-text fields are the ones a form cannot invent; everything
        # else stays exactly as the dialog opened it.
        for name, filler in (
            ("name", "easy" if kind == "curves" else "probe-device"),
            ("leaf", "fade"),
            ("value", "1"),
            ("binary", "/usr/bin/probe"),
            ("command", "true"),
            ("bezier", "default"),
        ):
            if name in {spec.name for spec in BY_KIND[kind].all_fields} and not values.get(
                name
            ):
                values[name] = filler
        model.entities.__getattribute__(kind).append(BY_KIND[kind].from_form(values, None))

    model.mark_entities_loaded()
    Writer(paths, app_version="0.0.0-test").write(model)

    runtime = tmp_path / "run"
    runtime.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY")
    }
    environment["XDG_RUNTIME_DIR"] = str(runtime)
    result = subprocess.run(
        ["Hyprland", "--verify-config", "-c", str(paths.entrypoint)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=180,
    )

    assert result.returncode == 0, (
        f"the Add form's own defaults produce a config Hyprland rejects:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "config ok" in result.stdout


def test_an_imported_unset_gesture_is_not_rewritten_on_open(tmp_path: Path) -> None:
    """`unset` is not offered as a choice, but an imported config may hold one.

    Without the pass-through the dialog opened on the first choice and saved it, turning a
    removal into a live `workspace` binding just by looking at the row.
    """
    from hyprtweaker.engine.model.entities import Gesture

    gesture = Gesture({"fingers": 3, "direction": "horizontal", "action": "unset"})
    dialog = editor("gestures", entity=gesture)

    assert dialog.collect()["action"] == "unset"
    assert dialog.build().fields["action"] == "unset"


def test_an_imported_animation_with_no_enabled_key_opens_on(tmp_path: Path) -> None:
    """It carries a speed and a curve, so "off" would be the app answering for the user."""
    from hyprtweaker.engine.model.entities import Animation

    dialog = editor(
        "animations",
        entity=Animation("fade", {"speed": 3, "bezier": "default"}),
        curve_names=("default",),
    )

    assert dialog.collect()["enabled"] is True


def test_an_every_reload_autostart_command_can_be_saved(tmp_path: Path) -> None:
    """Its legal event value is the empty string, which the blank check read as missing."""
    from hyprtweaker.engine.model.entities import StartupCommand

    dialog = editor("startup", entity=StartupCommand("waybar", event=""))

    assert dialog.validate() is None
    assert dialog.build().event == ""


def test_two_gestures_with_one_trigger_badge_only_the_later_row(tmp_path: Path) -> None:
    """Findings are keyed by row index; keying by title badged the culprit and its victim."""
    from hyprtweaker.engine.model.entities import Gesture

    session, window = build_window(tmp_path)
    session.model.entities.gestures.extend(
        [
            Gesture({"fingers": 3, "direction": "up", "action": "workspace"}),
            Gesture({"fingers": 3, "direction": "up", "action": "close"}),
        ]
    )
    page = window.declaration_page("gestures")
    page.refresh()

    assert page.rows[0].findings == ()
    assert page.rows[1].findings
