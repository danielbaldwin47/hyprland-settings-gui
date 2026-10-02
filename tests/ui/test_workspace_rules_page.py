"""UI smoke tier: the Workspaces Page and its selector editor (#159).

One row per workspace rule, identity the selector (ADR-0008). The editor here edits the
selector only; every field rides through a save untouched. Driven programmatically per
the repo's probe-before-screenshot rule: `window.workspace_rule_editor` builds the same
wired dialog the page's buttons present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"
SECTION = "entity:workspace_rules"


class StubApplier:
    """Stands where `_go_live` puts the real Applier, so the session accepts edits."""

    def __init__(self) -> None:
        self.serial = 0

    def commit_entities(self) -> int:
        self.serial += 1
        return self.serial


def build_window(tmp_path: Path, *, live: bool = False, rules: tuple[Any, ...] = ()) -> Any:
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
    if live:
        session._applier = StubApplier()
        session._offline_reason = None
    session.model.entities.workspace_rules.extend(rules)
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    return session, MainWindow(session, application=app)


def rule(workspace: str, **fields: Any) -> Any:
    from hyprtweaker.engine.model.entities import WorkspaceRule

    return WorkspaceRule(workspace=workspace, fields=fields)


def open_editor(window: Any, selector: str | None = None) -> Any:
    """The wired editor, presented over the window the way the page's buttons present it."""
    dialog = window.workspace_rule_editor(selector)
    dialog.present(window)
    closed: list[bool] = []
    dialog.connect("closed", lambda _dialog: closed.append(True))
    dialog.closed = closed
    return dialog


def titles(page: Any) -> list[tuple[str, str]]:
    return [(row.widget.get_title(), row.widget.get_subtitle()) for row in page.rows]


# --- the list ----------------------------------------------------------------------------


def test_each_rule_is_one_row_titled_by_its_selector_and_summarised_by_its_fields(
    tmp_path: Path,
) -> None:
    _session, window = build_window(
        tmp_path,
        rules=(
            rule("1", monitor="DP-1", default=True),
            rule("special:scratch", gaps_out=40, decorate=False),
            rule("w[tv1]&<b>"),
        ),
    )

    page = window.workspace_rules_page

    assert titles(page) == [
        ("1", "monitor DP-1, default"),
        ("special:scratch", "gaps_out 40, decorate off"),
        ("w[tv1]&<b>", "Nothing set yet"),
    ]
    # Selectors are user text: as Pango markup `&` and `<b>` would render blank or bold.
    assert not page.rows[2].widget.get_use_markup()


def test_an_empty_page_shows_one_sentence_and_the_add_action(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True)
    page = window.workspace_rules_page

    assert page.rows == ()
    empty = page.empty_row
    assert empty is not None
    assert empty.get_title() == "No workspace rules yet"
    assert (
        empty.get_subtitle() == "Add one to give a workspace its own layout, gaps or monitor."
    )
    assert page.empty_add_button.get_label() == "Add rule"
    assert page.empty_add_button.get_sensitive()


def test_a_page_with_rules_shows_no_empty_state(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True, rules=(rule("1"),))

    assert window.workspace_rules_page.empty_row is None


def test_a_read_only_session_lists_rules_but_offers_no_edit(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, rules=(rule("1"),))
    page = window.workspace_rules_page

    assert not page.add_button.get_sensitive()
    assert not page.rows[0].edit_button.get_sensitive()
    assert not page.rows[0].remove_button.get_sensitive()


def test_a_live_session_offers_add_edit_and_remove(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True, rules=(rule("1"),))
    page = window.workspace_rules_page

    assert page.add_button.get_sensitive()
    assert page.rows[0].edit_button.get_sensitive()
    assert page.rows[0].remove_button.get_sensitive()


# --- reachability ------------------------------------------------------------------------


def test_the_page_is_reachable_in_both_views_under_its_entity_id(tmp_path: Path) -> None:
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)

    for view in (View.TASKS, View.CONFIG):
        window.set_view(view)
        names = []
        index = 0
        while (row := window._sidebar.get_row_at_index(index)) is not None:
            names.append(row.get_name())
            index += 1
        assert SECTION in names, view
        assert window._stack.get_child_by_name(SECTION) is not None
    assert window.workspace_rules_page.title == "Workspaces"


# --- add, edit, remove -------------------------------------------------------------------


def test_adding_a_rule_lists_it_with_no_fields(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True)

    dialog = open_editor(window)
    dialog.selector_row.set_text(" 5 ")
    dialog.save()

    assert [(r.workspace, dict(r.fields)) for r in session.workspace_rules] == [("5", {})]
    assert titles(window.workspace_rules_page) == [("5", "Nothing set yet")]
    assert dialog.closed == [True]


def test_a_blank_selector_is_refused_in_the_dialog(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True)

    dialog = open_editor(window)
    dialog.selector_row.set_text("   ")
    dialog.save()

    assert session.workspace_rules == []
    assert dialog.closed == []
    assert dialog.error_label.get_visible()
    assert dialog.error_label.get_label() == "Type a workspace selector, such as 5 or name:web."


def test_editing_the_selector_keeps_every_field(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", monitor="DP-1", gaps_in=5),)
    )

    dialog = open_editor(window, "3")
    assert dialog.selector_row.get_text() == "3"
    dialog.selector_row.set_text("4")
    dialog.save()

    assert [(r.workspace, dict(r.fields)) for r in session.workspace_rules] == [
        ("4", {"monitor": "DP-1", "gaps_in": 5})
    ]
    assert titles(window.workspace_rules_page) == [("4", "monitor DP-1, gaps_in 5")]


def test_removing_a_rule_drops_its_row(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("1"), rule("2")))

    window.workspace_rules_page.rows[0].remove_button.emit("clicked")

    assert [r.workspace for r in session.workspace_rules] == ["2"]
    assert titles(window.workspace_rules_page) == [("2", "Nothing set yet")]


def test_an_unknown_field_survives_a_rename_and_a_siblings_removal_byte_for_byte(
    tmp_path: Path,
) -> None:
    """#133: unknown effects are preserved verbatim through the writer."""
    from hyprtweaker.engine.writer.monitors import render_workspace_rules_module

    keeper = rule(
        "3", frobnicate="x y", layout_opts={"orientation": "top"}, gaps_out={"top": 4}
    )
    session, window = build_window(tmp_path, live=True, rules=(rule("1"), keeper))
    before = render_workspace_rules_module(session.workspace_rules, app_version=APP_VERSION)
    assert before is not None
    line = next(text for text in before.splitlines() if "frobnicate" in text)
    assert line == (
        'hl.workspace_rule({ workspace = "3", frobnicate = "x y", '
        'layout_opts = { orientation = "top" }, gaps_out = { top = 4 } })'
    )

    dialog = open_editor(window, "3")
    dialog.selector_row.set_text("special:notes")
    dialog.save()
    window.workspace_rules_page.rows[0].remove_button.emit("clicked")

    after = render_workspace_rules_module(session.workspace_rules, app_version=APP_VERSION)
    assert after is not None
    rules = [text for text in after.splitlines() if text.startswith("hl.workspace_rule")]
    assert rules == [line.replace('workspace = "3"', 'workspace = "special:notes"')]


# --- one row per selector ----------------------------------------------------------------


def test_a_duplicate_selector_keeps_the_dialog_open_and_names_the_rule(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("5", monitor="DP-1"),))

    dialog = open_editor(window)
    dialog.selector_row.set_text("5")
    dialog.save()

    assert [r.workspace for r in session.workspace_rules] == ["5"]
    assert dialog.duplicate_row.get_visible()
    assert dialog.duplicate_row.get_title() == "A rule for “5” exists"
    assert dialog.show_button.get_label() == "Show it"
    assert not dialog.error_label.get_visible()
    assert dialog.closed == []

    # The message is about the text that was saved; editing it takes the message away.
    dialog.selector_row.set_text("6")
    assert not dialog.duplicate_row.get_visible()


def test_renaming_onto_another_rules_selector_is_a_duplicate_too(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("1"), rule("2")))

    dialog = open_editor(window, "2")
    dialog.selector_row.set_text("1")
    dialog.save()

    assert [r.workspace for r in session.workspace_rules] == ["1", "2"]
    assert dialog.duplicate_row.get_title() == "A rule for “1” exists"


def test_saving_a_rule_under_its_own_selector_is_not_a_duplicate(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("1", monitor="DP-1"),))

    dialog = open_editor(window, "1")
    dialog.save()

    assert not dialog.duplicate_row.get_visible()
    assert [r.workspace for r in session.workspace_rules] == ["1"]
    assert dialog.closed == [True]


def test_show_it_reveals_and_flashes_the_existing_row(tmp_path: Path) -> None:
    from hyprtweaker.ui.flash import FLASH_CLASS

    _session, window = build_window(tmp_path, live=True, rules=(rule("4"), rule("5")))

    dialog = open_editor(window)
    dialog.selector_row.set_text("5")
    dialog.save()
    dialog.show_button.emit("clicked")

    flashed = [
        row.rule.workspace
        for row in window.workspace_rules_page.rows
        if row.widget.has_css_class(FLASH_CLASS)
    ]
    assert flashed == ["5"]
    assert dialog.closed == [True]


def test_reveal_answers_whether_the_selector_has_a_row(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, rules=(rule("5"),))
    page = window.workspace_rules_page

    assert page.reveal("5") is True
    assert page.reveal("6") is False


def test_a_refused_save_keeps_the_dialog_open_and_says_why(tmp_path: Path) -> None:
    """The session went read-only while the dialog was open: not a duplicate, a refusal."""
    session, window = build_window(tmp_path, live=True, rules=(rule("1"),))

    dialog = open_editor(window)
    session._offline_reason = "Hyprland is not running."
    dialog.selector_row.set_text("2")
    dialog.save()

    assert [r.workspace for r in session.workspace_rules] == ["1"]
    assert not dialog.duplicate_row.get_visible()
    assert dialog.error_label.get_label() == "Can't save: Hyprland is not running."
    assert dialog.closed == []


def test_undoing_an_added_rule_takes_its_row_away(tmp_path: Path) -> None:
    """Entity undo (#189) refreshes the Page whose list it put back."""
    from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult

    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)
    dialog.selector_row.set_text("5")
    dialog.save()
    session._applied(ApplyResult(ApplyOutcome.OK, entities=session._applier.serial))

    window._undo()

    assert session.workspace_rules == []
    assert window.workspace_rules_page.rows == ()


# --- the editor's fields (#160) ----------------------------------------------------------


def choice(combo: Any) -> str:
    return str(combo.get_selected_item().get_string())


def offered(dialog: Any) -> list[str]:
    model = dialog.fields.picker.get_model()
    return [model.get_string(i) for i in range(model.get_n_items())][1:]


def pick(dialog: Any, title: str) -> None:
    """Choose `title` in the "Add a setting" row, as the user does."""
    dialog.fields.picker.set_selected(offered(dialog).index(title) + 1)


def stored(session: Any, selector: str) -> Any:
    return next(r for r in session.workspace_rules if r.workspace == selector)


def commit_gap(spin: Any, number: int) -> None:
    spin.set_value(number)
    spin.emit("activate")


def test_a_rules_fields_open_as_rows_of_their_own_type(tmp_path: Path) -> None:
    from hyprtweaker.engine.model.entities import WorkspaceRule

    held = WorkspaceRule(
        "3",
        {
            "monitor": "DP-1",
            "default": True,
            "border_size": 4,
            "layout": "master",
            "gaps_in": 6,
            "gaps_out": {"top": 1, "right": 2, "bottom": 3, "left": 4},
        },
    )
    _session, window = build_window(tmp_path, live=True, rules=(held,))

    fields = open_editor(window, "3").fields

    assert fields.text_entry("monitor").get_text() == "DP-1"
    assert fields.row("monitor").get_subtitle() == "An output name such as DP-1, or desc:…"
    assert fields.option_entry.get_title() == "Add a layout option"
    assert fields.row("default").get_active() is True
    assert fields.row("border_size").get_value() == 4
    assert choice(fields.row("layout")) == "master"
    assert (
        fields.gap_field("gaps_in").uniform,
        fields.gap_field("gaps_in").all_sides.get_value(),
    ) == (
        True,
        6,
    )
    sides = fields.gap_field("gaps_out")
    assert not sides.uniform
    assert [
        int(sides.sides[side].get_value()) for side in ("top", "right", "bottom", "left")
    ] == [
        1,
        2,
        3,
        4,
    ]
    assert fields.row("persistent") is None  # not held: offered, not shown
    assert "Keep when empty" in offered(open_editor(window, "3"))


def test_editing_one_field_leaves_every_other_value_as_the_same_object(tmp_path: Path) -> None:
    """#133 holds through field edits, not only a rename: unknown keys, `layout_opts` and a
    gap table with a missing side are the very objects the rule held."""
    from hyprtweaker.engine.model.entities import WorkspaceRule
    from hyprtweaker.engine.writer.monitors import render_workspace_rules_module

    sides = {"top": 4}
    opts = {"orientation": "top", "count": 3}
    held = WorkspaceRule(
        "3",
        {"monitor": "DP-1", "frobnicate": "x y", "layout_opts": opts, "gaps_out": sides},
    )
    session, window = build_window(tmp_path, live=True, rules=(held,))

    dialog = open_editor(window, "3")
    dialog.fields.text_entry("monitor").set_text("HDMI-A-1")
    dialog.save()

    fields = stored(session, "3").fields
    assert list(fields) == ["monitor", "frobnicate", "layout_opts", "gaps_out"]
    assert fields["monitor"] == "HDMI-A-1"
    assert fields["layout_opts"] is opts
    assert fields["gaps_out"] is sides
    assert fields["frobnicate"] == "x y"
    text = render_workspace_rules_module(session.workspace_rules, app_version=APP_VERSION)
    assert text is not None
    assert (
        'hl.workspace_rule({ workspace = "3", monitor = "HDMI-A-1", frobnicate = "x y", '
        'layout_opts = { orientation = "top", count = 3 }, gaps_out = { top = 4 } })'
    ) in text


def test_saving_without_touching_a_field_stores_the_rules_own_mapping(tmp_path: Path) -> None:
    held = rule("3", monitor="DP-1", border_size=2, gaps_in=5)
    _session, window = build_window(tmp_path, live=True, rules=(held,))

    dialog = open_editor(window, "3")

    assert dialog.collect_fields() is held.fields


def test_a_field_added_from_the_picker_is_written_and_leaves_the_picker(
    tmp_path: Path,
) -> None:
    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)
    dialog.value_row.set_text("7")

    assert "Keep when empty" in offered(dialog)
    pick(dialog, "Keep when empty")
    pick(dialog, "Gaps between windows")
    pick(dialog, "Layout")
    dialog.save()

    assert dict(stored(session, "7").fields) == {
        "persistent": True,
        "gaps_in": 0,
        "layout": "dwindle",
    }


def test_removing_a_field_row_drops_its_key_and_offers_it_again(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", monitor="DP-1", persistent=True),)
    )
    dialog = open_editor(window, "3")
    assert "Keep when empty" not in offered(dialog)

    remove = dialog.fields.row("persistent").get_last_child()
    dialog.fields.remove_row("persistent")
    dialog.save()

    assert dict(stored(session, "3").fields) == {"monitor": "DP-1"}
    assert "Keep when empty" in offered(dialog)
    assert remove is not None


def test_a_text_field_left_blank_writes_no_key(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)
    dialog.value_row.set_text("7")
    pick(dialog, "Monitor")

    dialog.save()

    assert dict(stored(session, "7").fields) == {}


def test_a_gap_commit_replaces_that_key_only(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", gaps_in=5, gaps_out=9),)
    )
    dialog = open_editor(window, "3")

    commit_gap(dialog.fields.gap_field("gaps_in").all_sides, 12)
    dialog.save()

    assert dict(stored(session, "3").fields) == {"gaps_in": 12, "gaps_out": 9}


def test_a_held_layout_the_list_lacks_joins_it_and_is_not_changed(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", layout="lua:columns", monitor="DP-1"),)
    )
    dialog = open_editor(window, "3")

    combo = dialog.fields.row("layout")

    assert choice(combo) == "lua:columns"
    assert [
        combo.get_model().get_string(i) for i in range(combo.get_model().get_n_items())
    ] == [
        "dwindle",
        "master",
        "scrolling",
        "monocle",
        "lua:columns",
    ]
    dialog.fields.text_entry("monitor").set_text("DP-2")
    dialog.save()
    assert stored(session, "3").fields["layout"] == "lua:columns"


def test_the_layout_choices_come_from_the_schema_without_the_lua_placeholder(
    tmp_path: Path,
) -> None:
    _session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)
    pick(dialog, "Layout")

    combo = dialog.fields.row("layout")

    assert [
        combo.get_model().get_string(i) for i in range(combo.get_model().get_n_items())
    ] == [
        "dwindle",
        "master",
        "scrolling",
        "monocle",
    ]


def test_a_value_a_typed_row_cannot_show_gets_a_raw_row_and_is_kept(tmp_path: Path) -> None:
    held = rule("3", gaps_in="5 10", border_size="thick", float_gaps=2)
    session, window = build_window(tmp_path, live=True, rules=(held,))
    dialog = open_editor(window, "3")

    assert dialog.fields.row("gaps_in").get_title() == "gaps_in"
    assert dialog.fields.row("gaps_in").get_text() == "5 10"
    dialog.fields.row("float_gaps")  # the typed row exists for the value it can show
    commit_gap(dialog.fields.gap_field("float_gaps").all_sides, 3)
    dialog.save()

    assert dict(stored(session, "3").fields) == {
        "gaps_in": "5 10",
        "border_size": "thick",
        "float_gaps": 3,
    }


def test_an_unknown_key_is_a_raw_row_that_keeps_its_type_when_edited(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", frobnicate=7, gapsout=40, note="a b"),)
    )
    dialog = open_editor(window, "3")

    dialog.fields.row("frobnicate").set_text("9")
    dialog.save()

    assert dict(stored(session, "3").fields) == {"frobnicate": 9, "gapsout": 40, "note": "a b"}
    assert type(stored(session, "3").fields["frobnicate"]) is int


def test_an_unknown_table_value_is_shown_read_only_and_kept(tmp_path: Path) -> None:
    table = {"a": 1}
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", exotic=table, monitor="DP-1"),)
    )
    dialog = open_editor(window, "3")

    assert not dialog.fields.row("exotic").get_editable()
    dialog.fields.text_entry("monitor").set_text("DP-2")
    dialog.save()

    assert stored(session, "3").fields["exotic"] is table


# --- layout_opts -------------------------------------------------------------------------


def test_layout_opts_rows_show_the_held_table(tmp_path: Path) -> None:
    held = rule("3", layout_opts={"orientation": "top", "count": 3, "on": True})
    _session, window = build_window(tmp_path, live=True, rules=(held,))

    fields = open_editor(window, "3").fields

    assert [
        (key, fields.option_row(key).get_text()) for key in ("orientation", "count", "on")
    ] == [("orientation", "top"), ("count", "3"), ("on", "true")]


def test_editing_one_option_keeps_the_others_as_held_and_this_one_as_typed(
    tmp_path: Path,
) -> None:
    session, window = build_window(
        tmp_path, live=True, rules=(rule("3", layout_opts={"orientation": "top", "count": 3}),)
    )
    dialog = open_editor(window, "3")

    dialog.fields.option_row("orientation").set_text("left")
    dialog.save()

    opts = stored(session, "3").fields["layout_opts"]
    assert opts == {"orientation": "left", "count": 3}
    assert type(opts["count"]) is int  # untouched: the object it was, not the text "3"


def test_an_option_is_added_by_name_and_empty_ones_are_not_written(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("3", monitor="DP-1"),))
    dialog = open_editor(window, "3")

    dialog.fields.option_entry.set_text("direction")
    dialog.fields.option_entry.emit("apply")
    dialog.fields.option_row("direction").set_text("right")
    dialog.fields.option_entry.set_text("blank")
    dialog.fields.option_entry.emit("apply")
    dialog.save()

    assert dialog.fields.option_entry.get_text() == ""
    assert dict(stored(session, "3").fields) == {
        "monitor": "DP-1",
        "layout_opts": {"direction": "right"},
    }


def test_removing_every_option_drops_the_table(tmp_path: Path) -> None:
    session, window = build_window(
        tmp_path,
        live=True,
        rules=(rule("3", monitor="DP-1", layout_opts={"orientation": "top"}),),
    )
    dialog = open_editor(window, "3")

    dialog.fields.remove_option("orientation")
    dialog.save()

    assert dict(stored(session, "3").fields) == {"monitor": "DP-1"}


# --- selector modes ----------------------------------------------------------------------


def modes(dialog: Any) -> tuple[bool, bool]:
    """(advanced switch on, raw entry visible)"""
    return dialog.mode_switch.get_active(), dialog.selector_row.get_visible()


def test_a_selector_the_pickers_can_say_opens_in_simple_mode(tmp_path: Path) -> None:
    _session, window = build_window(
        tmp_path, live=True, rules=(rule("3"), rule("name:web"), rule("special:scratch"))
    )

    number = open_editor(window, "3")
    name = open_editor(window, "name:web")
    special = open_editor(window, "special:scratch")

    assert modes(number) == (False, False)
    assert (choice(number.kind_row), number.value_row.get_text()) == ("Number", "3")
    assert (choice(name.kind_row), name.value_row.get_text()) == ("Name", "web")
    assert (choice(special.kind_row), special.value_row.get_text()) == (
        "Special workspace",
        "scratch",
    )
    assert name.value_row.get_title() == "Name"


def test_a_filter_selector_opens_in_advanced_mode_with_its_text(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True, rules=(rule("w[tv1]"),))

    dialog = open_editor(window, "w[tv1]")

    assert modes(dialog) == (True, True)
    assert dialog.selector_row.get_text() == "w[tv1]"
    assert not dialog.kind_row.get_visible()
    assert not dialog.notice_label.get_visible()


def test_the_pickers_compose_the_selector_that_is_saved(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)

    dialog.kind_row.set_selected(1)
    dialog.value_row.set_text("web")
    dialog.save()

    assert [r.workspace for r in session.workspace_rules] == ["name:web"]


def test_switching_to_advanced_carries_the_picked_selector_across(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True, rules=(rule("name:web"),))
    dialog = open_editor(window, "name:web")

    dialog.mode_switch.set_active(True)

    assert modes(dialog) == (True, True)
    assert dialog.selector_row.get_text() == "name:web"
    dialog.mode_switch.set_active(False)
    assert modes(dialog) == (False, False)
    assert dialog.value_row.get_text() == "web"


def test_a_filter_cannot_go_back_to_the_pickers_and_says_why(tmp_path: Path) -> None:
    _session, window = build_window(tmp_path, live=True, rules=(rule("w[tv1]"),))
    dialog = open_editor(window, "w[tv1]")

    dialog.mode_switch.set_active(False)

    assert modes(dialog) == (True, True)
    assert dialog.notice_label.get_label() == (
        "The pickers cannot say this selector, so it stays advanced."
    )


def test_an_edited_selector_hyprland_cannot_read_blocks_save(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True, rules=(rule("w[tv1]", monitor="DP-1"),))
    dialog = open_editor(window, "w[tv1]")

    dialog.selector_row.set_text("x[1]")
    dialog.save()

    assert dialog.error_label.get_label() == (
        "“x[” is not a workspace filter. Filters start with w, r, f, s, n or m."
    )
    assert dialog.closed == []
    assert [r.workspace for r in session.workspace_rules] == ["w[tv1]"]


def test_an_imported_selector_hyprland_may_not_read_never_blocks_save(tmp_path: Path) -> None:
    """CONTEXT.md "Finding": a rule the app declines to write cannot be fixed in the app."""
    session, window = build_window(tmp_path, live=True, rules=(rule("x[1]", monitor="DP-1"),))
    dialog = open_editor(window, "x[1]")

    assert modes(dialog) == (True, True)
    assert dialog.notice_label.get_label() == "Hyprland may not read this selector."
    dialog.fields.text_entry("monitor").set_text("DP-2")
    dialog.save()

    assert [(r.workspace, dict(r.fields)) for r in session.workspace_rules] == [
        ("x[1]", {"monitor": "DP-2"})
    ]
    assert dialog.closed == [True]


def test_a_selector_that_may_not_mean_what_it_says_warns_and_still_saves(
    tmp_path: Path,
) -> None:
    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)
    dialog.mode_switch.set_active(True)
    dialog.selector_row.set_text("Web")

    dialog.save()

    assert (
        dialog.notice_label.get_label()
        == "Hyprland reads “Web” as a name. Write name:Web to say so."
    )
    assert [r.workspace for r in session.workspace_rules] == ["Web"]


def test_a_number_picker_refuses_text(tmp_path: Path) -> None:
    session, window = build_window(tmp_path, live=True)
    dialog = open_editor(window)

    dialog.value_row.set_text("web")
    dialog.save()

    assert dialog.error_label.get_label() == (
        "A workspace number is digits only, such as 5. Pick Name for a name."
    )
    assert session.workspace_rules == []
