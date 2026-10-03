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
from started_app import started_application

APP_VERSION = "0.0.0-test"

from hyprtweaker.ui.pages.declaration_kinds import BY_KIND  # noqa: E402

KINDS = ("animations", "curves", "gestures", "devices", "env", "startup", "permissions")


def build_window(tmp_path: Path, schema: Any = None) -> Any:
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
        schema=schema,
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = started_application()
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


def _every_page(window: Any) -> list[tuple[str, str, Any]]:
    """`(stack id, title, page widget)` for every Page the window built, Schema and Entity."""
    pages = [(page.plan.section, page.plan.title, page.page) for page in window.pages]
    entity_pages = [
        window.binds_page,
        window.window_rules_page,
        window.layer_rules_page,
        window.workspace_rules_page,
        window.monitors_page,
        *window.declaration_pages,
    ]
    pages += [(page.section, page.title, page.page) for page in entity_pages]
    return pages


def test_no_page_shares_a_stack_id_or_a_title_with_another(tmp_path: Path) -> None:
    """Hyprland has an `animations` Section *and* an animation tree; likewise `gestures`,
    and `binds` beside the Keybinds Page (#120).

    A stack child name used twice is not an error GTK raises -- it warns on stderr, keeps
    both children, and resolves the name to the first, so the second Page's sidebar row
    opens the other Page. That shipped past a green suite twice. Asserted in the Config
    view, the one with a Page per Section: the Tasks view names its Schema Pages
    differently and never collides. Two sidebar rows with the same title are the same
    defect one layer up: legible, and still a puzzle.
    """
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    pages = _every_page(window)
    ids = [section for section, _title, _page in pages]
    titles = [title for _section, title, _page in pages]

    assert len(set(ids)) == len(ids), f"duplicate stack id: {sorted(_repeats(ids))}"
    assert len(set(titles)) == len(titles), f"duplicate title: {sorted(_repeats(titles))}"


def test_every_sidebar_row_opens_its_own_page_in_the_config_view(tmp_path: Path) -> None:
    """Selecting a row shows the Page it names, not another Page filed under its id (#120)."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    for section, title, page in _every_page(window):
        window._select_section(section)
        assert window.visible_section == section
        assert page.is_ancestor(window._stack.get_visible_child()), (section, title)


def test_the_keybinds_page_and_the_binds_section_are_two_pages(tmp_path: Path) -> None:
    """The case #120 found: the Keybinds row opened "Keybind behaviour"."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    window._select_section("entity:binds")
    assert window._content_page.get_title() == "Keybinds"

    window._select_section("binds")
    assert window._content_page.get_title() == "Keybind behavior"


def test_building_the_config_view_warns_about_no_duplicate_stack_child(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """GTK's only report of a reused stack id is a line on stderr, so read stderr."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    assert "duplicate child name" not in capfd.readouterr().err


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


def test_a_command_with_an_ampersand_shows_as_typed(tmp_path: Path) -> None:
    """Row titles are Pango markup unless told otherwise: `a && b` would render blank."""
    from gi.repository import Gtk

    from hyprtweaker.engine.model.entities import StartupCommand

    session, window = build_window(tmp_path)
    session.model.entities.startup.append(StartupCommand("a && b"))
    page = window.declaration_page("startup")
    page.refresh()

    def shown(widget: Any) -> Any:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                yield child.get_text()
            yield from shown(child)
            child = child.get_next_sibling()

    assert "a && b" in list(shown(page.rows[0].widget))


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


def _tree_schema(*leaves: str) -> Any:
    from hyprtweaker.engine.schema import Schema

    return Schema("0.57.0", (), animation_leaves=leaves)


def test_a_leaf_the_sessions_schema_records_is_not_flagged_on_the_page(tmp_path: Path) -> None:
    """The Page asks the schema for the tree, not the list shipped with the app (#121)."""
    from hyprtweaker.engine.model.entities import Animation

    held = [
        Animation("brandNewLeaf", {"enabled": False}),
        Animation("windowsIn", {"enabled": False}),
    ]
    session, window = build_window(tmp_path, _tree_schema("brandNewLeaf"))
    session.model.entities.animations.extend(held)
    page = window.declaration_page("animations")
    page.refresh()

    assert [bool(row.findings) for row in page.rows] == [False, True]
    assert "no such leaf" in page.rows[1].findings[0].message


def test_the_editor_offers_the_leaves_it_is_handed_in_place_of_the_shipped_list() -> None:
    dialog = editor("animations", choices={"leaf": ("brandNewLeaf", "fade")})

    model = dialog._rows["leaf"].get_model()

    assert [model.get_string(i) for i in range(model.get_n_items())] == ["brandNewLeaf", "fade"]
    assert dialog.collect()["leaf"] == "brandNewLeaf"


def test_the_window_hands_the_editor_the_session_schemas_leaves(tmp_path: Path) -> None:
    """The tree's own leaves, in the tree's order: a leaf the app has no place for yet
    comes after the ones it knows."""
    _session, window = build_window(tmp_path, _tree_schema("brandNewLeaf", "fade"))

    dialog = window.declaration_editor("animations", on_done=lambda _entity: None)

    model = dialog._rows["leaf"].get_model()
    assert [model.get_string(i) for i in range(model.get_n_items())] == ["fade", "brandNewLeaf"]


def test_a_new_animation_opens_on_global_not_the_first_leaf_alphabetically(
    tmp_path: Path,
) -> None:
    """#150 review finding 9: the schema records its leaves alphabetically, so an untouched
    Save wrote `border`; the root of the tree is the one every other leaf inherits from."""
    _session, window = build_window(tmp_path)

    dialog = window.declaration_editor("animations", on_done=lambda _entity: None)

    assert dialog.collect()["leaf"] == "global"


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


def optional_tier_rows(dialog: Any) -> list[Any]:
    """Every preferences row under the optional group, in tree order.

    Adw rows parent to an internal list box, so the group's own children are no help;
    this walks the whole subtree the way a user sees it.
    """
    from gi.repository import Adw

    rows: list[Any] = []

    def walk(widget: Any) -> None:
        child = widget.get_first_child()
        while child is not None:
            if isinstance(child, Adw.PreferencesRow):
                rows.append(child)
            walk(child)
            child = child.get_next_sibling()

    walk(dialog._optional_group)
    return rows


def optional_tier_titles(dialog: Any) -> list[str]:
    return [row.get_title() for row in optional_tier_rows(dialog)]


def pick_from_add_row(dialog: Any, label: str) -> None:
    """Choose `label` in the "Add a setting" picker, as the user does."""
    (picker,) = [
        row for row in optional_tier_rows(dialog) if row.get_title() == "Add a setting"
    ]
    model = picker.get_model()
    labels = [model.get_string(i) for i in range(model.get_n_items())]
    picker.set_selected(labels.index(label))


def test_adding_and_removing_optional_fields_leaves_one_row_per_present_key(
    tmp_path: Path,
) -> None:
    from hyprtweaker.engine.entities_catalog import DEVICE_FIELD_SPECS
    from hyprtweaker.engine.model.entities import Device

    dialog = editor("devices", entity=Device("mouse", {"sensitivity": -0.5}))
    assert optional_tier_titles(dialog) == ["Sensitivity", "Add a setting"]

    # The add handler runs inside `notify::selected` of the picker row the rebuild then
    # removes, so drive it through the picker rather than calling the rebuild directly.
    pick_from_add_row(dialog, "Acceleration profile")
    assert optional_tier_titles(dialog) == [
        "Sensitivity",
        "Acceleration profile",
        "Add a setting",
    ]

    dialog._on_remove(None, DEVICE_FIELD_SPECS["sensitivity"])
    assert optional_tier_titles(dialog) == ["Acceleration profile", "Add a setting"]
    assert "sensitivity" not in dialog._rows

    pick_from_add_row(dialog, "Sensitivity")
    dialog._on_remove(None, DEVICE_FIELD_SPECS["accel_profile"])
    pick_from_add_row(dialog, "Acceleration profile")
    assert sorted(optional_tier_titles(dialog)) == [
        "Acceleration profile",
        "Add a setting",
        "Sensitivity",
    ]
    assert set(dialog.build().fields) == {"sensitivity", "accel_profile"}


def test_the_add_row_goes_once_every_optional_field_is_present(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import Device

    every = {spec.name: "" for spec in editor("devices")._descriptor.optional}
    dialog = editor("devices", entity=Device("mouse", every))

    titles = optional_tier_titles(dialog)
    assert "Add a setting" not in titles
    assert len(titles) == len(set(titles)) == len(every)


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


def test_a_read_only_session_greys_edit_and_remove_rather_than_hiding_them(
    tmp_path: Path,
) -> None:
    """F21 of the #148 review: Binds, Rules and Workspaces grey them; these hid them."""
    from hyprtweaker.engine.model.entities import EnvVar

    session, window = build_window(tmp_path)
    session.model.entities.env.append(EnvVar(name="XCURSOR_SIZE", value="24"))
    page = window.declaration_page("env")
    page.refresh()
    row = page.rows[0]

    assert row.edit_button is not None and not row.edit_button.get_sensitive()
    assert not row.remove_button.get_sensitive()
