"""UI smoke tier: the finder opens, lists, and lands the user on the Row (ticket #72).

What a query *matches*, and in what order, is settled in `tests/unit/test_ui_search.py`
against a golden and no display. The questions left for this tier are the ones only a real
toolkit answers: does Ctrl+F reach the entry, do results replace the nav list, and does
activating a hit actually put the Row on screen -- including the two cases where the Row did
not exist a moment earlier, which is ADR-0017's One-off reveal and its View fallback.

Those last two are the reason this file is not merely a smoke test. A search that navigates
to Rows the Advanced switch is withholding is the whole feature for the `hidden` tier, and
"it built without complaint" would not have caught a reveal that lands on an empty Page.

**One window for the whole module**, reset between tests rather than rebuilt. A `MainWindow`
holds a Row per Option and costs hundreds of megabytes; `tests/ui` runs as a single pytest
process and already peaks around 4.4 GB, so a file that built nine of its own ended the
whole tier on the OOM killer -- surfacing, unhelpfully, as an unrelated test hanging near
the end of the run. The reset below goes through the same public affordances a user would.

The toolkit imports sit inside the test functions, so a machine without PyGObject skips this
tier rather than erroring during collection (`conftest.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import main_loop
import pytest

APP_VERSION = "0.0.0-test"

HIDDEN_OPTION = "debug:manual_crash"
"""The `hidden` tier, which has no home in the Tasks view at any switch setting."""

ROUNDING_OPTION = "decoration:rounding"
BINDS_OPTION = "binds:workspace_back_and_forth"

AT_STARTUP: dict[str, Any] = {}
"""What the shared window's index held the moment `MainWindow` returned, before any query."""


@pytest.fixture(scope="module")
def state_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The throwaway config root the shared window is rooted at."""
    return tmp_path_factory.mktemp("search")


@pytest.fixture(scope="module")
def window(state_dir: Path) -> Iterator[Any]:
    """One window over a read-only session, shared by every test in this module."""
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
        paths=ConfigPaths.rooted_at(state_dir),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    app = Adw.Application(application_id="io.github.danielbaldwin47.HyprtweakerTest")
    built = MainWindow(session, application=app)
    AT_STARTUP["entity entries"] = built._index.entity_count
    # Mapped, because one assertion below is about *mapping* and nothing else can stand in
    # for it (see `test_type_to_search_survives_the_title_swap`). Destroyed at teardown: a
    # window left mapped keeps its `GtkApplication` alive and the next module's `app.run()`
    # never returns.
    built.present()
    settle()
    yield built
    built.destroy()
    settle()


@pytest.fixture(autouse=True)
def _reset(window: Any) -> None:
    """Put the shared window back to a just-opened state, through public affordances only.

    Closing the finder clears the query and the results; selecting a sidebar Page by
    activation is what ends any outstanding One-off reveal -- the same route a user takes,
    so the reset cannot pass through a state the app itself never reaches.
    """
    from hyprtweaker.ui.pages.plan import View

    window.finder.close()
    window.set_view(View.CONFIG)
    window.sidebar.emit("row-activated", window.sidebar.get_row_at_index(0))
    settle()


def settle() -> None:
    """Run the pending main-loop turns.

    The reveal finishes in a low-priority idle on purpose -- a `GtkStack` allocates only its
    visible child, so scrolling into a Page that has not been laid out yet is a no-op
    (`MainWindow.reveal_option`). Without draining the loop this tier would assert against
    the state one turn *before* the thing it is testing happens.
    """
    main_loop.settle("the shared window's reveal and reset to finish")


# --- reaching the finder ------------------------------------------------------------------


def test_ctrl_f_focuses_the_finder(window: Any) -> None:
    """Ctrl+F is bound to the window's search action, and the action opens the entry."""
    assert window.get_application().get_accels_for_action("win.search") == ["<Control>f"]

    assert not window.search_mode
    window.activate_action("win.search", None)
    settle()

    assert window.search_mode
    assert window.sidebar_mode == "nav", "an open but empty finder still shows the nav list"

    # The focus lands on the entry's internal `GtkText`, not on the `GtkSearchEntry` itself,
    # so "is the entry focused?" has to be asked of the ancestry. Asking `entry.is_focus()`
    # reads False here even though the cursor is in it -- which is how a working shortcut
    # gets "fixed" into a broken one.
    focus = window.get_focus()
    assert focus is not None
    assert focus is window.finder.entry or focus.is_ancestor(window.finder.entry), (
        f"Ctrl+F left the focus on {type(focus).__name__}"
    )


def test_type_to_search_is_wired_to_the_window(window: Any) -> None:
    """The other half of ADR-0017's shortcuts, delegated to the toolkit.

    Asserted as the wiring rather than by synthesising a keystroke: `set_key_capture_widget`
    *is* the feature -- GTK already knows a keypress landing in a Row's entry or spin button
    is not the start of a search, and a hand-rolled handler would have to relearn that.
    """
    assert window.finder.bar.get_key_capture_widget() is window


def test_the_closed_search_bar_stays_mapped(window: Any) -> None:
    """The search bar must stay **mapped** while the finder is closed, not merely parented.

    This is the assertion the wiring test above cannot make, and the one that pins ADR-0017
    §Surface's amendment. GTK's capture handler opens with
    `if (!gtk_widget_get_mapped (bar)) return GDK_EVENT_PROPAGATE` (`gtksearchbar.c`), so any
    arrangement that hides the bar between searches -- a `GtkStack` page in the header title
    slot was the first attempt -- leaves `get_key_capture_widget()` pointing at the window
    while type-to-search does nothing at all. Every widget pointer still checks out; only the
    mapping tells you.
    """
    assert not window.search_mode
    assert window.finder.bar.get_mapped(), "the closed finder's bar is unmapped: dead shortcut"


# --- results replace the nav list ---------------------------------------------------------


def test_a_query_lists_results_and_clearing_restores_the_nav_list(window: Any) -> None:
    """ "Clearing or escaping restores the nav list" (ADR-0017)."""
    window.search("rounding")
    settle()

    assert window.sidebar_mode == "results"
    assert window.hits, "a query matching the shipped Schema listed nothing"
    assert all(hit.name for hit in window.hits)

    window.finder.close()
    settle()

    assert window.sidebar_mode == "nav"
    assert window.hits == ()


def test_a_query_matching_nothing_stays_in_results(window: Any) -> None:
    """An answered search must not look like a forgotten one."""
    window.search("zzzznosuchoption")
    settle()

    assert window.sidebar_mode == "results"
    assert window.hits == ()


# --- a hit navigates ----------------------------------------------------------------------


def test_a_hit_navigates_and_flashes(window: Any) -> None:
    """Activating a result opens the Row's Page and marks the Row (ADR-0017)."""
    from hyprtweaker.ui.flash import FLASH_CLASS

    window.search("rounding")
    settle()
    hit = next(hit for hit in window.hits if hit.name == ROUNDING_OPTION)

    window.open_hit(hit)
    settle()

    home = next(page for page in window.pages if page.row(ROUNDING_OPTION) is not None)
    row = home.row(ROUNDING_OPTION)

    assert window.visible_section == home.plan.section
    assert row.widget.has_css_class(FLASH_CLASS), "the revealed Row was not flash-highlighted"


def test_a_binds_option_hit_opens_the_binds_section_not_the_keybinds_page(
    window: Any,
) -> None:
    """`binds:*` Options live on the Section page, which once shared an id with Keybinds (#120).

    The Config view is the one where the two sit side by side, and the visible stack child
    is asserted rather than the id alone: a shared id selected the right name and showed
    whichever page GTK registered first.
    """
    window.search("workspace_back_and_forth")
    settle()
    window.open_hit(next(hit for hit in window.hits if hit.name == BINDS_OPTION))
    settle()

    home = next(page for page in window.pages if page.row(BINDS_OPTION) is not None)

    assert window.visible_section == "binds"
    assert home.page.is_ancestor(window._stack.get_visible_child())
    assert not window.binds_page.page.is_ancestor(window._stack.get_visible_child())


def test_hidden_tier_hit_switches_to_config_and_reveals(window: Any, state_dir: Path) -> None:
    """The One-off reveal and the View fallback, in the one case that needs both.

    `debug:manual_crash` is the `hidden` tier: ADR-0013 §5 keeps it off every curated Page at
    any switch setting, so a hit on it from the Tasks view has to switch the segment to
    Config -- and even there the Advanced switch is off, so the Row still does not exist
    until the reveal puts it there for this visit.
    """
    from hyprtweaker.engine.paths import ConfigPaths
    from hyprtweaker.engine.prefs import PrefsStore
    from hyprtweaker.ui.pages.plan import View

    window.set_view(View.TASKS)
    settle()
    assert not window.show_advanced

    window.search("manual_crash")
    settle()
    window.open_hit(next(hit for hit in window.hits if hit.name == HIDDEN_OPTION))
    settle()

    assert window.view is View.CONFIG, "a hit with no Tasks home must switch the View"
    assert window.revealed == frozenset({HIDDEN_OPTION})
    assert any(page.row(HIDDEN_OPTION) is not None for page in window.pages)
    assert not window.show_advanced, "the reveal must not flip the global switch"

    # ADR-0017: the fallback is "the ordinary View switch ... remembered like any manual
    # toggle", not a temporary hidden state -- so it has to reach the Prefs file.
    stored = PrefsStore(ConfigPaths.rooted_at(state_dir).state_dir).load()
    assert stored.view == View.CONFIG.value


def test_the_reveal_ends_when_the_user_navigates(window: Any) -> None:
    """ "One-off" means for this visit: the withheld Row goes back when the user moves on.

    Driven through the sidebar's `row-activated`, which is what a click or Enter emits --
    the reveal's own programmatic selection emits `row-selected` only, and must not undo
    the reveal it just made.
    """
    window.search("manual_crash")
    settle()
    window.open_hit(next(hit for hit in window.hits if hit.name == HIDDEN_OPTION))
    settle()
    assert any(page.row(HIDDEN_OPTION) is not None for page in window.pages)

    window.sidebar.emit("row-activated", window.sidebar.get_row_at_index(0))
    settle()

    assert window.revealed == frozenset()
    assert all(page.row(HIDDEN_OPTION) is None for page in window.pages)


# --- the Rules & entities group (#75) -------------------------------------------------------


def test_startup_builds_no_entity_entries() -> None:
    """Startup pays for the Schema only: the first query builds the Entity entries (S2b)."""
    assert AT_STARTUP == {"entity entries": None}


@pytest.fixture
def entities(window: Any) -> Iterator[dict[str, Any]]:
    """One entity of each kind, seeded into the shared window's model and Pages, then removed.

    Seeded the way `test_binds_page.py` seeds: into the model's lists, then each Page
    refreshed, as the window does after an edit. The profile goes through the Session,
    which a read-only session allows (a capture is App-dir JSON, not a config write).
    """
    from hyprtweaker.engine.model.entities import (
        Bind,
        DispatcherCall,
        LayerRule,
        MonitorRule,
        WindowRule,
    )

    session = window._session
    model = session.model.entities
    seeded = {
        "bind": Bind(
            keys="SUPER + Z",
            dispatcher=DispatcherCall(path="exec_cmd", args={"command": "zathura"}),
        ),
        "dead": Bind(
            keys="SUPER + zzdeadkey",
            dispatcher=DispatcherCall(path="exec_cmd", args={"command": "zzdead"}),
            enabled=False,
        ),
        "multi": Bind(
            keys="SUPER + Y&U",
            dispatcher=DispatcherCall(path="exec_cmd", args={"command": "zzmulti"}),
            enabled=False,
        ),
        "window_rule": WindowRule(
            name="Zz float zathura", match={"class": "zathura"}, effects={"float": True}
        ),
        "layer_rule": LayerRule(
            name="Zz blur zbar", match={"namespace": "zbar"}, effects={"blur": True}
        ),
        "monitor_rule": MonitorRule(output="ZZ-1", fields={"mode": "1920x1080@60"}),
    }
    model.binds.extend([seeded["bind"], seeded["dead"], seeded["multi"]])
    model.window_rules.append(seeded["window_rule"])
    model.layer_rules.append(seeded["layer_rule"])
    model.monitors.append(seeded["monitor_rule"])
    seeded["profile"] = session.save_monitor_profile("Zz studio")
    _refresh_entity_pages(window)
    yield seeded

    for name in ("binds", "window_rules", "layer_rules", "monitors"):
        getattr(model, name)[:] = [
            entity for entity in getattr(model, name) if entity not in seeded.values()
        ]
    session.delete_monitor_profile(seeded["profile"])
    _refresh_entity_pages(window)


def _refresh_entity_pages(window: Any) -> None:
    for page in (
        window.binds_page,
        window.window_rules_page,
        window.layer_rules_page,
        window.monitors_page,
    ):
        page.refresh()
    settle()


def test_results_group_settings_first_then_rules_and_entities(
    window: Any, entities: Any
) -> None:
    """ADR-0017's two groups, in order, each under its own heading."""
    from hyprtweaker.ui.search import EntityHit, OptionHit

    window.search("float")
    settle()
    kinds = [type(hit) for hit in window.hits]
    assert OptionHit in kinds and EntityHit in kinds
    assert kinds == sorted(kinds, key=[OptionHit, EntityHit].index)

    headings = [
        (index, row.get_header().get_label())
        for index in range(len(window.hits))
        if (row := window.finder.results.get_row_at_index(index)).get_header() is not None
    ]
    assert headings == [(0, "Settings"), (kinds.index(EntityHit), "Rules & entities")]


@pytest.mark.parametrize(
    ("query", "seed", "page_kind"),
    [
        ("super + z", "bind", "binds"),
        ("zz float", "window_rule", "window_rules"),
        ("zz blur", "layer_rule", "layer_rules"),
        ("zz-1", "monitor_rule", "monitors"),
        ("zz studio", "profile", "monitors"),
    ],
)
def test_an_entity_hit_opens_its_page_and_flashes_its_row(
    window: Any, entities: Any, query: str, seed: str, page_kind: str
) -> None:
    from hyprtweaker.ui.flash import FLASH_CLASS
    from hyprtweaker.ui.pages.tasks import entity_page_id

    window.search(query)
    settle()
    hit = next(hit for hit in window.hits if getattr(hit, "target", None) == entities[seed])

    window.open_hit(hit)
    settle()

    assert window.visible_section == entity_page_id(page_kind)
    flashed = _flashed_rows(window, FLASH_CLASS)
    assert len(flashed) == 1, f"expected one flashed row, found {len(flashed)}"
    if seed == "profile":
        assert flashed[0] in window.monitors_page.profile_rows, "not in the Profiles group"


def _flashed_rows(window: Any, css_class: str) -> list[Any]:
    """Every row on the visible Page wearing the flash, found by walking its widgets."""
    found = []
    stack = [window._stack.get_visible_child()]
    while stack:
        widget = stack.pop()
        if widget.has_css_class(css_class):
            found.append(widget)
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


@pytest.mark.parametrize("view_name", ["CONFIG", "TASKS"])
def test_a_bind_hit_opens_entity_binds_in_both_views(
    window: Any, entities: Any, view_name: str
) -> None:
    """The Binds Page id is `entity:binds` (#120), built by `entity_page_id` (#200)."""
    from hyprtweaker.ui.pages.plan import View

    window.set_view(View[view_name])
    settle()
    window.search("super + z")
    settle()
    window.open_hit(
        next(hit for hit in window.hits if getattr(hit, "target", None) == entities["bind"])
    )
    settle()

    assert window.view is View[view_name], "an Entity Page is in both Views: no fallback"
    assert window.visible_section == "entity:binds"
    assert window.binds_page.page.is_ancestor(window._stack.get_visible_child())


@pytest.mark.parametrize("seed", ["dead", "multi"])
def test_a_bind_hit_reads_its_rows_badge(window: Any, entities: Any, seed: str) -> None:
    """A dead-keysym or multi-key bind is described in the words its row's badge uses."""
    bind = entities[seed]
    window.search(bind.keys)
    settle()
    hit = next(hit for hit in window.hits if getattr(hit, "target", None) == bind)
    row = next(row for row in window.binds_page.rows if row.bind == bind)

    assert hit.badge == row.badge_label.get_label()
    assert (
        hit.badge
        in window.finder.results.get_row_at_index(window.hits.index(hit)).get_subtitle()
    )


def test_a_rule_hit_clears_the_filter_that_hid_it(window: Any, entities: Any) -> None:
    """A filter that hides the rule would make the hit land on "No rules match"."""
    from hyprtweaker.ui.flash import FLASH_CLASS

    page = window.window_rules_page
    page.set_filter("nothing matches this")
    assert page.rows == ()

    window.search("zz float")
    settle()
    window.open_hit(
        next(
            hit
            for hit in window.hits
            if getattr(hit, "target", None) == entities["window_rule"]
        )
    )
    settle()

    assert page.filter_entry.get_text() == ""
    assert [
        row.widget.has_css_class(FLASH_CLASS)
        for row in page.rows
        if row.rule == entities["window_rule"]
    ] == [True]


def test_a_hit_for_a_removed_entity_refreshes_the_results(window: Any, entities: Any) -> None:
    """Never a dead end, never a crash: the list updates and the Page stays where it was."""
    window.search("super + z")
    settle()
    hit = next(hit for hit in window.hits if getattr(hit, "target", None) == entities["bind"])
    before = window.visible_section

    window._session.model.entities.binds.remove(entities["bind"])
    window.open_hit(hit)
    settle()

    assert window.visible_section == before
    assert hit not in window.hits


def test_sync_reruns_the_open_query(window: Any, entities: Any) -> None:
    """An edit landing while results show reaches them on the window's next `sync`."""
    from hyprtweaker.engine.model.entities import Bind, DispatcherCall

    window.search("zzlate")
    settle()
    assert window.hits == ()

    late = Bind(
        keys="SUPER + X", dispatcher=DispatcherCall(path="exec_cmd", args={"command": "zzlate"})
    )
    window._session.model.entities.binds.append(late)
    try:
        window.sync()
        assert [hit.title for hit in window.hits] == ["SUPER + X"]
    finally:
        window._session.model.entities.binds.remove(late)
