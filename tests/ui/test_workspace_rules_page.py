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
