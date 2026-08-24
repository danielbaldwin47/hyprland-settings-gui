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

import pytest

APP_VERSION = "0.0.0-test"

HIDDEN_OPTION = "debug:manual_crash"
"""The `hidden` tier, which has no home in the Tasks view at any switch setting."""

ROUNDING_OPTION = "decoration:rounding"


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
    yield built
    built.destroy()


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
    from gi.repository import GLib

    context = GLib.MainContext.default()
    while context.pending():
        context.iteration(False)


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
