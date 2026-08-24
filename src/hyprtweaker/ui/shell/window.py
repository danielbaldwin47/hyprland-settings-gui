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

from collections.abc import Callable, Mapping
from dataclasses import replace
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
    Problem,
    UndoStep,
)
from hyprtweaker.engine.apply import plan as recovery_plan  # noqa: E402
from hyprtweaker.engine.binds_analysis import submap_names  # noqa: E402
from hyprtweaker.engine.importer.loss import LossReport  # noqa: E402
from hyprtweaker.engine.ipc import CommandClient, Instance, NoInstance  # noqa: E402
from hyprtweaker.engine.migration.detect import ConfigKind, Detection, detect  # noqa: E402
from hyprtweaker.engine.migration.export import render as export_render  # noqa: E402
from hyprtweaker.engine.migration.flow import (  # noqa: E402
    Decision,
    MigrationFlow,
    fresh_start,
)
from hyprtweaker.engine.migration.sentinel import Sentinel  # noqa: E402
from hyprtweaker.engine.migration.sentinel import read as sentinel_read  # noqa: E402
from hyprtweaker.engine.model.entities import (  # noqa: E402
    Bind,
    LayerRule,
    MonitorRule,
    WindowRule,
)
from hyprtweaker.engine.profiles import MonitorStateSnapshot  # noqa: E402
from hyprtweaker.engine.schema import Schema  # noqa: E402
from hyprtweaker.engine.triggers import parse_trigger  # noqa: E402
from hyprtweaker.session import AutoRevert, Session  # noqa: E402
from hyprtweaker.ui.dialogs.bind_editor import BindEditor  # noqa: E402
from hyprtweaker.ui.dialogs.capture import CaptureDialog  # noqa: E402
from hyprtweaker.ui.dialogs.confirm_revert import ConfirmRevertDialog  # noqa: E402
from hyprtweaker.ui.dialogs.errors import error_dialog  # noqa: E402
from hyprtweaker.ui.dialogs.migration import (  # noqa: E402
    MigrationDialog,
    export_dialog,
    import_dialog,
    migration_dialog,
)
from hyprtweaker.ui.dialogs.rule_editor import RuleEditor  # noqa: E402
from hyprtweaker.ui.dialogs.submap_editor import SubmapEditor  # noqa: E402
from hyprtweaker.ui.pages.binds import BindActions, BindsPage  # noqa: E402
from hyprtweaker.ui.pages.config import ConfigPage  # noqa: E402
from hyprtweaker.ui.pages.monitors import (  # noqa: E402
    MonitorActions,
    MonitorsPage,
    ProfileActions,
)
from hyprtweaker.ui.pages.plan import plan_config_view  # noqa: E402
from hyprtweaker.ui.pages.rules import (  # noqa: E402
    LayerRulesPage,
    RuleActions,
    RulesPage,
    WindowRulesPage,
)
from hyprtweaker.ui.rows.factory import OptionRow, RowFactory  # noqa: E402

IMPORT_ACTION = "import-config"
EXPORT_ACTION = "export-config"
REPORT_ACTION = "import-report"

READ_ONLY_REASON = {
    ConfigKind.LEGACY_CONF: "You are still on hyprland.conf -- settings can't be saved yet.",
    ConfigKind.FOREIGN_LUA: (
        "Your hyprland.lua was not written here -- settings can't be saved until it is "
        "imported."
    ),
}
"""Why the app is read-only, in the user's terms (ADR-0009).

Shown, dismissible, and not repeated: "no nagging beyond that". The app is still worth
opening on an unmigrated box, which is why the pages render at all.
"""


def _discard(coro: Any) -> None:
    """Throw away a coroutine nobody can run. See `MainWindow._spawn`."""
    close = getattr(coro, "close", None)
    if close is not None:
        close()


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

SEVERE_BANNER_CLASS = "error"
"""libadwaita's own red styling, for ADR-0016's "Red Banner".

A style class rather than a colour, so it follows the user's theme and their accent choice
-- a hard-coded red is the one thing that would look wrong in every theme but the one it was
picked in."""

UNDO_TOAST_SECONDS = 4
"""Long enough to notice and reach, short enough not to sit over the Row that just changed."""

SHOW_ADVANCED_ACTION = "show-advanced"
"""One global switch, in the primary menu -- never per-Page (ADR-0013 §5).

The filter itself lives in `plan.py`, which also carries the tier rule the switch cannot
express on its own: `hidden` (`debug`, `quirks`, `experimental`, `input-capture`) is
Config-view-only, so flipping this on can never put "Crash Hyprland" on a curated Tasks
Page. A revealed Row wears an "Advanced" pill so it is legible as one (`rows/chrome.py`).

Search's one-off reveal -- reaching a withheld Row without flipping this at all -- is
ADR-0017's and arrives with #67.

Session-scoped: the remembered choice belongs in the Prefs file with the View choice and the
dialog answers, and that file is #71."""


class MainWindow(Adw.ApplicationWindow):
    """The Config view over one `Session`."""

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
        )
        self._pages: list[ConfigPage] = []
        self._binds_page: BindsPage | None = None
        self._window_rules_page: WindowRulesPage | None = None
        self._layer_rules_page: LayerRulesPage | None = None
        self._monitors_page: MonitorsPage | None = None
        self._session.watch_monitors(self._on_monitor_hotplug)
        self._offered: Detection | None = None
        """The import on offer, while one is (ADR-0009).

        Held because it outranks the health Banner: "settings can't be saved yet, Convert..."
        is more use to someone on an unmigrated box than "no compositor", and it is the only
        Banner state with a way out on its own button."""
        self._dependents = _dependents(session.schema)
        self._last_failure: str | None = None
        self._closing = False
        self._profile_toast: Adw.Toast | None = None
        self._profile_offered: tuple[str, frozenset[tuple[str, str]]] | None = None
        """Which `(profile, connected set)` pair the standing toast already offered.

        The hotplug dedupe: one dock arriving as several socket2 events must produce one
        toast, and a set with no match clears it so the next docking offers again."""
        self._profile_confirm: ConfirmRevertDialog | None = None
        self._undo_toast: Adw.Toast | None = None
        """The undo offer currently on screen, so the next one replaces it.

        Without this a burst of gestures stacks toasts, and the button on the one the user
        finally reaches is the *oldest* gesture rather than the last -- an undo that takes
        back something they have since changed twice."""

        self.set_title("Hyprtweaker")
        self.set_default_size(1000, 700)

        self._sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar.connect("row-selected", self._on_section_selected)

        self._stack = Gtk.Stack(vexpand=True)
        self._banner = Adw.Banner(revealed=False)
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

    # --- construction -----------------------------------------------------------------------

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        header.pack_end(self._menu_button())

        scroller = Gtk.ScrolledWindow(
            child=self._sidebar,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vexpand=True,
        )
        toolbar = Adw.ToolbarView(content=scroller)
        toolbar.add_top_bar(header)
        return Adw.NavigationPage(title="Hyprland", child=toolbar)

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

        interop = Gio.Menu()
        interop.append("Import...", f"win.{IMPORT_ACTION}")
        interop.append("Export...", f"win.{EXPORT_ACTION}")
        interop.append("Last import report", f"win.{REPORT_ACTION}")
        # A section of its own: Import and Export are about somebody else's config coming in
        # or this one going out, which is a different kind of act from changing a setting.
        menu.append_section(None, interop)
        return Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=menu,
            tooltip_text="Main menu",
        )

    def _install_actions(self) -> None:
        advanced = Gio.SimpleAction.new_stateful(
            SHOW_ADVANCED_ACTION, None, GLib.Variant.new_boolean(False)
        )
        advanced.connect("activate", self._on_toggle_advanced)
        self.add_action(advanced)
        self._advanced_action = advanced

        undo = Gio.SimpleAction.new(UNDO_ACTION, None)
        undo.connect("activate", self._on_undo)
        self.add_action(undo)
        self._undo_action = undo

        for name, handler in (
            (IMPORT_ACTION, self._on_import),
            (EXPORT_ACTION, self._on_export),
            (REPORT_ACTION, self._on_report),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

        controller = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        controller.add_shortcut(
            Gtk.Shortcut(
                trigger=Gtk.ShortcutTrigger.parse_string(UNDO_ACCELERATOR),
                action=Gtk.NamedAction.new(f"win.{UNDO_ACTION}"),
            )
        )
        self.add_controller(controller)

        application = self.get_application()
        if application is not None:
            # Only the label, and only when there is an application to ask: a window built
            # bare -- which is how the smoke tier builds one -- has no accel map to write to.
            application.set_accels_for_action(f"win.{UNDO_ACTION}", [UNDO_ACCELERATOR])

    # --- migration, import and export ----------------------------------------------------

    def migration_flow(self, source: Path | None = None) -> MigrationFlow:
        """A flow over this session's paths and schema, wired to the live compositor if any.

        The client is built here rather than borrowed from the Session because migration is
        the one caller of `reload full-reset`, and because a first-run wizard commonly runs
        while the Session itself is read-only -- there is no live model to apply through yet.
        """
        try:
            client: CommandClient | None = CommandClient(Instance.current())
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
            flow.build_preview(source)
        return flow

    def show_migration(self, source: Path | None = None) -> MigrationDialog:
        """Open the wizard. Returned so the UI tier can assert on what it is showing."""
        return migration_dialog(
            self,
            self.migration_flow(source),
            spawn=self._spawn,
            on_finished=self._on_migration_finished,
        )

    def _on_migration_finished(self, decision: Decision | None) -> None:
        """A kept migration is the one thing that retires the "convert me" Banner.

        A rollback -- or a wizard closed part-way -- leaves the offer standing, because the
        config the app cannot write to is still the one in place.
        """
        if decision is Decision.KEPT:
            self._offered = None
        self.sync()

    def route_first_run(self) -> Detection:
        """ADR-0009's four cases, decided once at startup and acted on.

        Returns the detection so the caller -- and the smoke tier -- can see which way it
        went without inspecting dialogs.
        """
        session = self._session
        detection = self._detect()
        self._offered = detection if detection.offers_import else None

        pending = sentinel_read(session.paths)
        if pending is not None:
            self._offer_rollback(pending)
            return detection

        if detection.kind is ConfigKind.FRESH:
            fresh_start(session.paths, session.schema, app_version=session.app_version)
        elif detection.offers_import:
            # Read-only until the offered import is accepted: there is nowhere honest to
            # write while the live session is reading a file this app does not own.
            session.set_read_only(READ_ONLY_REASON[detection.kind])
            self.sync_banner()
            GLib.idle_add(self._present_offer, detection)
        return detection

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
        flow = self.migration_flow()
        if response == "keep":
            flow.keep()
        else:
            self._spawn(flow.roll_back_live(pending))

    def _on_import(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        import_dialog(self, self.show_migration)

    def _on_export(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        export_dialog(self, self._write_export)

    def _on_report(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        """The last import's Loss report, reachable long after the wizard closed (ADR-0009).

        Persisted at Preview time precisely so this entry can exist: a user who wants to
        know what conversion cost them usually wants it days later, not while deciding.
        """
        report = LossReport.latest(self._session.paths)
        if report is None:
            self._toasts.add_toast(Adw.Toast(title="No configuration has been imported yet"))
            return
        dialog = Adw.AlertDialog(heading="Last import", body=report.render())
        dialog.add_response("close", "Close")
        dialog.present(self)

    def _write_export(self, target: Path) -> None:
        result = export_render(
            self._session.model,
            self._session.paths,
            app_version=self._session.app_version,
        )
        result.write(target)
        note = (
            f"Exported to {target.name}"
            if not result.missing
            else f"Exported to {target.name}, without {len(result.missing)} unreadable file(s)"
        )
        self._toasts.add_toast(Adw.Toast(title=note))

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
    def monitors_page(self) -> MonitorsPage | None:
        """The Displays Page, once built. The UI tier asserts against it."""
        return self._monitors_page

    @property
    def show_advanced(self) -> bool:
        return bool(self._advanced_action.get_state().get_boolean())

    @property
    def visible_section(self) -> str | None:
        """Which Section's Page the content pane is showing."""
        return self._stack.get_visible_child_name()

    def rebuild(self) -> None:
        """Build one Page per Section, replacing whatever was there.

        Whole-view rebuild rather than per-Row reveal: the Advanced switch changes which
        Options exist on a Page and therefore which Group each one lands in, and rebuilding
        from the plan is the only version of that which cannot drift from `plan.py`.
        """
        selected = self._selected_section()

        self._pages = []
        self._sidebar.remove_all()
        while (child := self._stack.get_first_child()) is not None:
            self._stack.remove(child)

        for plan in plan_config_view(self._session.schema, show_advanced=self.show_advanced):
            page = ConfigPage(plan, self._factory)
            self._pages.append(page)
            self._stack.add_named(_scrolled(page.page), plan.section)
            self._sidebar.append(_sidebar_row(plan.section, plan.title, plan.option_count))

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
                swap=self._swap_binds,
                edit_submap=self._edit_submap,
            ),
        )
        self._stack.add_named(_scrolled(self._binds_page.page), BindsPage.section)
        self._sidebar.append(
            _sidebar_row(BindsPage.section, BindsPage.title, len(self._binds_page.binds))
        )

        # The rule Pages: the same Entity-Page shape, twice (ADR-0008).
        self._window_rules_page = WindowRulesPage(
            self._session, actions=self._rule_actions("window")
        )
        self._layer_rules_page = LayerRulesPage(
            self._session, actions=self._rule_actions("layer")
        )
        for rules_page in (self._window_rules_page, self._layer_rules_page):
            self._stack.add_named(_scrolled(rules_page.page), rules_page.section)
            self._sidebar.append(
                _sidebar_row(rules_page.section, rules_page.title, len(rules_page.rules))
            )

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
        self._sidebar.append(
            _sidebar_row(
                MonitorsPage.section, MonitorsPage.title, len(self._monitors_page.rules)
            )
        )
        # The app-open answer feeds the canvas *and* the Profile-match toast: one fetch,
        # riding the same helper-data lane hotplug refreshes use (ADR-0018).
        self._session.fetch_monitors(self._on_monitors_event)

        self._select_section(selected or self._session.schema.section_names[0])
        self.sync()

    # --- binds ---------------------------------------------------------------------------

    def _add_bind(self, submap: str | None = None) -> None:
        def done(bind: Bind) -> None:
            if self._session.add_bind(bind):
                self._refresh_binds()

        BindEditor(on_done=done, submap=submap).present(self)

    def _edit_bind(self, index: int) -> None:
        if self._binds_page is None:
            return
        binds = self._binds_page.binds
        if not 0 <= index < len(binds):
            return

        def done(bind: Bind) -> None:
            if self._session.replace_bind(index, bind):
                self._refresh_binds()

        BindEditor(on_done=done, bind=binds[index]).present(self)

    def _remove_bind(self, index: int) -> None:
        if self._session.remove_bind(index):
            self._refresh_binds()

    def _set_bind_enabled(self, index: int, enabled: bool) -> None:
        if self._session.set_bind_enabled(index, enabled):
            self._refresh_binds()

    def _swap_binds(self, first: int, second: int) -> None:
        if self._session.swap_binds(first, second):
            self._refresh_binds()

    def _rebind_bind(self, index: int) -> None:
        """The conflict popover's "rebind it": Capture on the other bind, directly.

        Straight to Capture rather than through the full editor (ADR-0007): the problem
        being solved is *only* that two binds share a trigger, and the fix is a new
        trigger for one of them.
        """
        if self._binds_page is None:
            return
        binds = self._binds_page.binds
        if not 0 <= index < len(binds):
            return
        bind = binds[index]

        def done(text: str) -> None:
            keys = str(parse_trigger(text.strip()))
            if keys and self._session.replace_bind(index, replace(bind, keys=keys)):
                self._refresh_binds()

        CaptureDialog(
            on_done=done,
            initial=bind.keys,
            in_submap=bool(bind.submap) or bind.options.submap_universal,
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
        if self._binds_page is not None:
            self._binds_page.refresh()
        self.sync()

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
        def done(rule: WindowRule | LayerRule) -> None:
            if self._session.add_rule(kind, rule):
                self._refresh_rules(kind)

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

        def done(rule: WindowRule | LayerRule) -> None:
            if self._session.replace_rule(kind, index, rule):
                self._refresh_rules(kind)

        RuleEditor(
            kind=kind,
            on_done=done,
            rule=rules[index],
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
        if self._session.move_rule(kind, index, to):
            self._refresh_rules(kind)

    def _refresh_rules(self, kind: str) -> None:
        page = self._rules_page(kind)
        if page is not None:
            page.refresh()
        self.sync()

    # --- monitors -------------------------------------------------------------------------

    def _apply_monitor_breaking(self, output: str, fields: Mapping[str, Any]) -> None:
        """A display-breaking edit: apply, then Confirm-or-revert (ADR-0008).

        The snapshot is taken *before* the patch, so revert restores what the user was
        looking at when they made the change -- silence, Esc, or the countdown expiring
        all put it back; only the Keep button makes the new state stand.
        """
        snapshot = self._session.monitor_snapshot()
        if not self._session.patch_monitor_rule(output, fields):
            return
        self._refresh_monitors()
        ConfirmRevertDialog(
            on_keep=self._refresh_monitors,
            on_revert=lambda: self._revert_monitors(snapshot),
        ).present(self)

    def _revert_monitors(self, snapshot: tuple[MonitorRule, ...]) -> None:
        self._session.restore_monitor_rules(snapshot)
        self._refresh_monitors()

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
        toast = Adw.Toast(
            title=f'Displays match profile "{profile.name}"',
            button_label="Activate",
            timeout=10,
        )
        toast.connect("button-clicked", lambda _t: self._activate_monitor_profile(slug))
        self._profile_toast = toast
        self._toasts.add_toast(toast)

    def _save_monitor_profile(self, name: str) -> None:
        """Capture the current setup under `name` -- the save dialog's verb."""
        if self._monitors_page is None:
            return
        self._session.save_monitor_profile(name, self._monitors_page.connected)
        self._toasts.add_toast(Adw.Toast(title=f'Saved profile "{name}"'))
        self._refresh_monitors()

    def _activate_monitor_profile(self, slug: str) -> None:
        """Activation: one transaction, then Confirm-or-revert (ADR-0015).

        The snapshot is wider than a field edit's -- both rule lists plus the active
        pointer -- because activation touches workspace pins too, and a revert that
        left the refused profile's pins standing would be half a revert.
        """
        snapshot = self._session.monitor_state_snapshot()
        if not self._session.activate_monitor_profile(slug):
            return
        self._refresh_monitors()
        self._profile_confirm = ConfirmRevertDialog(
            on_keep=self._refresh_monitors,
            on_revert=lambda: self._revert_monitor_state(snapshot),
        )
        self._profile_confirm.present(self)

    @property
    def profile_confirm(self) -> ConfirmRevertDialog | None:
        """The countdown guarding the last activation. Probed by the smoke tier."""
        return self._profile_confirm

    def _revert_monitor_state(self, snapshot: MonitorStateSnapshot) -> None:
        self._session.restore_monitor_state(snapshot)
        self._refresh_monitors()

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
        if self._monitors_page is None:
            return
        self._session.fetch_monitors(self._monitors_page.set_connected)
        self.sync()

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

        self.sync_banner()
        self._undo_action.set_enabled(self._session.can_undo)

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
            self._banner.set_use_markup(False)
            self._banner.remove_css_class(SEVERE_BANNER_CLASS)
            return

        health = self._session.health
        self._banner.set_title(health.title)
        self._banner.set_revealed(health.unhealthy)
        # libadwaita shows the button whenever the label is non-empty, so clearing it is how
        # a Banner with nothing to open loses its button rather than keeping a dead one.
        self._banner.set_button_label(health.button or "")
        # Off, because these titles carry file names: a path containing an ampersand is not
        # markup, and a Banner that tried to parse it as markup would render nothing at all.
        self._banner.set_use_markup(False)
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
        ADR-0016 is explicit that toasts are "only for transient auto-revert events", and
        both kinds of failure the Banner *does* carry are excluded here: a config error,
        which belongs to the Banner and its dialog because they can offer to fix it, and a
        read-back mismatch, which raises the Banner and badges its Row. What is left for a
        toast is the handful of failures that never reached the compositor at all -- a
        refused write, a full disk -- which would otherwise happen in silence.

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
        self._undo_action.set_enabled(self._session.can_undo)
        self.sync_banner()

        if not result.ok:
            self._dismiss_undo()
        if not result.ok and not result.errors and not result.mismatches:
            self._toasts.add_toast(Adw.Toast(title=_result_summary(result), timeout=5))

    def show_revert(self, revert: AutoRevert) -> None:
        """The app has just taken back its own rejected write (ADR-0016 §Auto-revert).

        The one event the ADR reserves a toast for outright, because it is the only time the
        UI changes without the user having asked: they made a change, Hyprland refused it,
        and the Row has moved back on its own. **Details** carries the `configerrors` lines,
        which by now exist nowhere else -- the restore transaction's own reload cleared the
        compositor's copy.
        """
        self._dismiss_undo()
        toast = Adw.Toast(title=_revert_summary(revert), timeout=8)
        if revert.errors:
            toast.set_button_label("Details")
            # The same dialog the Banner opens, with no actions on it. By the time this can
            # be clicked the app has already put the file back, so every recovery it could
            # offer would be a recovery from a recovery.
            toast.connect(
                "button-clicked", lambda *_: error_dialog(self, recovery_plan(revert.errors))
            )
        self._toasts.add_toast(toast)

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
        # One call for all of them: two releases would be two Entrypoint rewrites racing
        # each other through the queue.
        if health.quarantined:
            self._session.release_quarantine(*health.quarantined)

    def show_errors(self) -> Adw.AlertDialog:
        """Open the one error dialog over the current problems. Returned for the UI tier."""
        return error_dialog(self, self._session.recovery, on_action=self._on_recovery_action)

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
            self._session.restore_last_good(problem.module)
        elif action is Action.REGENERATE:
            self._session.regenerate_entrypoint()
        elif action is Action.QUARANTINE:
            self._confirm_quarantine(problem)

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
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self, None, None)

    # --- undo -------------------------------------------------------------------------------

    @property
    def undo_toast(self) -> Adw.Toast | None:
        """The undo offer currently on screen, if there is one.

        View state rather than an accessor for a private field: "is the app offering to undo
        right now?" is the whole visible consequence of a gesture landing, and the UI tier
        asserts on it exactly as it asserts on `pages`.
        """
        return self._undo_toast

    def offer_undo(self, step: UndoStep) -> None:
        """Offer to take back the gesture that just landed.

        Told which gesture rather than reading the stack top, and the difference is visible:
        a transaction that recorded no step -- an undo's own write, or one whose bytes were
        already on disk -- would otherwise raise an offer for whatever gesture happened to be
        underneath, naming a Row the user has not touched for a while.
        """
        self._undo_action.set_enabled(self._session.can_undo)
        self._dismiss_undo()
        toast = Adw.Toast(title=self._gesture_title(step), timeout=UNDO_TOAST_SECONDS)
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
        """Take back the last gesture. The session decides whether there is one."""
        self._dismiss_undo()
        if self._session.undo():
            # `sync` runs on the session's own `on_state_changed` too, but the undo has
            # already moved the model and the Rows should not wait for the compositor to
            # confirm what the app is about to write.
            self.sync()
        self._undo_action.set_enabled(self._session.can_undo)

    def _on_undo(self, _action: Gio.SimpleAction, _parameter: Any) -> None:
        self._undo()

    def _gesture_title(self, step: UndoStep) -> str:
        """What the undo toast calls the gesture it is offering to reverse.

        The Option's own title, because that is the word on the Row the user just changed --
        never the dotted key, which lives in the Help popover and the search index (ADR-0013).
        A gesture spanning several Options is counted rather than listed: the css-gaps editor
        writes four sides at once, and "Gaps in, Gaps in, Gaps in, Gaps in" is not a sentence.
        """
        titles = [self._session.schema[name].title for name in step.names]
        if len(titles) == 1:
            return f"{titles[0]} changed"
        return f"{len(titles)} settings changed"

    def reveal_option(self, name: str) -> None:
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
        GLib.idle_add(self._put_on_screen, row, priority=GLib.PRIORITY_LOW)

    def _put_on_screen(self, row: OptionRow) -> bool:
        """Scroll a Row into view and leave the keyboard on it.

        Both, because neither is enough alone: an *insensitive* widget cannot take focus --
        precisely the case a dependency badge navigates away from, and the case of every Row
        on a read-only session -- so the explicit scroll is what makes the reveal work at
        all. The focus is what leaves the user on the control they came to change.
        """
        _scroll_into_view(row.widget)
        if not row.control.grab_focus():
            row.widget.grab_focus()
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
        self._stack.set_visible_child_name(section)
        self._content_page.set_title(self._session.schema.section_title(section))
        self._split.set_show_content(True)

    def _on_toggle_advanced(self, action: Gio.SimpleAction, _parameter: Any) -> None:
        action.set_state(GLib.Variant.new_boolean(not self.show_advanced))
        self.rebuild()

    def _on_close_request(self, *_: Any) -> bool:
        """Hold the window open until the session has flushed and let go of its sockets.

        An edit made in the last moment before closing is still inside the apply debounce.
        Letting the window go first would drop it, and the user watched it land in the UI.
        """
        if self._closing:
            return False
        self._closing = True
        self._session.close(self.destroy)
        return True

    # --- helpers ------------------------------------------------------------------------

    def _selected_section(self) -> str | None:
        row = self._sidebar.get_selected_row()
        return None if row is None else row.get_name()

    def _select_section(self, section: str) -> None:
        for index in range(len(self._pages)):
            row = self._sidebar.get_row_at_index(index)
            if row is not None and row.get_name() == section:
                self._sidebar.select_row(row)
                return


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


def _result_summary(result: ApplyResult) -> str:
    """One line naming what went wrong, in words rather than in the enum's wire spelling."""
    return _FAILURE_TEXT.get(result.outcome, "The change could not be applied.")


def _revert_summary(revert: AutoRevert) -> str:
    """ADR-0016's toast line, or the honest version when the restore did not land.

    "Reverted" is a claim about the config on disk, and claiming it over a file that is still
    broken would send the user away from the one screen that could tell them so."""
    if revert.restored:
        return "Hyprland rejected the change — reverted."
    return "Hyprland rejected the change, and it could not be reverted."
