"""UI smoke tier: the seven declarative Pages assemble and their editor collects (#70).

Row text is settled headless in `tests/unit/test_ui_declaration_text.py`; what is left here
is whether GTK builds the lists, whether the filter narrows them, whether a finding reaches
the row it belongs to, and whether the editor's collect/validate path produces the entity
the widgets describe -- all driven by probing widget state rather than by screenshots, per
the repo's probe-before-screenshot rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

APP_VERSION = "0.0.0-test"

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
        Animation("windowsIn", {"enabled": True, "bezier": "easy"})
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
