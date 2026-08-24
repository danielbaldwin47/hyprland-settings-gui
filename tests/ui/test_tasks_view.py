"""UI smoke tier: the curated Tasks view assembles, and the View choice outlives the window.

Deliberately shallow (spec #48). *Which* Options land on which curated Page is settled in
`tests/unit/test_ui_tasks_view.py`, where it needs no display; what is left for this tier is
whether GTK and libadwaita build the curated arrangement, and whether the two things that
can only be observed across a real window boundary actually hold: that switching Views
rebuilds the sidebar, and that a restart comes back where the user left off.

The toolkit imports sit inside the test functions on purpose -- module-scope ``gi`` turns a
machine without PyGObject into a collection error instead of the skip this tier wants.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

APP_VERSION = "0.0.0-test"


def build_window(tmp_path: Path) -> Any:
    """A window over a read-only session rooted in a throwaway directory.

    No `set_view` here: this file is about the arrangement the app opens in by default.
    """
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


def sidebar_ids(window: Any) -> list[str]:
    """Every navigable sidebar row, in order. Category headings have no name and drop out."""
    ids: list[str] = []
    index = 0
    while (row := window._sidebar.get_row_at_index(index)) is not None:
        name = row.get_name()
        # An unnamed row is a category heading: no Page behind it, not selectable.
        if name and row.get_selectable():
            ids.append(name)
        index += 1
    return ids


# --- the curated view assembles ---------------------------------------------------------------


def test_the_app_opens_in_the_tasks_view(tmp_path: Path) -> None:
    """Tasks is the default (#7): the curated arrangement is what the audience came for."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)

    assert window.view is View.TASKS
    assert window.categories


def test_every_curated_page_in_the_mapping_builds(tmp_path: Path) -> None:
    """AC 1 of #71, asked of a real toolkit rather than of a tuple."""
    from hyprtweaker.ui.pages.tasks import PageSpec, load_tasks_mapping

    _session, window = build_window(tmp_path)

    curated = [
        page.id
        for category in load_tasks_mapping().categories
        for page in category.pages
        if isinstance(page, PageSpec)
    ]
    built = [page.plan.section for page in window.pages]

    assert built == curated
    assert all(page.page.get_title() for page in window.pages)


def test_the_four_categories_appear_as_headings(tmp_path: Path) -> None:
    """That the categories *exist* is a headless question, settled in the unit tier.

    What is asked here is what only a real sidebar can answer: that each one is built as a
    row the pointer cannot land on. A selectable heading takes the selection and blanks the
    content pane, and no tuple assertion can see that.
    """
    from gi.repository import Gtk

    _session, window = build_window(tmp_path)

    headings = []
    index = 0
    while (row := window._sidebar.get_row_at_index(index)) is not None:
        child = row.get_child()
        if isinstance(child, Gtk.Label) and not row.get_selectable():
            headings.append(child.get_text())
        index += 1

    assert headings == ["Look", "Windows", "Input", "System"]


def test_every_entity_page_is_reachable_from_the_curated_sidebar(tmp_path: Path) -> None:
    """The curated view may rename and regroup, but it may not lose a destination (#7)."""
    _session, window = build_window(tmp_path)

    listed = set(sidebar_ids(window))
    entities = {page.section for page in window.declaration_pages}

    assert entities <= listed
    assert {"binds", "monitors", "window_rules", "layer_rules"} <= listed


def test_no_sidebar_row_points_at_a_page_that_was_not_built(tmp_path: Path) -> None:
    """A row with no Page behind it selects into an empty content pane."""
    _session, window = build_window(tmp_path)

    for section in sidebar_ids(window):
        assert window._stack.get_child_by_name(section) is not None


def test_a_curated_heading_with_an_ampersand_is_not_swallowed_by_pango(
    tmp_path: Path,
) -> None:
    """`Adw.PreferencesGroup` parses its title as markup, so a bare "&" renders as nothing.

    The failure is silent -- an empty heading and a `Gtk-WARNING` nobody reads -- and it is
    reachable from curated data alone, which is why it is pinned rather than left to whoever
    next writes an ampersand into `tasks.json`.

    Asserted against Pango rather than against `Adw.PreferencesGroup.get_title()`, because
    that getter is not the same window on every libadwaita: 1.9 hands back the escaped
    string it was given, while the version on CI hands back the *parsed* text. Both are
    consistent with a correct app, so an assertion about the getter tests the toolkit's
    accessor rather than our escaping. What actually has to hold is version-independent --
    the string we give libadwaita is markup that parses, and parses to the heading the
    curator wrote.
    """
    from gi.repository import Pango

    from hyprtweaker.ui.pages.config import escaped

    _session, window = build_window(tmp_path)

    # Reaching for a private helper is deliberate: the claim is about the exact string
    # handed to the toolkit, and no public surface reports that consistently across
    # libadwaita versions.
    curated = [
        group.title for page in window.pages for group in page.plan.groups if "&" in group.title
    ]

    assert curated, "no curated heading has an ampersand -- this test now guards nothing"

    for title in curated:
        _ok, _attrs, text, _accel = Pango.parse_markup(escaped(title), -1, "\0")
        assert text == title


# --- switching, and remembering --------------------------------------------------------------


def test_switching_to_config_rebuilds_the_sidebar_as_one_page_per_section(
    tmp_path: Path,
) -> None:
    from hyprtweaker.ui.pages.plan import View

    session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    assert [page.plan.section for page in window.pages] == list(session.schema.section_names)
    assert window.categories == ()


def test_switching_back_and_forth_leaves_the_curated_view_intact(tmp_path: Path) -> None:
    """A rebuild that leaked state would show up as pages accumulating or vanishing."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    before = [page.plan.section for page in window.pages]

    window.set_view(View.CONFIG)
    window.set_view(View.TASKS)

    assert [page.plan.section for page in window.pages] == before


def test_switching_views_leaves_the_sidebar_agreeing_with_the_content(
    tmp_path: Path,
) -> None:
    """The Views name their Pages differently, so a carried-over id selects nothing.

    Found by probing rather than by reasoning: from `look.decoration`, switching to Config
    left `get_selected_row()` empty while the stack had moved on to its first child -- a
    window with no selected row but a Page on screen, which reads as a broken sidebar.
    """
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window._select_section("look.decoration")
    assert window.visible_section == "look.decoration"

    window.set_view(View.CONFIG)

    assert window._sidebar.get_selected_row() is not None
    assert window._selected_section() == window.visible_section


def test_a_page_that_exists_in_both_views_survives_the_switch(tmp_path: Path) -> None:
    """An Entity Page is named the same in both arrangements, so it should be kept.

    Deliberately *not* `binds`: that id is ambiguous, because the `binds` Schema Section and
    the Keybinds Entity Page share it, and a test standing on an ambiguous id would go green
    on the very sidebar-versus-content disagreement this pins. The stack child is asserted
    for the same reason -- a selected row naming the right id is only half the claim.
    """
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window._select_section("entity:animations")

    window.set_view(View.CONFIG)

    assert window._selected_section() == "entity:animations"
    assert window.visible_section == "entity:animations"


def test_the_view_choice_survives_a_restart(tmp_path: Path) -> None:
    """AC 4 of #71, end to end: a second window over the same state dir opens in Config."""
    from hyprtweaker.ui.pages.plan import View

    _session, window = build_window(tmp_path)
    window.set_view(View.CONFIG)

    _again, reopened = build_window(tmp_path)

    assert reopened.view is View.CONFIG


def test_the_advanced_switch_is_remembered_too(tmp_path: Path) -> None:
    """ADR-0019 names it alongside the View choice, and #56 left it session-scoped."""
    from hyprtweaker.ui.shell.window import SHOW_ADVANCED_ACTION

    _session, window = build_window(tmp_path)
    # Through the action map rather than `activate_action`: widget-level activation walks a
    # muxer that an unrooted window has not got, and silently does nothing.
    window.lookup_action(SHOW_ADVANCED_ACTION).activate(None)
    assert window.show_advanced

    _again, reopened = build_window(tmp_path)

    assert reopened.show_advanced


def test_remembering_the_view_writes_only_to_the_state_dir(tmp_path: Path) -> None:
    """The second half of AC 4: the Hyprland config model is not touched by a preference.

    Prefs living beside the config would put app churn into the user's dotfile repo, which
    is exactly what ADR-0005 and ADR-0019 place in `$XDG_STATE_HOME` to avoid.
    """
    from hyprtweaker.ui.pages.plan import View

    session, window = build_window(tmp_path)
    before = sorted(path.name for path in (tmp_path / "hypr").rglob("*"))

    window.set_view(View.CONFIG)

    assert sorted(path.name for path in (tmp_path / "hypr").rglob("*")) == before
    assert (session.paths.state_dir / "prefs.json").is_file()


def test_an_unreadable_prefs_file_still_opens_a_window(tmp_path: Path) -> None:
    """A preference is never worth failing to start over (ADR-0019)."""
    from hyprtweaker.ui.pages.plan import View

    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "prefs.json").write_text("{ truncated", encoding="utf-8")

    _session, window = build_window(tmp_path)

    assert window.view is View.TASKS
