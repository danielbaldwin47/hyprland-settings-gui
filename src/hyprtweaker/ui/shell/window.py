"""The main window: sidebar, Config view, and the one place the session's state shows.

`Adw.NavigationSplitView` with a Section list on the left and one generated Page per Section
on the right -- the Config view of ADR-0013 and design artboard `Main`. The Tasks view, its
switcher, and the remembered choice between them are #71; until then the sidebar is the
Config view alone, and there is nothing to switch.

Every Page is built at startup rather than on first visit. Two reasons, and the second is
the load-bearing one: switching Section stays instant, and "every Section builds a Page from
the shipped Schema" becomes a fact about the running app instead of a claim about code that
may never have run (the UI smoke tier asserts exactly this).

Errors get a Banner, not a Row badge (ADR-0016). There is exactly one, it covers every
unhealthy state there is -- no compositor, config errors, an Entrypoint refusal, an active
Quarantine -- and *which* of those it says is `Session.health`'s judgement rather than this
window's. Its button opens the one error dialog, whose per-file buttons come from the
recovery matrix; this window only wires them to the session methods that perform them.

Toasts carry the two things instant apply cannot say by simply happening: that the last
gesture can be taken back, and that one was taken back for you. The first is the undo toast,
one per finished gesture rather than one per edit -- the Apply queue has already coalesced a
drag or a keystroke burst into a single transaction, so "one toast per transaction" *is* one
per gesture, and the previous toast is dismissed rather than queued behind it. The second is
auto-revert (ADR-0016), which is the only event the ADR reserves a toast for outright.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from hyprtweaker.engine.apply import (  # noqa: E402
    Action,
    ApplyOutcome,
    ApplyResult,
    EntityStep,
    PresetStep,
    Problem,
    Step,
    UndoGroup,
)
from hyprtweaker.engine.apply import plan as recovery_plan  # noqa: E402
from hyprtweaker.engine.binds_analysis import submap_names  # noqa: E402
from hyprtweaker.engine.importer.loss import LossReport  # noqa: E402
from hyprtweaker.engine.ipc import CommandClient, NoInstance  # noqa: E402
from hyprtweaker.engine.migration.detect import ConfigKind, Detection, detect  # noqa: E402
from hyprtweaker.engine.migration.export import render as export_render  # noqa: E402
from hyprtweaker.engine.migration.flow import (  # noqa: E402
    Decision,
    MigrationFlow,
    asks_consent,
    fresh_start,
    marker_rescue_command,
)
from hyprtweaker.engine.migration.sentinel import Sentinel  # noqa: E402
from hyprtweaker.engine.migration.sentinel import read as sentinel_read  # noqa: E402
from hyprtweaker.engine.model.entities import (  # noqa: E402
    DISPLAY_KINDS,
    KEYBIND_KINDS,
    Bind,
    LayerRule,
    PluginLoad,
    WindowRule,
    WorkspaceRule,
    entity_title,
)
from hyprtweaker.engine.monitors_catalog import breaks_display, revert_breaking  # noqa: E402
from hyprtweaker.engine.prefs import Prefs, PrefsStore  # noqa: E402
from hyprtweaker.engine.presets import (  # noqa: E402
    ColorChoice,
    PresetApplied,
    PresetApplyResult,
    PresetColorConflict,
    PresetNotApplied,
)
from hyprtweaker.engine.profiles import MonitorStateSnapshot  # noqa: E402
from hyprtweaker.engine.schema import ResolvedOption, Schema  # noqa: E402
from hyprtweaker.engine.scripting import LAYOUT_OPTION, discovered_layouts  # noqa: E402
from hyprtweaker.engine.triggers import parse_trigger  # noqa: E402
from hyprtweaker.engine.workspace_catalog import layout_choices  # noqa: E402
from hyprtweaker.session import (  # noqa: E402
    HELD_ENTRY_MOVED,
    AutoRevert,
    Notice,
    Replaced,
    Session,
)
from hyprtweaker.ui.dialogs.bind_editor import BindEditor  # noqa: E402
from hyprtweaker.ui.dialogs.capture import CaptureDialog, FetchSwitches  # noqa: E402
from hyprtweaker.ui.dialogs.colour_conflict import (  # noqa: E402
    COLOR_CONFLICT_DIALOG,
    ColourConflictDialog,
    remembered_choice,
)
from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog  # noqa: E402
from hyprtweaker.ui.dialogs.declaration_editor import (  # noqa: E402
    DeclarationEditor,
    taken_identities,
)
from hyprtweaker.ui.dialogs.errors import error_dialog  # noqa: E402
from hyprtweaker.ui.dialogs.migration import (  # noqa: E402
    MigrationDialog,
    export_dialog,
    import_dialog,
    import_report_dialog,
    migration_dialog,
)
from hyprtweaker.ui.dialogs.notices import notice_dialog, notice_title  # noqa: E402
from hyprtweaker.ui.dialogs.rule_editor import RuleEditor  # noqa: E402
from hyprtweaker.ui.dialogs.submap_editor import SubmapEditor  # noqa: E402
from hyprtweaker.ui.dialogs.workspace_rule_editor import WorkspaceRuleEditor  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.binds import BindActions, BindsPage  # noqa: E402
from hyprtweaker.ui.pages.config import ConfigPage  # noqa: E402
from hyprtweaker.ui.pages.declaration_kinds import BY_KIND  # noqa: E402
from hyprtweaker.ui.pages.declarations import (  # noqa: E402
    PAGES as DECLARATION_PAGES,
)
from hyprtweaker.ui.pages.declarations import (  # noqa: E402
    DeclarationActions,
    DeclarationsPage,
)
from hyprtweaker.ui.pages.monitors import (  # noqa: E402
    MonitorActions,
    MonitorsPage,
    ProfileActions,
)
from hyprtweaker.ui.pages.plan import (  # noqa: E402
    Disclosure,
    PagePlan,
    View,
    is_visible,
    plan_config_view,
)
from hyprtweaker.ui.pages.rules import (  # noqa: E402
    LayerRulesPage,
    RuleActions,
    RulesPage,
    WindowRulesPage,
)
from hyprtweaker.ui.pages.scripting import (  # noqa: E402
    PluginActions,
    ScriptingActions,
    ScriptingPage,
)
from hyprtweaker.ui.pages.tasks import (  # noqa: E402
    ORPHAN_CATEGORY_TITLE,
    CategoryPlan,
    TasksMapping,
    entity_page_id,
    load_tasks_mapping,
    plan_tasks_view,
)
from hyprtweaker.ui.pages.theming import (  # noqa: E402
    ThemingActions,
    ThemingMemory,
    ThemingPage,
)
from hyprtweaker.ui.pages.theming_presets import PresetActions  # noqa: E402
from hyprtweaker.ui.pages.workspace_rules import (  # noqa: E402
    WorkspaceRuleActions,
    WorkspaceRulesPage,
)
from hyprtweaker.ui.release import release, release_when_unparented  # noqa: E402
from hyprtweaker.ui.rows.factory import OptionRow, RowFactory  # noqa: E402
from hyprtweaker.ui.search import (  # noqa: E402
    EntityHit,
    EntityKind,
    Hit,
    OptionHit,
    SearchIndex,
    resolve,
)
from hyprtweaker.ui.shell.finder import NAV_MODE, RESULTS_MODE, Finder  # noqa: E402

_log = logging.getLogger(__name__)

ENTITY_CHANGED = "That item changed. Results updated."
"""The toast for a search hit whose entity was removed or rewritten since it was listed."""

IMPORT_ACTION = "import-config"
IMPORT_LABEL = "Import..."
EXPORT_ACTION = "export-config"
REPORT_ACTION = "import-report"

READ_ONLY_REASON = {
    ConfigKind.LEGACY_CONF: "You are still on hyprland.conf, so settings can't be saved yet.",
    ConfigKind.FOREIGN_LUA: (
        "Your hyprland.lua was not written here, so settings can't be saved until it is "
        "imported."
    ),
}
"""Why the app is read-only, in the user's terms (ADR-0009).

Shown, dismissible, and not repeated: "no nagging beyond that". The app is still worth
opening on an unmigrated box, which is why the pages render at all.
"""

CONVERT_SENTENCE = (
    "Your config has not been converted yet: use Convert... at the top of the window."
)
"""What a dialog or a disabled control says while an import is on offer (F20 of the #148
review): "not connected to Hyprland" would send the user looking for the wrong problem."""


def _discard(coro: Any) -> None:
    """Throw away a coroutine nobody can run. See `MainWindow._spawn`."""
    close = getattr(coro, "close", None)
    if close is not None:
        close()


def _release_dialogs_on_close(window: Adw.ApplicationWindow, _pspec: Any) -> None:
    """Release each dialog presented on `window` once it has closed and left the window.

    Once per dialog: a dialog becomes visible again each time one it opened (Capture over
    the bind editor) closes. The mark lives on the wrapper, which PyGObject then keeps for
    as long as the dialog lives, so it cannot be forgotten and hooked twice.
    """
    dialog = window.get_visible_dialog()
    if dialog is not None and not getattr(dialog, "_release_on_close", False):
        dialog._release_on_close = True
        dialog.connect("closed", _release_once_out)


def _release_once_out(dialog: Adw.Dialog) -> None:
    """Release a closed dialog when it is out of the window (#228).

    libadwaita emits `closed` as the dialog starts to animate out and takes it out of the
    window when the animation ends; `release` refuses a widget still in a window.
    """
    release_when_unparented(dialog)


UNDO_ACTION = "undo"
UNDO_ACCELERATOR = "<Control>z"
"""Ctrl+Z, on the window rather than on the focused control (ADR-0010 §Undo).

The stack is *global and linear*: one gesture at a time, wherever on whichever Page it
happened. A per-widget undo would put a spin button's own text-entry history in front of the
gesture the user actually means to take back, and would go silent the moment focus left the
Row they last changed.

Wired twice on purpose -- a `GtkShortcutController` on the window, and the application's
accelerator when there is an application. The controller is what makes the keystroke work at
all (and what the UI tier can drive); the accelerator is what makes the menu item show
"Ctrl+Z" beside itself."""

SEARCH_ACTION = "search"
SEARCH_ACCELERATOR = "<Control>f"
"""Ctrl+F, the GNOME HIG's find shortcut, on the window (ADR-0017).

Deliberately not Ctrl+K: a palette overlay has no libadwaita precedent, and the sidebar is
already this app's navigation surface. Type-to-search is the other half and costs nothing
here -- `Gtk.SearchBar.set_key_capture_widget` is exactly the "typing while focus is not in
a text entry starts a search" rule, implemented by the toolkit rather than by a key handler
of ours that would have to know which widgets count as text entries."""

SIDEBAR_TITLE = "Hyprland"
"""What the sidebar header says when the finder is closed (ADR-0017 swaps it for the entry)."""

NOT_SAVED_SENTENCE = "This change was not saved."
"""An editor's line for a refusal that said nothing of its own (a read-only session)."""

SEVERE_BANNER_CLASS = "error"
"""libadwaita's own red styling, for ADR-0016's "Red Banner".

A style class rather than a colour, so it follows the user's theme and their accent choice
-- a hard-coded red is the one thing that would look wrong in every theme but the one it was
picked in."""

BREAKING_DEBOUNCE_MS = 400
"""How long after the last display-breaking edit the batch applies (ADR-0008 "batch", #192).

Long enough that dragging a display on the canvas or stepping a scale spinner lands as one
apply and one countdown, short enough that the change still reads as the click's answer."""

DISPLAY_CHANGED = entity_title("monitors", "changed")
"""A display countdown's undo step, kept or with what survived its Revert: one title for both,
in the Displays Page's word (review of #151, finding 19)."""

UNDO_TOAST_SECONDS = 4
"""Long enough to notice and reach, short enough not to sit over the Row that just changed."""

NOTICE_TOAST_SECONDS = 8
"""ADR-0012's Info notices: as long as the auto-revert toast, which also offers Details.

A timeout rather than a toast that waits for the user: every toast queues behind the one on
screen, and a notice nobody closed would hold back the next undo offer indefinitely."""

PRESET_NOTE_SECONDS = 6
"""What a Preset could not do (a wallpaper left as it was): long enough to read a sentence."""

SHOW_ADVANCED_ACTION = "show-advanced"
"""One global switch, in the primary menu -- never per-Page (ADR-0013 §5).

The filter itself lives in `plan.py`, which also carries the tier rule the switch cannot
express on its own: `hidden` (`debug`, `quirks`, `experimental`, `input-capture`) is
Config-view-only, so flipping this on can never put "Crash Hyprland" on a curated Tasks
Page. A revealed Row wears an "Advanced" pill so it is legible as one (`rows/chrome.py`).

Search's one-off reveal -- reaching a withheld Row without flipping this at all -- is
ADR-0017's and arrives with #67.

Remembered in the Prefs file alongside the View choice (#71, ADR-0019), so the switch a
power user leaves on is still on tomorrow."""

VIEW_ACTION = "view"
"""Which sidebar arrangement is showing: the segmented control above the sidebar list.

A window action rather than a widget's own state because #7 puts the same choice in two
places -- the control and the primary menu -- and ADR-0017 adds a third caller, a search hit
whose Row has no home in the active View. One action means those three cannot disagree, and
a search-driven switch is remembered exactly like a manual one, which is what the ADR asks
for ("one mechanism, no temporary hidden state")."""

THEME_ACTION = "theme"
"""The Theme override: System, Light or Dark, three radio items in the primary menu.

This app's own colour scheme, set on its `Adw.StyleManager` and nowhere else: the desktop's
GTK settings and portal are never written, and never read to decide anything here. Hyprland
boxes often run without the portal that makes "follow the system" reliable (ADR-0019), so
the user can force the ground they can read. Remembered in the Prefs file."""

_SCHEMES = {
    "system": Adw.ColorScheme.DEFAULT,
    "light": Adw.ColorScheme.FORCE_LIGHT,
    "dark": Adw.ColorScheme.FORCE_DARK,
}
"""Each Theme override name, in menu order, and the colour scheme it asks for."""

FORGET_REMEMBERED_ACTION = "forget-remembered"
"""Clear every "remember my choice" answer (ADR-0014), so each such dialog asks again.

Without it, a remembered answer is a one-way door (UX critique 4, #79). Hidden while
nothing is remembered: the action is disabled then, and the menu item hides with it
(`hidden-when`), since a greyed item cannot say why it is grey."""


class MainWindow(Adw.ApplicationWindow):
    """The Config view over one `Session`."""

    _refused_sentence: str | None = None
    """The last refusal, as an editor shows it (`_saved_or_why`)."""

    def __init__(
        self,
        session: Session,
        *,
        spawn: Callable[[Any], None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)

        self._session = session
        self._spawn = spawn or _discard
        """How the wizard runs its coroutines (the switch, the rollback countdown).

        Defaults to discarding them, which is what a bare window -- the UI smoke tier builds
        one -- can honestly offer: the migration's live half needs the asyncio/GLib bridge
        the application owns, and pretending otherwise would leave a switch half-done."""
        self._factory = RowFactory(
            session,
            on_edited=self._on_option_edited,
            navigate=self.reveal_option,
            reveal_backend=self.reveal_backend,
        )
        self._prefs_store = PrefsStore(session.paths.state_dir)
        self._prefs = self._prefs_store.load()
        """Preferences as last stored, read once. Never the Hyprland config model (ADR-0019).

        Read at construction rather than per-use so a `$XDG_STATE_HOME` that disappears
        mid-session cannot change the view out from under the user."""
        # Before the first present: the window's first frame is already the chosen ground.
        Adw.StyleManager.get_default().set_color_scheme(
            _SCHEMES[_theme_from(self._prefs.theme)]
        )
        self._view = _view_from(self._prefs.view)
        """The active sidebar arrangement, and the source of truth for it.

        Held here rather than read off the switcher's buttons: three callers can change it
        (the control, the menu, and a search hit landing outside the active View), and a
        widget that is both the state and one of the callers is how a toggle handler ends
        up rebuilding the window twice per click."""
        self._mapping: TasksMapping | None = None
        """The curated Tasks mapping, loaded on first use and kept.

        Lazy because a Config-view user never needs it, and `None` is also the honest
        answer on a broken install: `_load_mapping` degrades to the Config view rather
        than failing to open a window."""
        self._categories: tuple[CategoryPlan, ...] = ()
        self._built: list[SidebarEntry] = []
        """Every built Page, in build order, as the sidebar needs to know it.

        The sidebar is filled from this rather than during construction: the two Views
        show the same Pages in different orders, and building the Pages twice -- once per
        arrangement -- is how a Page ends up in one View only."""
        self._pages: list[ConfigPage] = []
        self._binds_page: BindsPage | None = None
        self._window_rules_page: WindowRulesPage | None = None
        self._layer_rules_page: LayerRulesPage | None = None
        self._workspace_rules_page: WorkspaceRulesPage | None = None
        self._monitors_page: MonitorsPage | None = None
        self._declaration_pages: dict[str, DeclarationsPage] = {}
        self._scripting_page: ScriptingPage | None = None
        self._theming_page: ThemingPage | None = None
        self._shown_entities: dict[str, tuple[Any, ...]] = {}
        """Each Entity list as its Page last drew it, so `sync` can tell which have moved."""
        self._shown_live = False
        """Whether the Entity Pages last drew their rows editable."""
        self._shown_causes: tuple[str | None, str | None] = (None, None)
        """The read-only cause and the unreadable-lists sentence the Entity Pages last drew
        their Save tooltips and empty states with (#269)."""
        self._section_titles: dict[str, str] = {}
        """Every built Page's heading, by the sidebar id it answers to.

        Filled by `rebuild` from the Pages themselves, so a Page that is not a Section
        -- every Entity Page -- gets the name it calls itself rather than one derived
        from its internal id."""
        self._session.watch_monitors(self._on_monitor_hotplug)
        self._offered: Detection | None = None
        """The import on offer, while one is (ADR-0009).

        Held because it outranks the health Banner: "settings can't be saved yet, Convert..."
        is more use to someone on an unmigrated box than "no compositor", and it is the only
        Banner state with a way out on its own button."""
        self._index = SearchIndex.build(session.schema, session)
        """The finder's index (ADR-0017 §Index build): its Options built here, its Entities
        on the first query and whenever a query finds the model moved since.

        The Options at construction rather than on first Ctrl+F: the build is one pass over
        the Schema and the alternative is a first search that stutters, which is the one
        search the user judges the feature by. The Entities are read from the session at
        query time, so startup pays nothing for them and no edit has to announce itself."""
        self._revealed: frozenset[str] = frozenset()
        """The Options a search hit has earned a place for on this visit (the One-off reveal).

        At most one name at a time, and cleared the moment the user navigates anywhere
        themselves. Holding it on the window rather than on the Row is what makes it
        genuinely one-off: it is an input to `rebuild`, so nothing has to remember to undo
        it -- the next ordinary rebuild simply does not carry it."""
        self._dependents = _dependents(session.schema)
        self._last_failure: str | None = None
        self._closing = False
        self._profile_toast: Adw.Toast | None = None
        self._profile_offered: tuple[str, frozenset[tuple[str, str]]] | None = None
        """Which `(profile, connected set)` pair the standing toast already offered.

        The hotplug dedupe: one dock arriving as several socket2 events must produce one
        toast, and a set with no match clears it so the next docking offers again."""
        self._countdown: _DisplayCountdown | None = None
        """The one Confirm-or-revert countdown open over the window, if any (#192).

        Every display-breaking change -- a batch of edits, a profile activation, an undo --
        opens it or joins it, so the user never faces two clocks."""
        self._pending_breaking: dict[str, dict[str, Any]] = {}
        """Display-breaking edits inside the debounce, by rule identity, last value winning.

        Not in the model yet, and the Page is not refreshed meanwhile: its widgets keep the
        value the user set until the batch applies."""
        self._debounce: int | None = None
        self._colour_conflict: ColourConflictDialog | None = None
        self._undo_toast: Adw.Toast | None = None
        """The undo offer currently on screen, so the next one replaces it.

        Without this a burst of gestures stacks toasts, and the button on the one the user
        finally reaches is the *oldest* gesture rather than the last -- an undo that takes
        back something they have since changed twice."""
        self._result_toast: Adw.Toast | None = None
        """The last failure toast `on_applied` raised, which a Preset's offer replaces."""
        self._theming_memory = ThemingMemory()
        """The Theming page's Regenerate options and tab, kept across rebuilds (F8)."""
        self.on_import_kept: Callable[[], None] | None = None
        """Starts the session an offer in front of it held back, once the user's answer
        leaves the app's own config in place. Set by `start_when_answered`."""
        self._switch_offer_open = False
        """The relaunch's "A configuration switch was not finished" is waiting for an answer:
        no session may start under it (#148 review R2)."""

        self.set_title("Hyprtweaker")
        self.set_default_size(1000, 700)

        self._sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar.connect("row-selected", self._on_section_selected)
        self._finder = Finder(
            self._index,
            on_activate=self.open_hit,
            on_mode_changed=self._show_sidebar_mode,
        )
        # `row-activated` fires for a click or Enter and *not* for a programmatic
        # `select_row`, which is the distinction the One-off reveal needs: the reveal itself
        # selects a row, and a handler on `row-selected` would drop the reveal it just made.
        self._sidebar.connect("row-activated", lambda *_: self._end_one_off_reveal())

        self._stack = Gtk.Stack(vexpand=True)
        # Plain text, set before any title: the titles carry file names, and a path holding
        # an ampersand parsed as markup renders nothing at all.
        self._banner = Adw.Banner(revealed=False, use_markup=False)
        self._banner.connect("button-clicked", self._on_banner_clicked)
        # The one surface a failed apply reports through. It has to exist before
        # `_build_content` wraps the body in it, and before the first `show_result`.
        self._toasts = Adw.ToastOverlay()
        self._content_page = Adw.NavigationPage(title="Hyprtweaker")

        self._split = Adw.NavigationSplitView(
            sidebar=self._build_sidebar(),
            content=self._build_content(),
            min_sidebar_width=220,
        )
        self.set_content(self._split)

        self._install_actions()
        self.rebuild()

        self.connect("close-request", self._on_close_request)
        self.connect("notify::visible-dialog", _release_dialogs_on_close)

    # --- construction -----------------------------------------------------------------------

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.pack_end(self._menu_button())
        header.pack_start(self._finder.button)

        scroller = Gtk.ScrolledWindow(
            child=self._sidebar,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        results = Gtk.ScrolledWindow(
            child=self._finder.results,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )

        # Which of the two is showing is a function of the query alone, and the finder says
        # so (ADR-0017: "while the query is non-empty, grouped results replace the nav
        # list"). Why a stack rather than one refilled ListBox: `_show_sidebar_mode`.
        self._sidebar_stack = Gtk.Stack(vexpand=True)
        self._sidebar_stack.add_named(scroller, NAV_MODE)
        self._sidebar_stack.add_named(results, RESULTS_MODE)

        # Type-to-search captures on the window, so it works wherever focus happens to be.
        self._finder.capture_keys_from(self)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # Directly under the header, which is both what ADR-0017's amended §Surface asks for
        # and the only place a `GtkSearchBar` can live without breaking type-to-search: it
        # has to stay mapped while the finder is closed, and it never collapses horizontally.
        body.append(self._finder.bar)
        body.append(self._build_view_switcher())
        body.append(self._sidebar_stack)

        toolbar = Adw.ToolbarView(content=body)
        toolbar.add_top_bar(header)
        return Adw.NavigationPage(title=SIDEBAR_TITLE, child=toolbar)

    def _build_view_switcher(self) -> Gtk.Widget:
        """The segmented control above the sidebar list (#7).

        Two linked `ToggleButton`s rather than `Adw.ToggleGroup`, which arrived in the same
        libadwaita 1.7 that is now the app's floor (`MINIMUM_LIBADWAITA`): a later tidy-up,
        not a fix, since the two buttons already work.

        The buttons drive the window action rather than each other. A toggle handler that
        flipped its sibling directly would re-enter on that flip -- the classic segmented
        control bug where one click rebuilds the view twice.
        """
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            homogeneous=True,
            css_classes=["linked"],
            margin_start=6,
            margin_end=6,
            margin_top=6,
            margin_bottom=6,
        )
        self._view_buttons: dict[View, Gtk.ToggleButton] = {}
        for view, label, tooltip in (
            (View.TASKS, "Tasks", "Settings grouped by what you want to change"),
            (View.CONFIG, "Config", "One page per Hyprland config section"),
        ):
            button = Gtk.ToggleButton(
                label=label,
                tooltip_text=tooltip,
                active=view is self.view,
            )
            button.connect("toggled", self._on_view_button_toggled, view)
            self._view_buttons[view] = button
            box.append(button)
        return box

    def _build_content(self) -> Adw.NavigationPage:
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self._banner)
        body.append(self._stack)

        toolbar = Adw.ToolbarView(content=self._toasts)
        self._toasts.set_child(body)
        toolbar.add_top_bar(Adw.HeaderBar())
        self._content_page.set_child(toolbar)
        return self._content_page

    def _menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Undo", f"win.{UNDO_ACTION}")
        menu.append("Show advanced settings", f"win.{SHOW_ADVANCED_ACTION}")

        # The View choice, in the menu as well as in the segmented control (#7). Two ways to
        # the same action, because the control is discoverable and the menu is where someone
        # who has already hidden the sidebar can still reach it.
        views = Gio.Menu()
        views.append("Tasks", f"win.{VIEW_ACTION}('{View.TASKS.value}')")
        views.append("Config", f"win.{VIEW_ACTION}('{View.CONFIG.value}')")
        menu.append_section("View", views)

        themes = Gio.Menu()
        for name in _SCHEMES:
            themes.append(name.capitalize(), f"win.{THEME_ACTION}('{name}')")
        menu.append_section("Theme", themes)
        # Its own unlabelled section right below Theme rather than inside it: forgetting a
        # dialog answer is not a colour, and under the "Theme" heading it would read as one.
        remembered = Gio.Menu()
        forget = Gio.MenuItem.new(
            "Forget remembered choices", f"win.{FORGET_REMEMBERED_ACTION}"
        )
        forget.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
        remembered.append_item(forget)
        menu.append_section(None, remembered)

        interop = Gio.Menu()
        interop.append(IMPORT_LABEL, f"win.{IMPORT_ACTION}")
        interop.append("Export...", f"win.{EXPORT_ACTION}")
        interop.append("Last import report", f"win.{REPORT_ACTION}")
        # A section of its own: Import and Export are about somebody else's config coming in
        # or this one going out, which is a different kind of act from changing a setting.
        menu.append_section(None, interop)
        # The popover is built here rather than left to the button, so its entries exist to
        # be given a tooltip: a menu model has no attribute for one.
        popover = Gtk.PopoverMenu.new_from_model(menu)
        if self._session.hyprland_too_old:
            self._explain_unavailable_import(popover)
        return Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            popover=popover,
            tooltip_text="Main menu",
        )

    def _explain_unavailable_import(self, popover: Gtk.PopoverMenu) -> None:
        """Say why Import is greyed out, in the Banner's own words (#101), less its "settings
        are read-only", which is about the Pages, not about importing.

        Below Hyprland 0.56 the compositor reads hyprlang only, so the wizard would write a
        Lua file it cannot load. The action is disabled in `_install_actions`; this is the
        half that tells the user why, on the entry they are looking at.
        """
        pending: list[Gtk.Widget] = [popover]
        while pending:
            widget = pending.pop()
            # `GtkModelButton` is private API and reports no action name, so the entry is
            # found by the label this menu gave it.
            if (
                type(widget).__name__ == "GtkModelButton"
                and widget.get_property("text") == IMPORT_LABEL
            ):
                widget.set_tooltip_text(f"{self._session.unsupported_reason}.")
                return
            child = widget.get_first_child()
            while child is not None:
                pending.append(child)
                child = child.get_next_sibling()

    def _install_actions(self) -> None:
        advanced = Gio.SimpleAction.new_stateful(
            SHOW_ADVANCED_ACTION,
            None,
            GLib.Variant.new_boolean(self._prefs.show_advanced),
        )
        advanced.connect("activate", self._on_toggle_advanced)
        self.add_action(advanced)
        self._advanced_action = advanced

        view = Gio.SimpleAction.new_stateful(
            VIEW_ACTION,
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(self._view.value),
        )
        view.connect("activate", self._on_choose_view)
        self.add_action(view)
        self._view_action = view

        theme = Gio.SimpleAction.new_stateful(
            THEME_ACTION,
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(_theme_from(self._prefs.theme)),
        )
        theme.connect("activate", self._on_choose_theme)
        self.add_action(theme)

        forget = Gio.SimpleAction.new(FORGET_REMEMBERED_ACTION, None)
        forget.connect("activate", self._on_forget_remembered)
        forget.set_enabled(bool(self._prefs.remembered))
        self.add_action(forget)
        self._forget_action = forget

        undo = Gio.SimpleAction.new(UNDO_ACTION, None)
        undo.connect("activate", self._on_undo)
        self.add_action(undo)
        self._undo_action = undo
        # A Ctrl+Z pressed over an edit still in flight runs once the edit lands (#151 review).
        self._session.on_undo_due = self._undo

        for name, handler in (
            (IMPORT_ACTION, self._on_import),
            (EXPORT_ACTION, self._on_export),
            (REPORT_ACTION, self._on_report),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        # Never reachable below Hyprland 0.56, for the whole run: that does not change.
        self.lookup_action(IMPORT_ACTION).set_enabled(not self._session.hyprland_too_old)

        search = Gio.SimpleAction.new(SEARCH_ACTION, None)
        search.connect("activate", self._on_search_action)
        self.add_action(search)

        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        for accelerator, action in (
            (UNDO_ACCELERATOR, UNDO_ACTION),
            (SEARCH_ACCELERATOR, SEARCH_ACTION),
        ):
            controller.add_shortcut(
                Gtk.Shortcut(
                    trigger=Gtk.ShortcutTrigger.parse_string(accelerator),
                    action=Gtk.NamedAction.new(f"win.{action}"),
                )
            )
        self.add_controller(controller)

        application = self.get_application()
        if application is not None:
            # Only the label, and only when there is an application to ask: a window built
            # bare -- which is how the smoke tier builds one -- has no accel map to write to.
            application.set_accels_for_action(f"win.{UNDO_ACTION}", [UNDO_ACCELERATOR])
            application.set_accels_for_action(f"win.{SEARCH_ACTION}", [SEARCH_ACCELERATOR])

    # --- migration, import and export ----------------------------------------------------

    def migration_flow(self, source: Path | None = None) -> MigrationFlow:
        """A flow over this session's paths and schema, wired to its compositor if any.

        The client is built here rather than borrowed from the Session because migration is
        the one caller of `reload full-reset`, and because a first-run wizard commonly runs
        while the Session itself is read-only -- there is no live model to apply through yet.
        """
        try:
            client: CommandClient | None = CommandClient(self._session.instance())
        except NoInstance:
            # No compositor to talk to. The wizard still detects, previews and writes; the
            # config simply takes effect at next login instead of now.
            client = None

        flow = MigrationFlow(
            paths=self._session.paths,
            schema=self._session.schema,
            app_version=self._session.app_version,
            client=client,
        )
        if source is not None:
            flow.detect()
            if not asks_consent(source):
                # A `.lua` is read only once the wizard has asked (#190); building its
                # preview here would raise `ConsentRequired` out of the file chooser.
                flow.build_preview(source)
        return flow

    def show_migration(self, source: Path | None = None) -> MigrationDialog:
        """Open the wizard. Returned so the UI tier can assert on what it is showing."""
        return migration_dialog(
            self,
            self.migration_flow(source),
            spawn=self._spawn,
            on_finished=self._on_migration_finished,
            source=source,
        )

    def _on_migration_finished(self, decision: Decision | None) -> None:
        """Whatever the wizard's ending, ask again what the config is now (ADR-0009).

        Kept, or written for the next login with no compositor to switch (#148 hand-test
        21): the config is the app's, so the offer goes and the session goes live over it,
        as a relaunch would (hand-test 11). Rolled back or closed part-way: the offer stands.
        A kept menu Import into a running session re-reads it (F5 of the #148 review).
        """
        if self._offered is not None and not self._detect().offers_import:
            self._offered = None
            start, self.on_import_kept = self.on_import_kept, None
            if start is not None:
                start()
            else:
                self._session.adopt_import()
        elif decision is Decision.KEPT:
            self._session.adopt_import()
        self.sync()

    def route_first_run(self) -> Detection:
        """ADR-0009's four cases, decided once at startup and acted on.

        Returns the detection so the caller -- and the smoke tier -- can see which way it
        went without inspecting dialogs.
        """
        session = self._session
        detection = self._detect()
        if session.hyprland_too_old:
            # Nothing to route: a fresh scaffold or a converted config would be a Lua file
            # this compositor never reads. The Session's own Banner says what is needed.
            self._offered = None
            return detection
        self._offered = detection if detection.offers_import else None

        pending = sentinel_read(session.paths)
        if pending is not None:
            # Read-only until answered: a session going live under the offer cleared the
            # read-only state a Roll back set, and wrote over the restored file (R2).
            self._switch_offer_open = True
            session.set_read_only("A configuration switch was not finished")
            self.sync_banner()
            self._offer_rollback(pending)
            return detection

        if detection.kind is ConfigKind.FRESH:
            fresh_start(session.paths, session.schema, app_version=session.app_version)
        elif detection.offers_import:
            # Read-only until the offered import is accepted: there is nowhere honest to
            # write while the live session is reading a file this app does not own.
            session.set_read_only(READ_ONLY_REASON[detection.kind], sentence=CONVERT_SENTENCE)
            self.sync_banner()
            GLib.idle_add(self._present_offer, detection)
        return detection

    def start_when_answered(self, start: Callable[[], None]) -> None:
        """Start the session now, or once the user has answered the offer in front of it.

        An import offer holds it until the import is kept; the relaunch's pending-switch
        offer until it is answered, and then only if the answer leaves the app's own config.
        """
        if self._switch_offer_open or self._offered is not None:
            self.on_import_kept = start
        else:
            start()

    def _start_held_session(self) -> None:
        start, self.on_import_kept = self.on_import_kept, None
        if start is not None:
            start()

    def _detect(self) -> Detection:
        """Which of ADR-0009's four cases this machine is in, asked once per caller."""
        return detect(
            self._session.paths,
            app_version=self._session.app_version,
            schema_version=self._session.schema.hyprland_version,
        )

    def _present_offer(self, detection: Detection) -> bool:
        self.show_migration()
        return GLib.SOURCE_REMOVE

    def _offer_rollback(self, pending: Sentinel) -> Adw.AlertDialog:
        """A switch nobody confirmed. Treat it as failed and offer to undo it (ADR-0009)."""
        dialog = Adw.AlertDialog(
            heading="A configuration switch was not finished",
            body=(
                "The app closed part-way through switching your configuration, so it was "
                "never confirmed. Rolling back puts you on the configuration you had before."
            ),
        )
        dialog.add_response("keep", "Keep it")
        dialog.add_response("roll-back", "Roll back")
        dialog.set_response_appearance("roll-back", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("roll-back")
        dialog.set_close_response("roll-back")
        dialog.connect("response", self._on_rollback_response, pending)
        dialog.present(self)
        return dialog

    def _on_rollback_response(
        self, _dialog: Adw.AlertDialog, response: str, pending: Sentinel
    ) -> None:
        # Answered once: closing the dialog emits its close response ("roll-back") again,
        # and a second roll back used to delete the file the first put back (hand-test 19).
        _dialog.disconnect_by_func(self._on_rollback_response)
        flow = self.migration_flow()
        if response == "keep":
            self._keep_pending(flow, pending)
            return
        try:
            outcome = flow.roll_back(pending)
        except Exception as error:  # any failure must reach a dialog that can close
            _log.warning("rolling back the unfinished switch failed", exc_info=True)
            body = (
                f"Roll back did not finish: {error}\n\nIf you are locked out, run this from "
                f"a TTY:\n{marker_rescue_command(self._session.paths, pending)}"
            )
            GLib.idle_add(self._show_unfinished, pending, body)
            return
        if not outcome.complete:
            # Nothing was changed and the marker stands: still unanswered, still read-only.
            GLib.idle_add(
                self._show_unfinished, pending, "\n\n".join((outcome.rescue, *outcome.notes))
            )
            return
        self._switch_offer_open = False
        self._spawn(flow.reload_restored())
        # The user's own file is back, so the session must not write to the app's Modules
        # any more: the same offer a launch on that file makes (#148 hand-tests 19, 20).
        # The app's own config back (an Import over it, R1): the session starts over it.
        detection = self._detect()
        if detection.offers_import:
            self._offered = detection
            self._session.set_read_only(
                READ_ONLY_REASON[detection.kind], sentence=CONVERT_SENTENCE
            )
            self.sync_banner()
        else:
            self._start_held_session()
        # Said, as the wizard's own Roll back says it, with any theming tool's file left
        # as the user changed it or not put back (finding 21).
        GLib.idle_add(self._show_rollback_notes, outcome.notes)

    def _keep_pending(self, flow: MigrationFlow, pending: Sentinel) -> None:
        try:
            flow.keep()
        except Exception as error:  # any failure must reach a dialog that can close
            _log.warning("keeping the unfinished switch failed", exc_info=True)
            body = (
                f"Keep did not finish: {error}\n\nIf you are locked out, run this from a "
                f"TTY:\n{marker_rescue_command(self._session.paths, pending)}"
            )
            GLib.idle_add(self._show_unfinished, None, body)
            return
        self._switch_offer_open = False
        self._start_held_session()

    def _show_unfinished(self, pending: Sentinel | None, body: str) -> bool:
        """The relaunch's Roll back or Keep could not finish (#268 AC1, AC4).

        The app stays read-only and the switch unfinished, so the next start offers it
        again; with a Roll back that stopped, Keep is offered here as the way forward.
        """
        dialog = Adw.AlertDialog(heading="The switch is still unfinished", body=body)
        dialog.add_response("close", "Close")
        if pending is not None:
            dialog.add_response("keep", "Keep the new configuration")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        if pending is not None:
            dialog.connect("response", self._on_unfinished_response, pending)
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _on_unfinished_response(
        self, dialog: Adw.AlertDialog, response: str, pending: Sentinel
    ) -> None:
        dialog.disconnect_by_func(self._on_unfinished_response)
        if response == "keep":
            self._keep_pending(self.migration_flow(), pending)

    def _show_rollback_notes(self, notes: tuple[str, ...]) -> bool:
        body = "\n\n".join(("You are on the configuration you had before the switch.", *notes))
        dialog = Adw.AlertDialog(heading="Rolled back", body=body)
        dialog.add_response("ok", "OK")
        dialog.present(self)
        return GLib.SOURCE_REMOVE

    def _on_import(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        if self._session.hyprland_too_old:
            return
        import_dialog(self, self.show_migration)

    def _on_export(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        self.export()

    def export(self) -> Gtk.FileDialog | Adw.AlertDialog:
        """Export, or say why there is nothing of the user's to export yet (F6, #148 review).

        The export is rendered from the model, which is the user's config only once it has
        been read: never while an import is still offered, and not offline without Lua.
        Exporting then wrote an empty hyprland.lua and called it done. Returned for tests.
        """
        if self._offered is not None:
            dialog = Adw.AlertDialog(
                heading="Convert your config first",
                body=(
                    "Export writes the settings this app manages as one hyprland.lua, and "
                    "your config has not been converted yet, so none of it would be in the "
                    "file. Convert it, then export."
                ),
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("convert", "Convert...")
            dialog.set_response_appearance("convert", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("convert")
            dialog.set_close_response("cancel")
            dialog.connect(
                "response",
                lambda _d, response: self.show_migration() if response == "convert" else None,
            )
            dialog.present(self)
            return dialog
        if not self._session.model_read:
            dialog = Adw.AlertDialog(
                heading="Nothing to export yet",
                body=(
                    "Export writes your settings as one hyprland.lua, and this app has not "
                    f"been able to read them. {self._session.offline_sentence or ''}".rstrip()
                ),
            )
            dialog.add_response("close", "Close")
            dialog.present(self)
            return dialog
        return export_dialog(self, self._write_export)

    def _on_report(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        """The last import's Loss report, reachable long after the wizard closed (ADR-0009).

        Persisted at Preview time precisely so this entry can exist: a user who wants to
        know what conversion cost them usually wants it days later, not while deciding.
        """
        report = LossReport.latest(self._session.paths)
        if report is None:
            self._toasts.add_toast(plain_toast("No configuration has been imported yet"))
            return
        provenance = self._session.manifest().migration or {}
        kept = bool(report.source) and provenance.get("source") == report.source
        import_report_dialog(self, report, kept=kept)

    def _write_export(self, target: Path) -> None:
        result = export_render(
            self._session.model,
            self._session.paths,
            app_version=self._session.app_version,
        )
        existed = target.exists()
        try:
            result.write(target)
        except OSError as error:
            # The write is atomic (#251): a failure leaves whatever was at `target` as it was.
            reason = error.strerror or str(error)
            note = f"The export was not written: {reason[:1].lower()}{reason[1:]}"
            if existed:
                note += f". {target.name} is unchanged"
            self._toasts.add_toast(plain_toast(note, timeout=8))
            return
        note = (
            f"Exported to {target.name}"
            if not result.missing
            else f"Exported to {target.name}, without {len(result.missing)} unreadable file(s)"
        )
        self._toasts.add_toast(plain_toast(note))

    # --- the Config view ---------------------------------------------------------------------

    @property
    def pages(self) -> tuple[ConfigPage, ...]:
        """Every built Page. The UI smoke tier asserts against this."""
        return tuple(self._pages)

    @property
    def binds_page(self) -> BindsPage | None:
        """The Binds Page, once `rebuild` has built it. The UI tier asserts against it."""
        return self._binds_page

    @property
    def window_rules_page(self) -> WindowRulesPage | None:
        """The Window rules Page, once built. The UI tier asserts against it."""
        return self._window_rules_page

    @property
    def layer_rules_page(self) -> LayerRulesPage | None:
        """The Layer rules Page, once built. The UI tier asserts against it."""
        return self._layer_rules_page

    @property
    def workspace_rules_page(self) -> WorkspaceRulesPage | None:
        """The Workspaces Page, once built. The UI tier asserts against it."""
        return self._workspace_rules_page

    def declaration_page(self, kind: str) -> DeclarationsPage | None:
        """One declarative Entity Page by kind, once built. The UI tier asserts against it.

        A lookup rather than seven properties: the Pages are one class seven times, so
        seven accessors would be seven chances for one of them to name the wrong kind.
        """
        return self._declaration_pages.get(kind)

    @property
    def declaration_pages(self) -> tuple[DeclarationsPage, ...]:
        """Every declarative Page, in sidebar order."""
        return tuple(self._declaration_pages.values())

    @property
    def scripting_page(self) -> ScriptingPage | None:
        """The Scripting Page, once built. The UI tier asserts against it."""
        return self._scripting_page

    @property
    def theming_page(self) -> ThemingPage | None:
        """The Theming Page (ADR-0014), once built. The UI tier asserts against it."""
        return self._theming_page

    @property
    def monitors_page(self) -> MonitorsPage | None:
        """The Displays Page, once built. The UI tier asserts against it."""
        return self._monitors_page

    @property
    def show_advanced(self) -> bool:
        return bool(self._advanced_action.get_state().get_boolean())

    @property
    def view(self) -> View:
        """Which sidebar arrangement is showing (#7)."""
        return self._view

    @property
    def categories(self) -> tuple[CategoryPlan, ...]:
        """The curated categories currently built. Empty in the Config view.

        The UI smoke tier asserts against this: it is how "the Tasks view built all its
        Pages" becomes a question a headless test can ask of a real window.
        """
        return self._categories

    def set_view(self, view: View) -> None:
        """Switch arrangement and remember the choice (#7, ADR-0019).

        The one entry point for all three callers -- the control, the menu, and ADR-0017's
        search hit whose Row has no home in the active View -- so that a search-driven
        switch persists exactly like a manual one rather than silently reverting next start.
        """
        if view is self._view:
            return
        # Switching arrangement is the user navigating, so it ends any outstanding One-off
        # the same way picking a sidebar Page does. `open_hit` sets `_revealed` *after* its
        # own call to this, so a search-driven switch is unaffected -- and without this an
        # advanced-tier reveal would survive into the other View, which is exactly the
        # "temporary hidden state" ADR-0017 refused to introduce.
        self._revealed = frozenset()
        self._view = view
        self._remember(self._prefs.with_view(view.value))
        self._view_action.set_state(GLib.Variant.new_string(view.value))
        self._sync_view_buttons()
        self.rebuild()

    def _sync_view_buttons(self) -> None:
        """Put the segmented control where the state says, without re-entering.

        `set_active` emits `toggled`, whose handler calls back into `set_view`; blocking the
        handler is what keeps one click from rebuilding the window twice.
        """
        for view, button in self._view_buttons.items():
            button.handler_block_by_func(self._on_view_button_toggled)
            button.set_active(view is self._view)
            button.handler_unblock_by_func(self._on_view_button_toggled)

    def _on_view_button_toggled(self, button: Gtk.ToggleButton, view: View) -> None:
        if button.get_active():
            self.set_view(view)
        elif view is self._view:
            # The only button in a segmented pair cannot be turned *off*: clicking the
            # active one would otherwise leave the sidebar showing no arrangement at all.
            self._sync_view_buttons()

    def _on_choose_view(self, _action: Gio.SimpleAction, parameter: Any) -> None:
        self.set_view(_view_from(parameter.get_string()))

    def _remember(self, prefs: Prefs) -> None:
        """Hold the new preferences and try to store them.

        A failed write is deliberately silent: `$XDG_STATE_HOME` being read-only means the
        choice will not survive a restart, which is not worth a toast over the Row the user
        is looking at, and `PrefsStore.save` has already declined to raise.

        The one path every preference change takes, a dialog's remembered answer included
        (#170), so "Forget remembered choices" turns sensitive the moment there is one.
        """
        self._prefs = prefs
        self._prefs_store.save(prefs)
        self._forget_action.set_enabled(bool(prefs.remembered))
        if self._theming_page is not None:
            # The Presets group shows a remembered colour answer, with "Forget" (#171).
            self._theming_page.presets.refresh()

    def _on_choose_theme(self, action: Gio.SimpleAction, parameter: Any) -> None:
        theme = _theme_from(parameter.get_string())
        action.set_state(GLib.Variant.new_string(theme))
        Adw.StyleManager.get_default().set_color_scheme(_SCHEMES[theme])
        self._remember(self._prefs.with_theme(theme))

    def _on_forget_remembered(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        self._remember(self._prefs.without_any_remembered())
        self._toasts.add_toast(plain_toast("Remembered choices forgotten"))

    @property
    def visible_section(self) -> str | None:
        """Which Section's Page the content pane is showing."""
        return self._stack.get_visible_child_name()

    def rebuild(self) -> None:
        """Build every Page for the active View, replacing whatever was there.

        Whole-view rebuild rather than per-Row reveal: the Advanced switch changes which
        Options exist on a Page and therefore which Group each one lands in, and rebuilding
        from the plan is the only version of that which cannot drift from `plan.py`.

        The Pages go into the stack here and the sidebar is filled at the end, from
        `_destinations`. Two passes because the Views differ only in how the same Pages are
        arranged and named -- interleaving construction with sidebar order is what would
        let a Page exist in one View and not the other, which is the one thing #7 forbids.
        """
        selected = self._selected_section()

        self._pages = []
        self._section_titles = {}
        self._built = []
        self._sidebar.remove_all()
        # The window holds its focus widget. A focused Row of an old Page outlives `release`,
        # and its chrome then keeps the Page and the window (#219). Whether GTK lets go of it
        # on removal depends on whether the window is active, so it is dropped here first.
        focus = self.get_focus()
        if focus is not None and focus.is_ancestor(self._stack):
            self.set_focus(None)
        while (child := self._stack.get_first_child()) is not None:
            self._stack.remove(child)
            release(child)

        self._categories, option_plans = self._plan_view()

        for plan in option_plans:
            page = ConfigPage(plan, self._factory)
            self._pages.append(page)
            self._stack.add_named(_scrolled(page.page), plan.section)
            self._register(plan.section, plan.title, plan.option_count)
            self._section_titles[plan.section] = plan.title

        # An Entity Page, so it comes from the model rather than from the Schema plan: there
        # is no Option behind a Bind to plan against (CONTEXT.md, ADR-0007).
        self._binds_page = BindsPage(
            self._session,
            actions=BindActions(
                add=self._add_bind,
                edit=self._edit_bind,
                remove=self._remove_bind,
                enable=self._set_bind_enabled,
                rebind=self._rebind_bind,
                recapture=lambda index: self._rebind_bind(index, enable=True),
                swap=self._swap_binds,
                move=self._move_bind,
                edit_submap=self._edit_submap,
            ),
        )
        self._stack.add_named(_scrolled(self._binds_page.page), BindsPage.section)
        self._section_titles[BindsPage.section] = BindsPage.title
        self._register(BindsPage.section, BindsPage.title, len(self._binds_page.binds))

        # The rule Pages: the same Entity-Page shape, twice (ADR-0008).
        self._window_rules_page = WindowRulesPage(
            self._session, actions=self._rule_actions("window")
        )
        self._layer_rules_page = LayerRulesPage(
            self._session, actions=self._rule_actions("layer")
        )
        for rules_page in (self._window_rules_page, self._layer_rules_page):
            self._stack.add_named(_scrolled(rules_page.page), rules_page.section)
            self._section_titles[rules_page.section] = rules_page.title
            self._register(rules_page.section, rules_page.title, len(rules_page.rules))

        # The Workspaces Page: workspace rules, one row per selector (ADR-0008, #159).
        self._workspace_rules_page = WorkspaceRulesPage(
            self._session,
            actions=WorkspaceRuleActions(
                add=self._add_workspace_rule,
                edit=self._edit_workspace_rule,
                remove=self._remove_workspace_rule,
            ),
        )
        workspaces = self._workspace_rules_page
        self._stack.add_named(_scrolled(workspaces.page), workspaces.section)
        self._section_titles[workspaces.section] = workspaces.title
        self._register(workspaces.section, workspaces.title, len(workspaces.rules))

        # The Displays destination: an Entity Page over monitor rules plus the live
        # helper data the canvas draws from (ADR-0008, #68).
        self._monitors_page = MonitorsPage(
            self._session,
            actions=MonitorActions(
                apply_breaking=self._apply_monitor_breaking,
                apply_benign=self._apply_monitor_benign,
                rename=self._rename_monitor_rule,
                remove=self._remove_monitor_rule,
            ),
            profiles=ProfileActions(
                save=self._save_monitor_profile,
                activate=self._activate_monitor_profile,
                update=self._update_monitor_profile,
                detach=self._detach_monitor_profile,
                delete=self._delete_monitor_profile,
            ),
        )
        self._stack.add_named(_scrolled(self._monitors_page.page), MonitorsPage.section)
        self._section_titles[MonitorsPage.section] = MonitorsPage.title
        self._register(MonitorsPage.section, MonitorsPage.title, len(self._monitors_page.rules))
        # The app-open answer feeds the canvas *and* the Profile-match toast: one fetch,
        # riding the same helper-data lane hotplug refreshes use (ADR-0018).
        self._session.fetch_monitors(self._on_monitors_event)

        # The seven declarative Entity Pages (#70): one class over a field catalogue, so
        # this loop is the whole registration rather than seven blocks like the ones above.
        self._declaration_pages = {}
        for page_class in DECLARATION_PAGES:
            page = page_class(self._session, actions=self._declaration_actions(page_class.kind))
            self._declaration_pages[page_class.kind] = page
            self._stack.add_named(_scrolled(page.page), page.section)
            self._register(page.section, page.title, len(page.entities))
            self._section_titles[page.section] = page.title

        # The Scripting Page: the plugin load list (#174) above a read-only inventory of the
        # user's Lua (ADR-0018, #173).
        self._scripting_page = ScriptingPage(
            self._session,
            actions=ScriptingActions(open_file=self._launch_file),
            plugin_actions=PluginActions(
                add=self._add_plugin,
                remove=self._remove_plugin,
                enable=self._set_plugin_enabled,
                move=self._move_plugin,
            ),
        )
        scripting = self._scripting_page
        self._stack.add_named(_scrolled(scripting.page), scripting.section)
        self._section_titles[scripting.section] = scripting.title
        self._register(scripting.section, scripting.title, self._scripting_count())

        # The Theming Page (ADR-0014, #164): the Color source and the tools that make it.
        self._theming_page = ThemingPage(
            self._session,
            memory=self._theming_memory,
            actions=ThemingActions(
                toast=self._toast,
                current_wallpaper=self._session.current_wallpaper,
                presets=PresetActions(
                    apply=self.apply_preset,
                    remembered=lambda: remembered_choice(self._prefs.remembered),
                    forget=lambda: self._remember(
                        self._prefs.without_remembered(COLOR_CONFLICT_DIALOG)
                    ),
                    remember=self._remember_colours,
                    toast=self._toast,
                ),
            ),
        )
        theming = self._theming_page
        self._stack.add_named(_scrolled(theming.page), theming.section)
        self._section_titles[theming.section] = theming.title
        self._register(theming.section, theming.title, theming.set_up_count)

        self._shown_entities = self._entity_lists()
        self._shown_live = bool(self._session.live)
        self._shown_causes = self._causes()
        self._fill_sidebar()
        self._select_section(self._restored(selected))
        self.sync()

    # --- arranging the sidebar ----------------------------------------------------------

    def _plan_view(self) -> tuple[tuple[CategoryPlan, ...], tuple[PagePlan, ...]]:
        """The active View's categories and its Schema-generated Pages.

        Returns both rather than assigning `self._categories` on the way past: this is the
        *input* to a rebuild, and a planner that quietly mutates the window is one whose
        result depends on when it was called.

        Config is every Section, generated and therefore incapable of drifting. Tasks is the
        curated mapping, which *can* drift and is allowed to (ADR-0012) -- so a mapping that
        will not load falls back to the Config arrangement rather than to an empty window.
        """
        disclosure = Disclosure(
            show_advanced=self.show_advanced, view=self.view, revealed=self._revealed
        )
        mapping = None if self.view is View.CONFIG else self._load_mapping()
        if mapping is None:
            return (), plan_config_view(self._session.schema, disclosure)

        categories = plan_tasks_view(self._session.schema, mapping, disclosure)
        pages = tuple(page for category in categories for page in category.option_pages)
        return categories, pages

    def _load_mapping(self) -> TasksMapping | None:
        """The curated mapping, or None if the install cannot produce one.

        Swallowed rather than raised because this runs inside `rebuild`, which runs on every
        switch flip: a broken or missing `tasks.json` should cost the user the curated view,
        not the ability to open the app. The unit tier asserts the shipped file loads, which
        is where a packaging mistake is supposed to be caught.
        """
        if self._mapping is None:
            try:
                self._mapping = load_tasks_mapping()
            except (OSError, ValueError, KeyError):
                return None
        return self._mapping

    def _register(self, section: str, title: str, count: int) -> None:
        self._built.append(SidebarEntry(section=section, title=title, count=count))

    def _scripting_count(self) -> int:
        """The Scripting Page lists the calls found and the plugin load list: both count."""
        page = self._scripting_page
        hits = page.hit_count if page is not None else 0
        return hits + len(self._session.declarations("plugins"))

    def _restored(self, selected: str | None) -> str:
        """Which Page to select after a rebuild: the one that was showing, if it still is.

        A View switch is the case that needs this. The Views name their Pages differently --
        `look.decoration` is `decoration` in the Config view -- so carrying the old id across
        a switch selects nothing, and `_select_section` walking off the end of the list is
        silent. That left the sidebar with no selection while the stack showed its first
        child: the window disagreeing with itself about where the user is.
        """
        if selected is not None and any(entry.section == selected for entry in self._built):
            return selected
        if self._built:
            return self._built[0].section
        return self._session.schema.section_names[0]

    def _fill_sidebar(self) -> None:
        """Put the built Pages in the sidebar, in the order the active View wants them."""
        if self.view is View.CONFIG or not self._categories:
            for entry in self._built:
                self._sidebar.append(_sidebar_row(entry.section, entry.title, entry.count))
            return

        known = {entry.section: entry for entry in self._built}
        listed: set[str] = set()
        for category in self._categories:
            self._sidebar.append(_category_heading(category.title))
            for page in category.pages:
                entry = known.get(page.section)
                if entry is None:
                    # The mapping references an Entity Page this build did not produce --
                    # a kind that has not shipped yet, or a renamed id. Skipping the row is
                    # right; inventing one would put a sidebar entry in front of no Page.
                    continue
                self._sidebar.append(_sidebar_row(entry.section, entry.title, entry.count))
                listed.add(entry.section)

        # Anything built but not named by the mapping still gets a row. Entity Pages are the
        # real case: #70 can add a kind before the curation places it, and an unlisted Page
        # is exactly the "setting we forgot" failure the fallback group exists to prevent.
        leftovers = [entry for entry in self._built if entry.section not in listed]
        if leftovers:
            self._sidebar.append(_category_heading(ORPHAN_CATEGORY_TITLE))
            for entry in leftovers:
                self._sidebar.append(_sidebar_row(entry.section, entry.title, entry.count))

    # --- binds ---------------------------------------------------------------------------

    def _add_bind(self, submap: str | None = None) -> None:
        def done(bind: Bind) -> str | None:
            why = self._saved_or_why(lambda: self._session.add_bind(bind))
            if why is None:
                self._refresh_binds()
            return why

        BindEditor(on_done=done, submap=submap, fetch_switches=self._switch_fetch()).present(
            self
        )

    def _edit_bind(self, index: int) -> None:
        if self._binds_page is None:
            return
        binds = self._binds_page.binds
        if not 0 <= index < len(binds):
            return

        held = binds[index]

        def done(bind: Bind) -> str | None:
            why = self._saved_or_why(
                lambda: self._session.replace_bind(index, bind, expected=held)
            )
            if why is None:
                self._refresh_binds()
            return why

        BindEditor(on_done=done, bind=held, fetch_switches=self._switch_fetch()).present(self)

    def _saved_or_why(self, save: Callable[[], bool]) -> str | None:
        """`None` once `save` is accepted; else why not, for the editor that asked (#225).

        An editor shows the sentence above its Save and stays open with the draft, so a
        refused save loses nothing. The sentence is the refusal's own (`show_refused`,
        `show_not_saved`), with the way on, since the toast behind the dialog cannot be
        acted on while it is open.
        """
        self._refused_sentence = None
        if save():
            return None
        return self._refused_sentence or NOT_SAVED_SENTENCE

    def _switch_fetch(self) -> FetchSwitches | None:
        """The live switch list for Capture's picker, or `None` when nobody is answering.

        One source for every door that opens Capture (add, edit, rebind), so none of them
        offers a different picker. `None` rather than a callable that fails: Capture words
        "not connected" and "connected, no switch" differently (ADR-0008 degrades to
        manual entry).
        """
        return self._session.fetch_switches if self._session.live else None

    def _remove_bind(self, index: int) -> None:
        if self._session.remove_bind(index):
            self._refresh_binds()

    def _set_bind_enabled(self, index: int, enabled: bool) -> None:
        if self._session.set_bind_enabled(index, enabled):
            self._refresh_binds()

    def _swap_binds(self, first: int, second: int) -> None:
        if self._session.swap_binds(first, second):
            self._refresh_binds()

    def _move_bind(self, index: int, to: int) -> None:
        """The drag reorder, and Alt+Up/Down: the moved bind lands at `to`.

        The refresh rebuilds every row, so the moved one is revealed there: focus follows
        it for the next Alt+Up/Down, and the flash shows where a drop landed.
        """
        if self._session.move_bind(index, to):
            self._refresh_binds()
            if self._binds_page is not None:
                self._binds_page.reveal(to)

    def _rebind_bind(self, index: int, *, enable: bool = False) -> None:
        """The conflict popover's "rebind it": Capture on the other bind, directly.

        Straight to Capture rather than through the full editor (ADR-0007): the problem
        being solved is *only* that two binds share a trigger, and the fix is a new
        trigger for one of them.

        `enable` is the error row's "Fix trigger…" (#139): a disabled bind whose trigger
        Hyprland cannot load, most often a dead keysym the Importer disabled. Capture refuses
        what would not load, so whatever comes back is loadable (the Session would refuse it
        otherwise, #199), and the user asked for the bind back, so it comes back enabled.
        """
        if self._binds_page is None:
            return
        binds = self._binds_page.binds
        if not 0 <= index < len(binds):
            return
        bind = binds[index]

        def done(text: str) -> None:
            keys = str(parse_trigger(text.strip()))
            fixed = replace(bind, keys=keys, enabled=bind.enabled or enable)
            if keys and self._session.replace_bind(index, fixed, expected=bind):
                self._refresh_binds()

        CaptureDialog(
            on_done=done,
            initial=bind.keys,
            in_submap=bool(bind.submap) or bind.options.submap_universal,
            fetch_switches=self._switch_fetch(),
        ).present(self)

    def _edit_submap(self, name: str | None) -> None:
        """Open the Submap editor: `name` is `None` for a creation, else the submap."""
        entities = self._session.model.entities
        current = next((s for s in entities.submaps if s.name == name), None)

        def done(new_name: str, reset_target: str) -> None:
            if self._session.save_submap(
                original=name, name=new_name, reset_target=reset_target
            ):
                self._refresh_binds()

        SubmapEditor(
            on_done=done,
            taken=submap_names(entities),
            name=name or "",
            reset_target=current.reset_target if current is not None else "",
        ).present(self)

    def _refresh_binds(self) -> None:
        self._refresh_entity_pages(KEYBIND_KINDS)

    # --- rules ---------------------------------------------------------------------------

    def _rule_actions(self, kind: str) -> RuleActions:
        return RuleActions(
            add=lambda: self._add_rule(kind),
            edit=lambda index: self._edit_rule(kind, index),
            remove=lambda index: self._remove_rule(kind, index),
            enable=lambda index, enabled: self._set_rule_enabled(kind, index, enabled),
            move=lambda index, to: self._move_rule(kind, index, to),
        )

    def _rules_page(self, kind: str) -> RulesPage | None:
        return self._window_rules_page if kind == "window" else self._layer_rules_page

    def _rule_fetch(self, kind: str) -> Callable[..., None] | None:
        """The live half of the Rule editor, or `None` when nobody is answering.

        `None` rather than a callable that fails, because the editor uses it to decide
        whether to *offer* the pick button at all -- ADR-0008 degrades to manual entry.
        """
        if not self._session.live:
            return None
        return self._session.fetch_clients if kind == "window" else self._session.fetch_layers

    def _taken_rule_names(self, kind: str, *, besides: int | None = None) -> tuple[str, ...]:
        rules = self._session.rules(kind)
        return tuple(
            rule.name for index, rule in enumerate(rules) if rule.name and index != besides
        )

    def _add_rule(self, kind: str) -> None:
        def done(rule: WindowRule | LayerRule) -> str | None:
            why = self._saved_or_why(lambda: self._session.add_rule(kind, rule))
            if why is None:
                self._refresh_rules(kind)
            return why

        RuleEditor(
            kind=kind,
            on_done=done,
            taken_names=self._taken_rule_names(kind),
            fetch_targets=self._rule_fetch(kind),
        ).present(self)

    def _edit_rule(self, kind: str, index: int) -> None:
        rules = self._session.rules(kind)
        if not 0 <= index < len(rules):
            return

        held = rules[index]

        def done(rule: WindowRule | LayerRule) -> str | None:
            why = self._saved_or_why(
                lambda: self._session.replace_rule(kind, index, rule, expected=held)
            )
            if why is None:
                self._refresh_rules(kind)
            return why

        RuleEditor(
            kind=kind,
            on_done=done,
            rule=held,
            taken_names=self._taken_rule_names(kind, besides=index),
            fetch_targets=self._rule_fetch(kind),
        ).present(self)

    def _remove_rule(self, kind: str, index: int) -> None:
        if self._session.remove_rule(kind, index):
            self._refresh_rules(kind)

    def _set_rule_enabled(self, kind: str, index: int, enabled: bool) -> None:
        if self._session.set_rule_enabled(kind, index, enabled):
            self._refresh_rules(kind)

    def _move_rule(self, kind: str, index: int, to: int) -> None:
        """The drag reorder, and Alt+Up/Down: focus follows the moved rule, as for binds."""
        if self._session.move_rule(kind, index, to):
            self._refresh_rules(kind)
            page = self._rules_page(kind)
            if page is not None:
                page.reveal(to)

    def _refresh_rules(self, kind: str) -> None:
        self._refresh_entity_pages(frozenset({f"{kind}_rules"}))

    # --- workspace rules (#159) ---------------------------------------------------------

    def workspace_rule_editor(self, selector: str | None = None) -> WorkspaceRuleEditor:
        """The editor for a new workspace rule, or for the one whose selector is `selector`.

        Wired whole -- save, refresh, "Show it" -- so the page's buttons and the UI tier
        drive the same dialog.
        """
        rules = self._session.workspace_rules
        rule = next((item for item in rules if item.workspace == selector), None)
        original = rule.workspace if rule is not None else None

        def done(saved: WorkspaceRule) -> str | None:
            if self._session.save_workspace_rule(saved, original=original):
                self._refresh_workspace_rules()
                return None
            return self._session.offline_reason or "the change was not accepted."

        return WorkspaceRuleEditor(
            on_done=done,
            on_show=self._reveal_workspace_rule,
            rule=rule,
            taken=[item.workspace for item in rules if item is not rule],
            layouts=self._layout_choices(),
        )

    def _layout_choices(self) -> tuple[str, ...]:
        """The layouts the layout row offers: the schema's own, without its `lua:<name>`
        placeholder, then the Lua layouts the user's files register (#175)."""
        option = self._session.schema.get(LAYOUT_OPTION)
        known = option.known_values.values if option and option.known_values else ()
        return (*layout_choices(known), *discovered_layouts(self._session.paths))

    def _add_workspace_rule(self) -> None:
        self.workspace_rule_editor().present(self)

    def _edit_workspace_rule(self, selector: str) -> None:
        if any(rule.workspace == selector for rule in self._session.workspace_rules):
            self.workspace_rule_editor(selector).present(self)

    def _remove_workspace_rule(self, selector: str) -> None:
        if self._session.remove_workspace_rule(selector):
            self._refresh_workspace_rules()

    def _reveal_workspace_rule(self, selector: str) -> None:
        if self._workspace_rules_page is not None:
            self._workspace_rules_page.reveal(selector)

    def _refresh_workspace_rules(self) -> None:
        self._refresh_entity_pages(frozenset({"workspace_rules"}))

    # --- declarative entities (#70) -------------------------------------------------------

    def _declaration_actions(self, kind: str) -> DeclarationActions:
        return DeclarationActions(
            add=lambda: self._add_declaration(kind),
            edit=lambda index: self._edit_declaration(kind, index),
            remove=lambda index: self._remove_declaration(kind, index),
        )

    def _curve_names(self) -> tuple[str, ...]:
        """The curves an animation may name -- what makes the dropdown truthful."""
        return tuple(curve.name for curve in self._session.curves if curve.name)

    def declaration_editor(
        self, kind: str, *, on_done: Callable[[Any], str | None], index: int | None = None
    ) -> DeclarationEditor:
        """The editor for a new entity of `kind`, or for the one at `index`."""
        entities = self._session.declarations(kind)
        return DeclarationEditor(
            kind=kind,
            on_done=on_done,
            entity=entities[index] if index is not None else None,
            curve_names=self._curve_names(),
            taken=taken_identities(kind, entities, skip=index),
            bounds=self._session.device_field_bounds,
            choices=BY_KIND[kind].choices_from(self._session.schema),
        )

    def _add_declaration(self, kind: str) -> None:
        def done(entity: Any) -> str | None:
            why = self._saved_or_why(lambda: self._session.add_declaration(kind, entity))
            if why is None:
                self._refresh_declarations(kind)
            return why

        self.declaration_editor(kind, on_done=done).present(self)

    def _edit_declaration(self, kind: str, index: int) -> None:
        entities = self._session.declarations(kind)
        if not 0 <= index < len(entities):
            return
        held = entities[index]

        def done(entity: Any) -> str | None:
            why = self._saved_or_why(
                lambda: self._session.replace_declaration(kind, index, entity, expected=held)
            )
            if why is None:
                self._refresh_declarations(kind)
            return why

        self.declaration_editor(kind, on_done=done, index=index).present(self)

    def _remove_declaration(self, kind: str, index: int) -> None:
        """Delete one entity, warning first when other rows depend on it.

        Only curves can be depended on: an animation that names a deleted curve stops
        Hyprland from loading the whole animation Module, so this is the one delete on
        these Pages that can break something the user is not looking at.
        """
        entities = self._session.declarations(kind)
        if not 0 <= index < len(entities):
            return

        if kind == "curves":
            # Asked of the Page, which owns the question -- the window recomputing it from
            # the model would be a second answer to "who uses this curve".
            page = self._declaration_pages.get("curves")
            users = page.curve_users(entities[index].name) if page is not None else ()
            if users:
                self._confirm_curve_delete(index, entities[index].name, users)
                return

        if self._session.remove_declaration(kind, index):
            self._refresh_declarations(kind)

    def _confirm_curve_delete(self, index: int, name: str, users: tuple[str, ...]) -> None:
        listed = ", ".join(users)
        dialog = Adw.AlertDialog(
            heading=f"Delete the curve “{name}”?",
            body=(
                f"{len(users)} animation(s) name it: {listed}. Without it Hyprland "
                f"refuses them, and the whole animations file stops loading."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete anyway")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "delete" and self._session.remove_declaration("curves", index):
                self._refresh_declarations("curves")
                # The animations that named it now carry a dangling reference, so their
                # Page has to be rebuilt too -- the finding lives on a row nobody touched.
                self._refresh_declarations("animations")

        dialog.connect("response", answered)
        dialog.present(self)

    def _refresh_declarations(self, kind: str) -> None:
        # A per-device override badges the Options it shadows: `sync` refreshes those.
        self._refresh_entity_pages(frozenset({kind}))

    # --- monitors -------------------------------------------------------------------------

    def _apply_monitor_breaking(self, output: str, fields: Mapping[str, Any]) -> None:
        """A display-breaking edit: held for the debounce, then applied behind the countdown.

        ADR-0008 "batch": edits arriving within `BREAKING_DEBOUNCE_MS` of each other land as
        one patch per display, which the queue carries as one transaction, under one
        countdown. Each edit re-arms the debounce, so a drag or a spinner held down applies
        once, after it stops.
        """
        self._pending_breaking.setdefault(output, {}).update(fields)
        self._sync_undo_action()
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
        self._debounce = GLib.timeout_add(BREAKING_DEBOUNCE_MS, self._on_debounce)

    def _on_debounce(self) -> bool:
        self._debounce = None
        self.flush_monitor_edits()
        return False

    def flush_monitor_edits(self) -> None:
        """Apply the held display-breaking edits now: what the debounce does when it runs out.

        Public so the UI tier can run the debounce out without waiting for it.
        """
        pending = self._drop_pending_breaking()
        if not pending:
            return

        def patch() -> bool:
            applied = [self._session.patch_monitor_rule(o, f) for o, f in pending.items()]
            return any(applied)

        self._behind_countdown(patch)

    def _drop_pending_breaking(self) -> dict[str, dict[str, Any]]:
        if self._debounce is not None:
            GLib.source_remove(self._debounce)
            self._debounce = None
        pending, self._pending_breaking = self._pending_breaking, {}
        return pending

    def _behind_countdown(self, change: Callable[[], bool], *, profile: bool = False) -> bool:
        """Make a display-breaking change inside the one countdown, opening it or joining it.

        Opening snapshots both rule lists and the active profile *before* the change, and
        opens the undo group that turns the countdown into one step or none (#189). Joining
        applies the change and gives the clock back in full: the user must get a whole
        countdown to judge the newest change by, and never a second dialog (S3 of #151). A
        refused change leaves no countdown behind that it would have opened, and neither does
        a change that, netted out, moves no display-breaking field (scale 1 to 1.25 and back
        inside the debounce): there is nothing on screen to confirm (review of #151, 38).
        A profile activation always counts: its countdown is its only take-back.
        """
        countdown = self._countdown
        opened = countdown is None
        if countdown is None:
            countdown = self._countdown = _DisplayCountdown(
                snapshot=self._session.monitor_state_snapshot(),
                group=self._session.begin_undo_group(DISPLAY_KINDS),
                dialog=ConfirmRevertDialog(
                    on_keep=self._keep_display, on_revert=self._revert_display
                ),
            )
        applied = change()
        nothing_to_confirm = not profile and not breaks_display(
            countdown.snapshot.monitors, self._session.monitor_rules
        )
        if opened and (not applied or nothing_to_confirm):
            self._countdown = None
            self._session.end_undo_group(countdown.group, title=DISPLAY_CHANGED)
        if not applied:
            return False
        countdown.includes_profile |= profile
        self._refresh_entity_pages(DISPLAY_KINDS)
        self._sync_undo_action()
        if self._countdown is not countdown:
            return True
        if opened:
            countdown.dialog.present(self)
        else:
            countdown.dialog.restart()
        return True

    def _keep_display(self) -> None:
        """Keep: everything the countdown carried stands, as one undo step."""
        countdown, self._countdown = self._countdown, None
        if countdown is None:
            return
        self._session.end_undo_group(countdown.group, title=DISPLAY_CHANGED)
        self._refresh_monitors()

    def _revert_display(self) -> None:
        """Revert, Esc, close or expiry: the display goes back to before the countdown.

        Only the display-breaking fields go back (`revert_breaking`), so a vrr edit made
        while the clock ran survives. A countdown that activated a profile reverts whole --
        rules, workspace pins and the active pointer -- because a profile sets benign fields
        too, and the user refused all of it. Breaking edits still inside the debounce are
        dropped: they were never applied, and the user asked for the old display back.
        """
        countdown, self._countdown = self._countdown, None
        if countdown is None:
            return
        self._drop_pending_breaking()
        snapshot = countdown.snapshot
        if countdown.includes_profile:
            self._session.restore_monitor_state(snapshot)
        else:
            self._session.restore_monitor_rules(
                revert_breaking(snapshot.monitors, self._session.monitor_rules)
            )
        self._session.end_undo_group(countdown.group, title=DISPLAY_CHANGED)
        self._refresh_entity_pages(DISPLAY_KINDS)

    @property
    def display_confirm(self) -> ConfirmRevertDialog | None:
        """The Confirm-or-revert dialog of the open countdown, if one is open."""
        return None if self._countdown is None else self._countdown.dialog

    def _apply_monitor_benign(self, output: str, fields: Mapping[str, Any]) -> None:
        """A benign edit (vrr, an absent display's rule): instant per ADR-0003."""
        if self._session.patch_monitor_rule(output, fields):
            self._refresh_monitors()

    def _rename_monitor_rule(self, output: str, to: str) -> None:
        """The "Match by" toggle: same rule, other identity string (ADR-0008)."""
        if self._session.rename_monitor_rule(output, to):
            self._refresh_monitors()

    def _remove_monitor_rule(self, output: str) -> None:
        if self._session.remove_monitor_rule(output):
            self._refresh_monitors()

    def _on_monitor_hotplug(self) -> None:
        """A display came or went: refresh the canvas, then compare against the profiles.

        The Profile-match half is ADR-0018's decision line: "While the app is open, its
        existing socket2 listener (the one refreshing the arrangement canvas) compares
        the connected-output set against saved Monitor profiles" -- so a dock plugged in
        mid-session is offered exactly like one present at launch. Ordinary edit
        refreshes (`_refresh_monitors`) deliberately do not compare: the toast's trigger
        is the connected set changing, never the config drifting under it.
        """
        if self._monitors_page is None:
            self._refresh_monitors()
            return
        self._session.fetch_monitors(self._on_monitors_event)
        self.sync()

    # --- monitor profiles -----------------------------------------------------------------

    @property
    def profile_toast(self) -> Adw.Toast | None:
        """The Profile-match toast currently offered, if any. Probed by the smoke tier."""
        return self._profile_toast

    def _on_monitors_event(self, monitors: tuple[Mapping[str, Any], ...] | None) -> None:
        """A connected-set answer: position the canvas, then offer a matching profile.

        The one lane both triggers share -- app open and socket2 hotplug (ADR-0018:
        "subscribes to the same monitor events as the canvas -- no new listener").
        """
        if self._monitors_page is not None:
            self._monitors_page.set_connected(monitors)
        self._offer_profile_match(monitors)

    def _offer_profile_match(self, monitors: tuple[Mapping[str, Any], ...] | None) -> None:
        """The Profile-match toast (ADR-0018): app-open only, one click activates.

        "App-open only" is ADR-0018's contrast with the rejected daemon: while the app
        runs, every connected-set change is compared; with it closed, nothing is. Only a
        profile whose activation would change something is offered -- a stable setup
        launching into the profile it already is must hear nothing -- and each distinct
        connected set is offered once, so the second `monitoraddedv2` of one dock cannot
        stack a second toast on the first. A set with no match resets that memory:
        unplugging and redocking offers again.
        """
        if not monitors:
            self._profile_offered = None
            return
        match = self._session.matching_monitor_profile(monitors)
        if match is None:
            self._profile_offered = None
            return
        slug, profile = match
        fingerprint = frozenset(
            (str(m.get("name", "")), str(m.get("description", "")).strip()) for m in monitors
        )
        if self._profile_offered == (slug, fingerprint):
            return
        self._profile_offered = (slug, fingerprint)
        if self._profile_toast is not None:
            self._profile_toast.dismiss()
        toast = plain_toast(f'Displays match profile "{profile.name}"', timeout=10)
        toast.set_button_label("Activate")
        toast.connect("button-clicked", lambda _t: self._activate_monitor_profile(slug))
        self._profile_toast = toast
        self._toasts.add_toast(toast)

    def _save_monitor_profile(self, name: str) -> None:
        """Capture the current setup under `name` -- the save dialog's verb."""
        if self._monitors_page is None:
            return
        if self._session.save_monitor_profile(name, self._monitors_page.connected) is None:
            # Gone read-only while the name dialog was open: the button is off now.
            self._toasts.add_toast(
                plain_toast(f'Profile "{name}" was not saved: applying is off')
            )
        else:
            self._toasts.add_toast(plain_toast(f'Saved profile "{name}"'))
        self._refresh_monitors()

    def _activate_monitor_profile(self, slug: str) -> None:
        """Activation: one transaction, behind the one countdown (ADR-0015).

        Joins a countdown already open rather than opening a second clock (#192); the
        revert then puts back both rule lists and the active pointer, because activation
        touches workspace pins too, and a revert that left the refused profile's pins
        standing would be half a revert. Breaking edits still inside the debounce are
        dropped: the profile replaces the monitor list they were going to patch.
        """
        self._drop_pending_breaking()
        self._behind_countdown(
            lambda: self._session.activate_monitor_profile(slug), profile=True
        )

    def _update_monitor_profile(self, slug: str) -> None:
        """The drift badge's "Update": recapture reality into the profile."""
        if self._monitors_page is None:
            return
        if self._session.update_monitor_profile(slug, self._monitors_page.connected):
            self._refresh_monitors()

    def _detach_monitor_profile(self) -> None:
        """The drift badge's "Detach": keep the setup, stop tracking the profile."""
        self._session.detach_monitor_profile()
        self._refresh_monitors()

    def _delete_monitor_profile(self, slug: str) -> None:
        self._session.delete_monitor_profile(slug)
        self._refresh_monitors()

    def _refresh_monitors(self) -> None:
        """Re-fetch the connected outputs; the answer rebuilds the page.

        One rebuild, not two: `fetch_monitors` always answers -- synchronously with
        `None` when nobody is listening, asynchronously with data when Hyprland is --
        and `set_connected` rebuilds on either, so refreshing here first would pay for
        every edit twice.
        """
        self._refresh_entity_pages(frozenset({"monitors"}))

    def _set_connected(self, monitors: tuple[Mapping[str, Any], ...] | None) -> None:
        if self._monitors_page is not None:
            self._monitors_page.set_connected(monitors)

    def sync(self) -> None:
        """Make every control agree with the model, and the Banner with the session's health.

        One Banner for every unhealthy state there is (ADR-0016 §Surfacing) -- no compositor,
        config errors, an Entrypoint refusal, an active Quarantine. Which sentence wins is
        `Session.health`'s judgement, not this method's: it is a decision with rules worth
        testing, and a window is the one place in this app a test cannot reach without a
        display.
        """
        for page in self._pages:
            page.refresh()
        # A foreign reload lands here, and Hyprland reloads when a `require`d file such as
        # `user.lua` changes: the Scripting inventory re-reads with it, its plugin list too.
        if self._scripting_page is not None:
            self._scripting_page.refresh()
        # The Color source is read off the Entrypoint, which a transaction or a foreign
        # reload can have changed: the Theming Page reads it again (ADR-0014).
        if self._theming_page is not None:
            self._theming_page.refresh()
        self._draw_entity_pages(self._moved_entities())
        # Always, not only when an Entity list moved: the Scripting count follows `user.lua`.
        self._sync_entity_counts()

        self.sync_banner()
        self._sync_undo_action()
        # A result list on screen follows the model too: an undo or a foreign reload must
        # not leave a row that opens something no longer there (settled S2b).
        self._finder.requery()

    def sync_banner(self) -> None:
        """Make the one Banner agree with `Session.health`, and nothing else.

        Split out of `sync` because it runs on a different schedule. Every finished Apply
        transaction can change the health -- that is how a rejected write raises the Banner --
        but a full `sync` refreshes all 353 Rows, which is far too much to spend per apply on
        a config that is usually fine.
        """
        if self._offered is not None:
            # ADR-0009's own banner, which outranks ADR-0016's health states while it
            # applies: the app is read-only for a reason the user can act on right here.
            self._banner.set_title(READ_ONLY_REASON[self._offered.kind])
            self._banner.set_revealed(True)
            self._banner.set_button_label("Convert...")
            self._banner.remove_css_class(SEVERE_BANNER_CLASS)
            return

        health = self._session.health
        self._banner.set_title(health.title)
        self._banner.set_revealed(health.unhealthy)
        # libadwaita shows the button whenever the label is non-empty, so clearing it is how
        # a Banner with nothing to open loses its button rather than keeping a dead one.
        self._banner.set_button_label(health.button or "")
        # ADR-0016's red Banner, for the states where the config is not doing what the user
        # believes it is: an Entrypoint refusal, no keybinds, or a recovery that gave up.
        if health.severe:
            self._banner.add_css_class(SEVERE_BANNER_CLASS)
        else:
            self._banner.remove_css_class(SEVERE_BANNER_CLASS)

    def show_result(self, result: ApplyResult) -> None:
        """What a finished Apply transaction changes about the view.

        Three things, and the first happens whether or not the write worked: a restart-flagged
        key that reached the file wants its "Pending restart" pill, and a key that was
        refused wants no pill at all (`ApplyResult.pending_restart` only names keys whose
        bytes actually landed -- ADR-0010).

        Then the Banner, which is the load-bearing one: a rejected write is exactly how the
        app's own transaction discovers the config is broken, and without this the Banner
        would only ever appear on a startup or a foreign reload -- leaving the state ADR-0016
        exists to surface invisible in the one case the user just caused.

        Then a failure toast, but only for a failure the Banner has nothing to say about.
        ADR-0016 keeps toasts for transient events, "never a persistent unhealthy state, which
        is the Banner's", and both kinds of failure the Banner *does* carry are excluded
        here: a config error, which belongs to the Banner and its dialog because they can
        offer to fix it, and a read-back mismatch, which raises the Banner and badges its
        Row. What is left for a toast is the handful of failures that never reached the
        compositor at all -- a refused write, a full disk -- which would otherwise happen in
        silence.

        A *successful* transaction gets no toast: instant apply's whole promise is that the
        change is the feedback (ADR-0003), and the offer to undo it arrives separately through
        `offer_undo`. One toast, never two: a failure withdraws any offer still on screen,
        because a change that did not land is not a change to take back.
        """
        # Both the keys that gained a "Pending restart" pill and the ones that gained -- or
        # just lost -- a "Didn't apply" one. The losers matter as much: a badge left on a key
        # that has since applied would be the app reporting a failure that is over.
        for name in {*result.pending_restart, *result.keys}:
            self._refresh_chrome_for(name)
        self._sync_undo_action()
        self.sync_banner()
        # The transaction's reload may have loaded or unloaded a plugin: ask again (#174).
        if self._scripting_page is not None:
            self._scripting_page.plugins.refresh()

        if not result.ok:
            self._dismiss_undo()
        if not result.ok and not result.errors and not result.mismatches:
            toast = plain_toast(_result_summary(result), timeout=5)
            self._result_toast = toast
            self._toasts.add_toast(toast)

    def show_revert(self, revert: AutoRevert) -> None:
        """The app has just taken back its own rejected write (ADR-0016 §Auto-revert).

        The one event the ADR reserves a toast for outright, because it is the only time the
        UI changes without the user having asked: they made a change, Hyprland refused it,
        and the Row has moved back on its own. **Details** carries the `configerrors` lines,
        which by now exist nowhere else -- the restore transaction's own reload cleared the
        compositor's copy.
        """
        self._dismiss_undo()
        toast = plain_toast(_revert_summary(revert), timeout=8)
        if revert.errors:
            toast.set_button_label("Details")
            # The same dialog the Banner opens, with no actions on it. By the time this can
            # be clicked the app has already put the file back, so every recovery it could
            # offer would be a recovery from a recovery.
            toast.connect(
                "button-clicked", lambda *_: error_dialog(self, recovery_plan(revert.errors))
            )
        self._toasts.add_toast(toast)

    def show_refused(self, what: str, module: str) -> Adw.Toast:
        """A change refused because its file was edited outside the app (ADR-0005), named
        with that file. Returned for tests.

        A toast because it answers the gesture just made, and the Banner stays up after it
        times out (`Health.edited_files`): every later change to that file is refused the
        same way until the user decides. Withdraws any undo offer: nothing here was saved.
        """
        self._dismiss_undo()
        self.sync_banner()
        name = module.rsplit("/", 1)[-1]
        said = f"{what} was not saved: {name} was edited outside this app"
        self._refused_sentence = f"{said}. Cancel, then choose Details on the banner."
        toast = plain_toast(said, timeout=8)
        toast.set_button_label("Details")
        toast.connect("button-clicked", lambda *_: self.show_edited_file(module, what))
        self._toasts.add_toast(toast)
        return toast

    def show_not_saved(self, what: str, why: str) -> Adw.Toast:
        """An editor's save refused because the list moved under it (#225): `why` is the
        session's clause. Returned for tests; withdraws any undo offer, as nothing was
        saved."""
        self._dismiss_undo()
        said = f"{what} was not saved: {why}"
        # The toast stays one line; the editor, where the user is, says when and what next.
        moved = " while this editor was open. Cancel, then edit it again from the list"
        self._refused_sentence = f"{said}{moved if why == HELD_ENTRY_MOVED else ''}."
        toast = plain_toast(said, timeout=8)
        self._toasts.add_toast(toast)
        return toast

    def show_edited_file(
        self, module: str, what: str | None = None, *, then: tuple[str, ...] = ()
    ) -> Adw.AlertDialog:
        """A file edited outside the app, and the three ways on. Returned for the UI tier.

        Keep (the default: nothing changes and the Banner lets the file go), open it to make
        the change by hand, or replace it with the app's version after keeping a copy of it;
        the change is then made again. `then` is the Banner's other edited files, each
        offered in turn once this one is answered.
        """
        name = module.rsplit("/", 1)[-1]
        refused = f"{what} was not saved" if what else "Changes to it are not saved"
        copies = self._session.edited_copies_shown
        dialog = Adw.AlertDialog(
            heading=f"{name} was edited outside this app",
            body=(
                f"This app does not overwrite a file you have edited yourself. {refused}.\n\n"
                f"Replace the file with the app's version, then make the change again: "
                f"replacing keeps a copy of your edited file in {copies}. Or open the file "
                f"and make the change there."
            ),
        )
        dialog.add_response("keep", "Keep my file")
        dialog.add_response("open", "Open file")
        dialog.add_response("replace", "Replace file")
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")
        dialog.connect("response", self._on_edited_file_response, module, then)
        dialog.present(self)
        return dialog

    def _on_edited_file_response(
        self, _dialog: Adw.AlertDialog, response: str, module: str, then: tuple[str, ...]
    ) -> None:
        # Answered once: closing the dialog emits its close response again (hand-test 19),
        # which offered the next file twice.
        _dialog.disconnect_by_func(self._on_edited_file_response)
        name = module.rsplit("/", 1)[-1]
        if response == "open":
            self._launch_file(self._session.edited_file_path(module))
        elif response == "replace":
            said = replaced_sentence(
                self._session.replace_edited_file(module),
                name,
                self._session.edited_copies_shown,
            )
            self._toasts.add_toast(plain_toast(said, timeout=8))
        else:
            self._session.keep_edited_file(module)
        self.sync_banner()
        if then:
            GLib.idle_add(lambda: self.show_edited_file(then[0], then=then[1:]) and False)

    def show_notice(self, notice: Notice) -> Adw.Toast:
        """One of ADR-0012's one-time notices: a release removed or renamed settings.

        A toast rather than the Banner: neither is a fault, and the Banner is for a state the
        user has to act on (ADR-0016). **Details** lists the settings. The notice counts as
        seen when the toast goes away -- timeout, button or close -- and not when it is
        raised, so one queued behind other toasts when the app quits comes back next start.
        Returned for the UI tier.
        """
        toast = plain_toast(notice_title(notice), timeout=NOTICE_TOAST_SECONDS)
        toast.set_button_label("Details")
        toast.connect("button-clicked", lambda *_: self.notice_details(notice))
        toast.connect("dismissed", lambda *_: self._session.notice_seen(notice))
        self._toasts.add_toast(toast)
        return toast

    def notice_details(self, notice: Notice) -> Adw.AlertDialog:
        """The settings a notice is about. Returned for the UI tier."""
        return notice_dialog(self, notice, self._session.schema)

    # --- recovery (ADR-0016) ------------------------------------------------------------------

    def _on_banner_clicked(self, _banner: Adw.Banner) -> None:
        """The Banner's one button: convert, open the errors, or lift a Quarantine.

        Three jobs on one button because there is only one Banner and the states are mutually
        exclusive in practice -- a Quarantine the user has already fixed has no errors left to
        show, and a config that is erroring has something more urgent to offer than a toggle.
        `Health.button` is what decides which, and it is the same object that wrote the label.
        """
        if self._offered is not None:
            self.show_migration()
            return

        health = self._session.health
        if health.recovery.unhealthy:
            self.show_errors()
            return
        if health.edited_files:
            first, *rest = health.edited_files
            self.show_edited_file(first, then=tuple(rest))
            return
        # One call for all of them: two releases would be two Entrypoint rewrites racing
        # each other through the queue.
        if health.quarantined:
            self._session.release_quarantine(*health.quarantined)

    def show_errors(self) -> Adw.AlertDialog:
        """Open the one error dialog over the current problems. Returned for the UI tier."""
        return error_dialog(
            self,
            self._session.recovery,
            on_action=self._on_recovery_action,
            restorable=self._session.restorable,
            unverified_import=self._session.unverified_since_import,
        )

    def _on_recovery_action(self, action: Action, problem: Problem) -> None:
        """Perform one of the dialog's per-class actions.

        A dispatch table and nothing else. Which actions a problem offers is the matrix's
        decision (`recovery.py`), performing them is the session's, and this is only the wire
        between the button and the method -- so a window cannot invent a recovery the ADR
        does not sanction.
        """
        if action is Action.OPEN_FILE:
            self._open_file(problem)
        elif action is Action.RESTORE_LAST_GOOD and problem.module is not None:
            self._confirm_restore(problem.module)
        elif action is Action.REGENERATE:
            self._session.regenerate_entrypoint()
        elif action is Action.QUARANTINE:
            self._confirm_quarantine(problem)

    def _confirm_restore(self, module: str) -> Adw.AlertDialog:
        """Ask before putting a file back, then say how it went (#148 hand-test 17).

        It overwrites the file as it is now -- usually somebody's hand edit -- so it asks,
        keeps a copy, and reports the outcome rather than closing on silence.
        """
        name = module.rsplit("/", 1)[-1]
        dialog = Adw.AlertDialog(
            heading=f"Restore {name}?",
            body=(
                f"{name} goes back to the last version this app wrote and Hyprland "
                f"accepted. A copy of the file as it is now is kept in "
                f"{self._session.edited_copies_shown}."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "restore":
                return

            def done(ok: bool) -> None:
                self._toast(
                    f"{name} is back to the last version Hyprland accepted."
                    if ok
                    else f"{name} could not be restored. The Banner says what is wrong."
                )

            if not self._session.restore_last_good(module, done=done):
                self._toast(f"{name} could not be restored: there is no earlier version.")

        dialog.connect("response", answered)
        dialog.present(self)
        return dialog

    def _confirm_quarantine(self, problem: Problem) -> None:
        """Ask before disabling somebody else's file (ADR-0016 §Quarantine).

        The consent gate, and the ADR is explicit that there is one: the app is about to stop
        loading a file the user wrote, and doing that silently would be indistinguishable
        from the app having broken it. The dialog says what will happen and that it is one
        click to undo.
        """
        require = self._session.quarantine_target(problem)
        if require is None:
            return

        name = f"{require}.lua"
        dialog = Adw.AlertDialog(
            heading=f"Disable {name} until it is fixed?",
            body=(
                f"Hyprland will stop loading {name}, so the rest of your config can work "
                f"again. The file is not changed, and you can turn it back on at any time."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("disable", f"Disable {name}")
        dialog.set_response_appearance("disable", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_quarantine_response, require)
        dialog.present(self)

    def _on_quarantine_response(
        self, _dialog: Adw.AlertDialog, response: str, require: str
    ) -> None:
        if response == "disable":
            self._session.quarantine(require)

    def _open_file(self, problem: Problem) -> None:
        """Hand the broken file to whatever opens `.lua` on this machine.

        The file, not the file-and-line: there is no portable way to tell an arbitrary
        desktop handler to jump to a line. The dialog carries the `file:line` prefix verbatim
        for exactly that reason -- ADR-0016 keeps the lines unreworded because they are "what
        they paste into an editor's go-to-line box".
        """
        path = self._session.file_for(problem)
        if path is None:
            return
        self._launch_file(path)

    def _launch_file(self, path: Path) -> None:
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self, None, None)

    # --- plugins (#174) -------------------------------------------------------------------

    def _add_plugin(self) -> None:
        """Pick a `.so` and append it. hyprpm is out of scope: the file must exist already."""
        shared = Gtk.FileFilter(name="Plugins (.so)")
        shared.add_suffix("so")
        anything = Gtk.FileFilter(name="All files")
        anything.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(shared)
        filters.append(anything)
        dialog = Gtk.FileDialog(title="Add plugin", filters=filters, default_filter=shared)

        def finished(source: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
            try:
                chosen = source.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if chosen is not None and chosen.get_path():
                self.add_plugin_path(chosen.get_path())

        dialog.open(self, None, finished)

    def add_plugin_path(self, path: str) -> None:
        """Append `path`, or say why not: a second entry for one `.so` is refused."""
        plugin = PluginLoad(path)
        if any(each.path == path for each in self._session.declarations("plugins")):
            name = path.rsplit("/", 1)[-1] or path
            self._toasts.add_toast(plain_toast(f"{name} is already in the list"))
            return
        if self._session.add_declaration("plugins", plugin):
            self._refresh_plugins()

    def _remove_plugin(self, index: int) -> None:
        if self._session.remove_declaration("plugins", index):
            self._refresh_plugins()

    def _set_plugin_enabled(self, index: int, enabled: bool) -> None:
        def flip(items: list[Any]) -> None:
            if 0 <= index < len(items):
                items[index] = replace(items[index], enabled=enabled)

        verb = "enabled" if enabled else "disabled"
        if self._session.edit_declarations(
            "plugins", flip, title=entity_title("plugins", verb)
        ):
            self._refresh_plugins()

    def _move_plugin(self, index: int, to: int) -> None:
        if self._session.move_declaration("plugins", index, to):
            self._refresh_plugins()
            if self._scripting_page is not None:
                self._scripting_page.plugins.focus(to)

    def _refresh_plugins(self) -> None:
        # `sync` refreshes the Scripting Page, its plugin list included.
        self.sync()

    # --- presets ----------------------------------------------------------------------------

    @property
    def colour_conflict(self) -> ColourConflictDialog | None:
        """The "Use <preset>'s colors?" question on screen, if one is. For the UI tier."""
        return self._colour_conflict

    def apply_preset(
        self,
        slug: str,
        *,
        wallpaper: bool = False,
        colors: ColorChoice | None = None,
        remember: bool = False,
    ) -> PresetApplyResult:
        """Apply a Preset, asking first whose colours win while a wallpaper sets them.

        The Presets group's Apply. A remembered answer is used without asking; otherwise
        the question is a dialog and the Preset applies once it is answered. `wallpaper` is
        the group's "change / keep mine". A refusal is said as a toast; what the wallpaper
        part could not do arrives through `show_preset_note`.
        """
        if remember and colors is not None:
            self._remember_colours(colors)
        if colors is None:
            colors = remembered_choice(self._prefs.remembered)
        result = self._session.apply_preset(slug, colors=colors, wallpaper=wallpaper)
        match result:
            case PresetColorConflict(source):
                name = dict(self._session.presets()).get(slug)
                dialog = ColourConflictDialog(
                    name.name if name is not None else slug,
                    source,
                    on_choice=lambda choice, remember: self._answer_colours(
                        slug, wallpaper, choice, remember
                    ),
                )
                dialog.connect("closed", self._on_colour_conflict_closed)
                self._colour_conflict = dialog
                dialog.present(self)
            case PresetNotApplied(reason):
                named = dict(self._session.presets()).get(slug)
                toast = plain_toast(
                    f"{named.name if named is not None else slug} was not applied. {reason}"
                )
                self._toasts.add_toast(toast)
            case PresetApplied():
                pass
        return result

    def _answer_colours(
        self, slug: str, wallpaper: bool, choice: ColorChoice, remember: bool
    ) -> None:
        self.apply_preset(slug, wallpaper=wallpaper, colors=choice, remember=remember)
        self.sync()

    def _remember_colours(self, choice: ColorChoice) -> None:
        """Keep the answer to "whose colours win", so it is not asked again: the one place
        it is written, for the Apply dialog, the colour question and the import alike."""
        self._remember(self._prefs.with_remembered(COLOR_CONFLICT_DIALOG, choice.value))

    def _on_colour_conflict_closed(self, dialog: ColourConflictDialog) -> None:
        if self._colour_conflict is dialog:
            self._colour_conflict = None

    def show_preset_note(self, text: str) -> Adw.Toast:
        """What applying or undoing a Preset could not do, said as a toast. Returned for
        the UI tier."""
        toast = plain_toast(text, timeout=PRESET_NOTE_SECONDS)
        self._toasts.add_toast(toast)
        return toast

    # --- undo -------------------------------------------------------------------------------

    @property
    def undo_toast(self) -> Adw.Toast | None:
        """The undo offer currently on screen, if there is one.

        View state rather than an accessor for a private field: "is the app offering to undo
        right now?" is the whole visible consequence of a gesture landing, and the UI tier
        asserts on it exactly as it asserts on `pages`.
        """
        return self._undo_toast

    def offer_undo(self, step: Step) -> None:
        """Offer to take back the gesture that just landed.

        Told which gesture rather than reading the stack top, and the difference is visible:
        a transaction that recorded no step -- an undo's own write, or one whose bytes were
        already on disk -- would otherwise raise an offer for whatever gesture happened to be
        underneath, naming a Row the user has not touched for a while.
        """
        self._sync_undo_action()
        self._dismiss_undo()
        if isinstance(step, PresetStep) and self._result_toast is not None:
            # A Preset that stood with a key that did not take: its offer says what did not,
            # so the transaction still gets one toast (finding 13 of the #153 review).
            self._result_toast.dismiss()
        self._result_toast = None
        toast = plain_toast(self._gesture_title(step), timeout=UNDO_TOAST_SECONDS)
        toast.set_button_label("Undo")
        toast.connect("button-clicked", lambda *_: self._undo())
        toast.connect("dismissed", self._on_undo_toast_dismissed)
        self._undo_toast = toast
        self._toasts.add_toast(toast)

    def _dismiss_undo(self) -> None:
        toast, self._undo_toast = self._undo_toast, None
        if toast is not None:
            toast.dismiss()

    def _on_undo_toast_dismissed(self, toast: Adw.Toast) -> None:
        if self._undo_toast is toast:
            self._undo_toast = None

    def _undo(self) -> None:
        """Take back the last gesture. The session decides whether there is one.

        Except on the Displays: while a countdown shows, the newest gesture is the change on
        its clock, so Ctrl+Z is its Revert -- the step beneath cannot be undone from under the
        countdown without being lost (review of #151, finding 12 and owner call 4). A
        breaking edit still in its debounce is the newest gesture too, and goes unapplied.
        """
        self._dismiss_undo()
        if self._countdown is not None:
            # The Esc path: "revert" is the dialog's close response.
            dialog = self._countdown.dialog
            dialog.emit("response", "revert")
            dialog.force_close()
            return
        if self._drop_pending_breaking():
            self._refresh_monitors()
            self._sync_undo_action()
            return
        offered = self._session.can_undo
        step = self._session.last_gesture
        if _breaks_display(step):
            # Putting a mode back can black-screen as surely as choosing one (#192).
            undone = self._behind_countdown(self._session.undo)
        else:
            undone = self._session.undo()
        if undone:
            # `sync` runs on the session's own `on_state_changed` too, but the undo has
            # already moved the model and the Rows should not wait for the compositor to
            # confirm what the app is about to write.
            self.sync()
        elif (
            offered and not self._session.undo_queued and self._session.last_gesture is not step
        ):
            # An entity step whose list changed since -- a hand edit was adopted. The session
            # dropped it rather than write over that edit; say so, or Ctrl+Z looks dead. A
            # queued undo is not refused: it runs when the edit in flight lands. Nor is one
            # refused by an edited file (#148 hand-test 31): the step stays, and the
            # refusal's own toast has said why.
            self._toasts.add_toast(plain_toast("Can't undo that change any more"))
        self._sync_undo_action()

    def _sync_undo_action(self) -> None:
        """Ctrl+Z is live while there is a step to undo, or a display change to revert."""
        revertible = self._countdown is not None or bool(self._pending_breaking)
        self._undo_action.set_enabled(self._session.can_undo or revertible)

    def _refresh_entity_pages(self, kinds: frozenset[str]) -> None:
        """Re-render the Pages that show the Entity lists `kinds`, then `sync` the rest.

        For an edit the window made itself: those Pages are drawn even when their list did
        not move (a monitor profile is not an Entity list, and its Page shows it).
        """
        self._draw_entity_pages(kinds)
        self.sync()

    def _moved_entities(self) -> frozenset[str]:
        """The Entity lists that differ from what their Pages last drew: every one when the
        session went live or read-only since, since each row's controls follow that, or the
        cause the empty states and Save tooltips name moved (#269)."""
        lists = self._entity_lists()
        if bool(self._session.live) != self._shown_live or self._causes() != self._shown_causes:
            return frozenset(lists)
        return frozenset(
            kind for kind, items in lists.items() if self._shown_entities.get(kind) != items
        )

    def _causes(self) -> tuple[str | None, str | None]:
        return (self._session.offline_sentence, self._session.entities_unreadable)

    def _draw_entity_pages(self, kinds: frozenset[str]) -> None:
        """Rebuild the Pages showing `kinds` from the model, and every sidebar count.

        `sync` calls this with the lists that moved behind the window's back -- a foreign
        reload adopting a hand edit, the startup load, an edit's cascade into another list
        -- because rows are index-addressed: a stale row's Remove lands on another entity.
        """
        if not kinds:
            return
        if "curves" in kinds:
            # The animations that named a curve may now carry a dangling reference, so their
            # Page has to be rebuilt too -- the finding lives on a row nobody touched.
            kinds |= {"animations"}
        if kinds & KEYBIND_KINDS and self._binds_page is not None:
            self._binds_page.refresh()
        for kind in ("window", "layer"):
            rules_page = self._rules_page(kind)
            if f"{kind}_rules" in kinds and rules_page is not None:
                rules_page.refresh()
        if "workspace_rules" in kinds and self._workspace_rules_page is not None:
            self._workspace_rules_page.refresh()
        if "monitors" in kinds and self._monitors_page is not None:
            # The answer rebuilds the Page (`_refresh_monitors`).
            self._session.fetch_monitors(self._set_connected)
        for kind in kinds & self._declaration_pages.keys():
            self._declaration_pages[kind].refresh()
        # "plugins" needs nothing here: its list is on the Scripting Page, which `sync`
        # rebuilds every time, and `_refresh_entity_pages` ends in `sync`.

        lists = self._entity_lists()
        self._shown_entities.update((kind, lists[kind]) for kind in kinds if kind in lists)
        self._shown_live = bool(self._session.live)
        self._shown_causes = self._causes()
        self._sync_entity_counts()

    def _entity_lists(self) -> dict[str, tuple[Any, ...]]:
        return {kind: tuple(items) for kind, items in self._session.model.entities.kinds()}

    def _sync_entity_counts(self) -> None:
        """Make each Entity Page's sidebar count say how many entities it lists now."""
        counts: dict[str, int] = {}
        if self._binds_page is not None:
            counts[self._binds_page.section] = len(self._binds_page.binds)
        for page in (self._window_rules_page, self._layer_rules_page):
            if page is not None:
                counts[page.section] = len(page.rules)
        if self._workspace_rules_page is not None:
            counts[self._workspace_rules_page.section] = len(self._workspace_rules_page.rules)
        if self._monitors_page is not None:
            counts[MonitorsPage.section] = len(self._monitors_page.rules)
        for declarations in self._declaration_pages.values():
            counts[declarations.section] = len(declarations.entities)
        if self._scripting_page is not None:
            counts[self._scripting_page.section] = self._scripting_count()
        if self._theming_page is not None:
            counts[self._theming_page.section] = self._theming_page.set_up_count
        index = 0
        while (row := self._sidebar.get_row_at_index(index)) is not None:
            index += 1
            count = counts.get(row.get_name())
            badge = row.get_child().get_last_child() if count is not None else None
            if isinstance(badge, Gtk.Label):
                badge.set_label(str(count))

    def _on_undo(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        self._undo()

    def _gesture_title(self, step: Step) -> str:
        """What the undo toast calls the gesture it is offering to reverse.

        The Option's own title, because that is the word on the Row the user just changed --
        never the dotted key, which lives in the Help popover and the search index (ADR-0013).
        A gesture spanning several Options is counted rather than listed: the css-gaps editor
        writes four sides at once, and "Gaps in, Gaps in, Gaps in, Gaps in" is not a sentence.
        An Entity step carries its own title ("Bind removed"): only the session knew which
        of add, remove or reorder the gesture was. A Preset step names the Preset.
        """
        if isinstance(step, EntityStep):
            return step.title
        if isinstance(step, PresetStep):
            names = set(step.options.names) if step.options is not None else set()
            untaken = [
                _counted(
                    len(names & self._session.unconfirmed), "was not confirmed by Hyprland"
                ),
                _counted(len(names & self._session.overridden), "is overridden"),
                _counted(len(names & self._session.unapplied), "did not apply"),
            ]
            said = " ".join(f"{part}." for part in untaken if part)
            return f"Applied {step.name}. {said + ' ' if said else ''}Press Ctrl+Z to undo."
        titles = [self._session.schema[name].title for name in step.names]
        if len(titles) == 1:
            return f"{titles[0]} changed"
        return f"{len(titles)} settings changed"

    # --- search -------------------------------------------------------------------------

    @property
    def finder(self) -> Finder:
        """The sidebar's search surface. The UI tier drives and asserts against it."""
        return self._finder

    @property
    def hits(self) -> tuple[Hit, ...]:
        """The results the sidebar is listing. What the UI smoke tier asserts against."""
        return self._finder.hits

    @property
    def search_mode(self) -> bool:
        """Whether the finder is open."""
        return self._finder.active

    @property
    def revealed(self) -> frozenset[str]:
        """The Options a search hit is showing one-off, despite the Advanced switch."""
        return self._revealed

    @property
    def sidebar(self) -> Gtk.ListBox:
        """The nav list. The UI tier drives it to check the One-off reveal ends."""
        return self._sidebar

    @property
    def sidebar_mode(self) -> str:
        """Whether the sidebar is listing Pages (`nav`) or results (`results`)."""
        return self._sidebar_stack.get_visible_child_name() or NAV_MODE

    def _show_sidebar_mode(self, mode: str) -> None:
        """Swap the nav list for the results, or back -- the finder decides which.

        A stack rather than refilling one ListBox: the nav list holds the selection the
        content pane is showing, and refilling it with results would destroy that, so
        escaping a search would land the user on a different Page than the one they left.
        """
        self._sidebar_stack.set_visible_child_name(mode)

    def start_search(self) -> None:
        """Open the finder and put the cursor in it -- Ctrl+F, and the magnifier."""
        self._finder.start()

    def search(self, text: str) -> None:
        """Open the finder on `text`, as though it had been typed.

        The one entry point that does not depend on a keystroke reaching a widget, which is
        what makes the results assertable: driving this through synthesised key events would
        test the toolkit's event plumbing rather than the finder.
        """
        self._finder.search(text)

    def _on_search_action(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        self.start_search()

    def open_hit(self, hit: Hit) -> None:
        """Navigate to a search result: an Option's Row, or an Entity's row on its Page."""
        if isinstance(hit, EntityHit):
            self._open_entity_hit(hit)
        else:
            self._open_option_hit(hit)

    def _open_option_hit(self, hit: OptionHit) -> None:
        """Navigate to an Option's Row: the active View first, Config only if it must.

        ADR-0017's navigation rule, in the order it states it. A hit resolves against the
        View the user chose; the switch to Config happens only when the Row has no home
        there at all -- the `hidden` tier while in Tasks -- and when it does happen it is
        the *ordinary* View switch, remembered like any manual toggle, because a temporary
        hidden view state is the thing that ADR rejected.

        The One-off reveal then covers the other reason a Row may not exist: the Advanced
        switch. Recorded before the rebuild rather than after, since it is an input to the
        plan the rebuild works from.
        """
        if not self._has_home(hit.option):
            self.set_view(View.CONFIG)

        self._revealed = frozenset({hit.name})
        self.rebuild()
        self.reveal_option(hit.name, flash_row=True)

    def _open_entity_hit(self, hit: EntityHit) -> None:
        """Open the hit's Page and flash its row -- or say it changed, never land elsewhere.

        The hit is re-resolved first (settled S2b): the entity may have moved since the
        results were listed, or be gone. Gone is said in a toast over a refreshed list, so
        the user sees why nothing opened and what the list holds now; a reveal by the old
        position would open a different bind, which is worse than opening nothing.

        Every Entity Page is in both Views, so there is no View fallback here. An Option's
        One-off reveal still standing ends, as any navigation ends it. The reveal waits a
        low-priority turn for the reason `reveal_option` gives: a `GtkStack` lays out only
        its visible child, so the Page has no geometry to scroll until then.
        """
        position = resolve(hit, self._session)
        if position is None:
            self._entity_changed()
            return
        self._end_one_off_reveal()
        key: int | str = position
        if hit.kind is EntityKind.MONITOR_RULE:
            key = self._session.monitor_rules[position].output
        elif hit.kind is EntityKind.WORKSPACE_RULE:
            key = self._session.workspace_rules[position].workspace
        elif hit.kind is EntityKind.MONITOR_PROFILE:
            key = self._session.monitor_profiles()[position][0]
        elif hit.kind is EntityKind.PRESET:
            key = self._session.presets()[position][0]
        self._select_section(entity_page_id(hit.kind.page_kind))
        GLib.idle_add(self._reveal_entity, hit.kind, key, priority=GLib.PRIORITY_LOW)

    def _reveal_entity(self, kind: EntityKind, key: int | str) -> bool:
        """Flash the entity's row on its Page and scroll it into view explicitly.

        Explicitly because the Pages reveal by focus, and an insensitive row -- every row
        of a read-only session -- cannot take focus, so focus alone would scroll nowhere.
        """
        row = self._entity_row(kind, key)
        if row is None:
            self._entity_changed()
        else:
            _scroll_when_laid_out(row)
        return False

    def _entity_row(self, kind: EntityKind, key: int | str) -> Gtk.Widget | None:
        """The Page's reveal for one entity: its row, flashed, or `None` when it has none."""
        match kind:
            case EntityKind.BIND:
                page = self._binds_page
                return page.reveal(key) if page is not None and isinstance(key, int) else None
            case EntityKind.WINDOW_RULE | EntityKind.LAYER_RULE:
                rules = self._rules_page(
                    "window" if kind is EntityKind.WINDOW_RULE else "layer"
                )
                return rules.reveal(key) if rules is not None and isinstance(key, int) else None
            case EntityKind.WORKSPACE_RULE:
                workspaces = self._workspace_rules_page
                return workspaces.reveal(str(key)) if workspaces is not None else None
            case EntityKind.MONITOR_RULE:
                monitors = self._monitors_page
                return monitors.reveal_rule(str(key)) if monitors is not None else None
            case EntityKind.MONITOR_PROFILE:
                monitors = self._monitors_page
                return monitors.reveal_profile(str(key)) if monitors is not None else None
            case EntityKind.PRESET:
                theming = self._theming_page
                return theming.reveal_preset(str(key)) if theming is not None else None

    def reveal_backend(self, tool: str) -> None:
        """Open the Theming Page on `tool` and flash it: a "Set by <tool>" pill's click (#165).

        Always lands (settled S8): matugen and wallust on their tab, another tool on its
        "Other tools" row, an unknown one on the Page's top. The scroll waits a turn for
        the reason `reveal_option` gives.
        """
        page = self._theming_page
        if page is None:
            return
        self._end_one_off_reveal()
        self._select_section(page.section)

        def reveal() -> bool:
            target = page.reveal_backend(tool)
            if target is not None:
                _scroll_when_laid_out(target)
            return False

        GLib.idle_add(reveal, priority=GLib.PRIORITY_LOW)

    def _toast(self, text: str) -> None:
        """A short message: plain text, since a tool's name or a path may hold `&`."""
        self._toasts.add_toast(plain_toast(text, timeout=4))

    def _entity_changed(self) -> None:
        """A hit whose entity is gone: refresh the list and say so, rather than fail quietly."""
        self._finder.requery()
        self._toasts.add_toast(plain_toast(ENTITY_CHANGED, timeout=4))

    def _end_one_off_reveal(self) -> None:
        """The visit is over: the user navigated somewhere themselves.

        Rebuilding is what actually takes the Row back out -- clearing the set alone would
        leave a withheld Row standing on a Page the Advanced switch says should not have it,
        until something else happened to rebuild. Guarded, because the common case is that
        there is no reveal outstanding and rebuilding 353 Rows per sidebar click would be an
        expensive way to do nothing.
        """
        if not self._revealed:
            return
        self._revealed = frozenset()
        self.rebuild()

    def _has_home(self, option: ResolvedOption) -> bool:
        """Whether this Option can appear in the active View at any switch setting.

        Asked of `plan.is_visible` with the switch forced on rather than by testing the tier
        here: the rule about which tiers a View admits is ADR-0013's and lives there, and a
        second copy of it in the shell is one that would not be updated together with it.
        """
        return is_visible(option, Disclosure(show_advanced=True, view=self.view))

    def reveal_option(self, name: str, *, flash_row: bool = False) -> None:
        """Show the Row for one Option and put the keyboard on it.

        What the Dependency badge does when clicked (ADR-0013 §3): "Requires Snap floating
        windows" is only useful if it takes you to that switch. Focusing rather than merely
        selecting the Section is what scrolls it into view -- and it leaves the user on the
        control they came to change.

        Silent when the Option has no Row right now, which is the Advanced switch hiding it.
        Revealing it anyway is the one-off reveal of ADR-0017, and it arrives with Search
        (#67); a badge that scrolled to nothing would be worse than one that does nothing.

        Putting it on screen waits a turn of the loop, and that is not a hedge: a `GtkStack`
        allocates only its visible child, so until the Section switch above has been laid
        out the Page's scroller honestly reports a height of zero and any scroll into it is
        a no-op (measured -- upper goes 0 -> 5846 across one idle).
        """
        found = self._find(name)
        if found is None:
            return
        page, row = found
        self._select_section(page.plan.section)
        GLib.idle_add(self._put_on_screen, row, flash_row, priority=GLib.PRIORITY_LOW)

    def _put_on_screen(self, row: OptionRow, flash_row: bool = False) -> bool:
        """Scroll a Row into view and leave the keyboard on it.

        Both, because neither is enough alone: an *insensitive* widget cannot take focus --
        precisely the case a dependency badge navigates away from, and the case of every Row
        on a read-only session -- so the explicit scroll is what makes the reveal work at
        all. The focus is what leaves the user on the control they came to change.
        """
        _scroll_into_view(row.widget)
        if not row.control.grab_focus():
            row.widget.grab_focus()
        if flash_row:
            # ADR-0017's "flash-highlighted". A search hit lands the user on a Page they did
            # not choose, several screens down: the scroll alone tells them nothing about
            # *which* of the Rows now on screen is the one they searched for.
            flash(row.widget)
        return False

    # --- signals ------------------------------------------------------------------------------

    def _on_option_edited(self, name: str) -> None:
        """One Option was written. Re-decide the chrome of every Row that can have moved.

        Exactly two can: the edited Row, whose reset arrow appears the moment the model
        holds a value, and the Rows gated on it, whose Dependency badge and control
        sensitivity turn on the value that just changed. Refreshing those rather than all
        353 keeps this cheap enough to run on the per-keystroke edits a spin button emits,
        and it deliberately does not touch controls -- see `ConfigPage.refresh_chrome`.
        """
        self._refresh_chrome_for(name)
        for dependent in self._dependents.get(name, ()):
            self._refresh_chrome_for(dependent)

    def _refresh_chrome_for(self, name: str) -> None:
        found = self._find(name)
        if found is not None:
            found[1].chrome.refresh()

    def _find(self, name: str) -> tuple[ConfigPage, OptionRow] | None:
        """The Page and Row for one Option name, or `None` when no Page built it.

        `None` is the ordinary case, not an error: the Advanced switch withholds a quarter
        of the Schema, and both callers -- refreshing chrome after an edit, and revealing a
        Row a badge names -- have nothing to do about a Row that is not on screen.
        """
        for page in self._pages:
            row = page.row(name)
            if row is not None:
                return page, row
        return None

    def _on_section_selected(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        section = row.get_name()
        if self._scripting_page is not None and section == self._scripting_page.section:
            # Showing the Page re-reads the files: "open in editor, save, come back" must
            # not need a reload or a restart to show what was just written.
            self._scripting_page.refresh()
        if self._theming_page is not None and section == self._theming_page.section:
            # A tool installed or a config edited while the app ran shows on arrival.
            self._theming_page.refresh()
        self._stack.set_visible_child_name(section)
        self._content_page.set_title(self._page_title(section))
        self._split.set_show_content(True)

    def _page_title(self, section: str) -> str:
        """The heading for the selected Page: the Page's own title, else the Schema's.

        Entity Pages are not Sections, so asking the Schema about one gets a *derived*
        title -- the raw id with its first letter capitalised. That reads as "Monitors"
        where the Page says "Displays", and after #70's `entity:` namespacing it reads as
        "Entity:animations", which is an internal id on screen. The Page already knows what
        it is called; the Schema only has to answer for Sections.
        """
        title = self._section_titles.get(section)
        return title if title is not None else self._session.schema.section_title(section)

    def _on_toggle_advanced(self, action: Gio.SimpleAction, _parameter: Any) -> None:
        action.set_state(GLib.Variant.new_boolean(not self.show_advanced))
        self._remember(self._prefs.with_show_advanced(self.show_advanced))
        self.rebuild()

    def _on_close_request(self, *_: Any) -> bool:
        """Hold the window open until the session has flushed and let go of its sockets.

        An edit made in the last moment before closing is still inside the apply debounce.
        Letting the window go first would drop it, and the user watched it land in the UI.
        """
        if self._closing:
            return False
        self._closing = True
        # A display change still on its clock must not outlive the window that could have
        # kept it: closing is one more way out that is not the Keep button (ADR-0008).
        self._drop_pending_breaking()
        if self._countdown is not None:
            dialog = self._countdown.dialog
            dialog.emit("response", "revert")
            dialog.force_close()
        self._session.close(self._destroy_and_release)
        return True

    def _destroy_and_release(self) -> None:
        self.destroy()
        release(self)

    # --- helpers ------------------------------------------------------------------------

    def _selected_section(self) -> str | None:
        row = self._sidebar.get_selected_row()
        return None if row is None else row.get_name()

    def _select_section(self, section: str) -> None:
        """Select the sidebar row named `section`, wherever in the list it sits.

        Walks until the list runs out rather than stopping at `len(self._pages)`: the
        Schema Pages are only the *first* stretch of the sidebar, and every Entity Page --
        binds, the rule lists, Displays, and the seven of #70 -- is appended after them.
        Bounding the search by the Schema Page count made every one of them unreachable by
        name, which is the path a restored selection and a search hit both take.
        """
        index = 0
        while (row := self._sidebar.get_row_at_index(index)) is not None:
            if row.get_name() == section:
                self._sidebar.select_row(row)
                return
            index += 1


_REVEAL_MARGIN = 24.0
"""A little air above a revealed Row, so it reads as "here it is" rather than as a Page
that happens to start there."""


def _scroll_into_view(row: Gtk.Widget) -> None:
    """Put `row` on screen inside whatever scroller holds it.

    Focus alone is not enough, and the reason is worth recording: focusing a widget scrolls
    it into view, but an *insensitive* widget cannot be focused at all -- which is precisely
    the case a dependency badge navigates away from. Doing the scroll explicitly makes the
    reveal independent of whether anything could take focus.

    Silent when the widget has no allocation yet (nothing has been laid out, so there is no
    honest answer to "where is it?") -- the reveal then simply lands on the Section.
    """
    scroller = row.get_ancestor(Gtk.ScrolledWindow)
    if scroller is None:
        return
    child = scroller.get_child()
    if child is None:
        return

    ok, point = row.compute_point(child, Graphene.Point().init(0.0, 0.0))
    if not ok:
        return

    # `point.y` is measured against the viewport, which is already scrolled -- so it is an
    # offset from where the view currently sits, not from the top of the content. Adding the
    # adjustment back is what turns it into the absolute position to scroll to.
    adjustment = scroller.get_vadjustment()
    target = adjustment.get_value() + point.y - _REVEAL_MARGIN
    highest = max(adjustment.get_upper() - adjustment.get_page_size(), adjustment.get_lower())
    adjustment.set_value(min(max(target, adjustment.get_lower()), highest))


def _scroll_when_laid_out(row: Gtk.Widget) -> None:
    """`_scroll_into_view`, once the row has been allocated.

    An Entity Page shown for the first time has no geometry until the frame after the
    switch, and an idle can run before that frame: the scroll then measures a zero-height
    page and stays at the top while the flash plays off screen (probed over end-4's 197
    binds). A row with a height has been laid out; one without waits for the frame clock,
    which ticks only while the row is mapped -- on the Page the hit just opened.
    """
    if row.get_height() > 0:
        _scroll_into_view(row)
        return

    def tick(widget: Gtk.Widget, _clock: object) -> bool:
        if widget.get_height() == 0:
            return GLib.SOURCE_CONTINUE
        _scroll_into_view(widget)
        return GLib.SOURCE_REMOVE

    row.add_tick_callback(tick)


def _dependents(schema: Schema) -> dict[str, tuple[str, ...]]:
    """Controlling Option -> the Options whose `depends_on` names it.

    The reverse of what the Schema stores, built once at startup: an edit has to find the
    Rows it just enabled or disabled, and walking 353 Options per keystroke to answer that
    is the kind of thing that only shows up as a laggy slider.
    """
    reverse: dict[str, list[str]] = {}
    for option in schema:
        if option.depends_on is not None:
            reverse.setdefault(option.depends_on.option, []).append(option.name)
    return {name: tuple(dependents) for name, dependents in reverse.items()}


def _scrolled(page: Adw.PreferencesPage) -> Gtk.ScrolledWindow:
    return Gtk.ScrolledWindow(child=page, hscrollbar_policy=Gtk.PolicyType.NEVER)


@dataclass(slots=True)
class _DisplayCountdown:
    """The one Confirm-or-revert countdown (S3 of #151): what Revert restores, and how.

    `snapshot` is taken when the countdown opens, before its first change. `group` holds
    every monitor and workspace-rule step made while it runs, so Keep ends it as one step
    and Revert, committed inside it, leaves none or only the benign edits that survived.
    `includes_profile` turns Revert from field-aware into whole.
    """

    snapshot: MonitorStateSnapshot
    group: UndoGroup
    dialog: ConfirmRevertDialog
    includes_profile: bool = False


def _breaks_display(step: Step | None) -> bool:
    """Whether undoing `step` would change a display-breaking monitor field."""
    return isinstance(step, EntityStep) and any(
        edit.kind == "monitors" and breaks_display(edit.after, edit.before)
        for edit in step.edits
    )


@dataclass(frozen=True, slots=True)
class SidebarEntry:
    """One built Page, as the sidebar needs to know it: where to go, and what to call it.

    A type rather than the `(str, str, int)` tuple this began as -- three same-shaped
    strings and an int, indexed positionally, is exactly the clump the repo's other plan
    objects are dataclasses to avoid. `section` is the `GtkStack` child name, which is also
    what a sidebar row is named and what `_select_section` navigates by.
    """

    section: str
    title: str
    count: int


def _view_from(value: str) -> View:
    """A stored or action-supplied view name, degraded to the default if unrecognised.

    The Prefs file is plain JSON a user can edit and an older app can have written, so an
    unknown name has to open *something*. Tasks is that something (#7); raising here would
    turn a stale preference into an app that cannot start.
    """
    try:
        return View(value)
    except ValueError:
        return View.TASKS


def _theme_from(value: str) -> str:
    """A stored or action-supplied Theme override name, degraded to System if unrecognised.

    As `_view_from` for views: a name from a newer app or a hand edit opens the app in the
    platform's own scheme rather than failing to start.
    """
    return value if value in _SCHEMES else "system"


def _category_heading(title: str) -> Gtk.ListBoxRow:
    """A category label in the Tasks sidebar: visible, never selectable.

    Not selectable and not activatable because it has no Page behind it -- a heading that
    took the selection would blank the content pane. `_select_section` walks rows by name
    and this one has none, so it is invisible to navigation as well as to the pointer.
    """
    label = Gtk.Label(
        label=title,
        xalign=0.0,
        css_classes=["heading", "dim-label"],
        margin_top=12,
        margin_bottom=2,
        margin_start=6,
    )
    row = Gtk.ListBoxRow(child=label, selectable=False, activatable=False)
    return row


def _sidebar_row(section: str, title: str, count: int) -> Gtk.ListBoxRow:
    """One Section in the sidebar, with how many Options are on its Page.

    The count is the design canvas's "43 options" chip moved to the sidebar, and it earns
    its place twice: it is how the empty Sections announce themselves when the Advanced
    switch is off, without the user having to open each one to find out.
    """
    label = Gtk.Label(label=title, xalign=0.0, hexpand=True)
    badge = Gtk.Label(label=str(count), css_classes=["dim-label", "numeric"])

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.append(label)
    box.append(badge)

    row = Gtk.ListBoxRow(child=box, name=section)
    row.set_tooltip_text(section)
    return row


#: What each unhappy `ApplyOutcome` means to a person. The enum's own spelling is a wire
#: value -- "read-back-mismatch" is not a sentence to show a user. This is the *toast* line
#: for a transaction that failed without config errors; anything with `configerrors` behind it
#: goes to the Banner and its dialog instead.
_FAILURE_TEXT = {
    ApplyOutcome.CONFIG_ERRORS: "Hyprland rejected the change.",
    ApplyOutcome.READ_BACK_MISMATCH: "The change was written but did not take effect.",
    ApplyOutcome.TIMEOUT: "Hyprland did not confirm the change.",
    ApplyOutcome.COMPOSITOR_GONE: "Hyprland stopped responding; the change is saved but "
    "not applied.",
    ApplyOutcome.WRITE_FAILED: "The settings file could not be written.",
    ApplyOutcome.ABORTED: "The change was refused before anything was written.",
}


def _counted(count: int, verb: str) -> str:
    """ "1 setting is overridden", "2 settings are overridden"; empty for none."""
    if count == 0:
        return ""
    if count == 1:
        return f"1 setting {verb}"
    plural = verb.replace("is ", "are ", 1) if verb.startswith("is ") else verb
    plural = plural.replace("was ", "were ", 1) if plural.startswith("was ") else plural
    return f"{count} settings {plural}"


def plain_toast(title: str, *, timeout: int = 5) -> Adw.Toast:
    """A toast whose title is plain text, never Pango markup (F7 of the #148 review).

    `Adw.Toast` parses its title as markup by default, so a preset's name, a path or a
    tool's message holding `&` or `<` rendered blank or wrong. Markup goes off before the
    title is set, which is the order that never parses it (#228).
    """
    toast = Adw.Toast(timeout=timeout)
    toast.set_use_markup(False)
    toast.set_title(title)
    return toast


def _result_summary(result: ApplyResult) -> str:
    """One line naming what went wrong, in words rather than in the enum's wire spelling."""
    return _FAILURE_TEXT.get(result.outcome, "The change could not be applied.")


def _revert_summary(revert: AutoRevert) -> str:
    """ADR-0016's toast line, or the honest version when the restore did not land.

    "Reverted" is a claim about the config on disk, and claiming it over a file that is still
    broken would send the user away from the one screen that could tell them so. A write the
    disk refused is said as that: Hyprland never saw it."""
    cause = (
        "The change could not be saved"
        if revert.outcome is ApplyOutcome.WRITE_FAILED
        else "Hyprland rejected the change"
    )
    if revert.restored:
        return f"{cause} — reverted."
    return f"{cause}, and it could not be reverted."


def replaced_sentence(outcome: Replaced, name: str, copies: str) -> str:
    """What "Replace file" did, one sentence per way it ends: only `DONE` wrote."""
    return {
        Replaced.DONE: (
            f"{name} was replaced with the app's version. Make your change again. "
            f"Your edited file is in {copies}."
        ),
        Replaced.NO_COPY: f"{name} was not replaced: a copy of it could not be kept.",
        Replaced.NOT_KNOWN: (
            f"{name} was not replaced: it does not read as a config, and the app has no "
            f"earlier version of it to rebuild. Open it and fix it by hand."
        ),
        Replaced.NOT_EDITED: f"{name} is back as the app wrote it, so nothing was replaced.",
        Replaced.READ_ONLY: f"{name} was not replaced: settings are read-only.",
    }[outcome]
