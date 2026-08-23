"""The main window: sidebar, Config view, and the one place the session's state shows.

`Adw.NavigationSplitView` with a Section list on the left and one generated Page per Section
on the right -- the Config view of ADR-0013 and design artboard `Main`. The Tasks view, its
switcher, and the remembered choice between them are #71; until then the sidebar is the
Config view alone, and there is nothing to switch.

Every Page is built at startup rather than on first visit. Two reasons, and the second is
the load-bearing one: switching Section stays instant, and "every Section builds a Page from
the shipped Schema" becomes a fact about the running app instead of a claim about code that
may never have run (the UI smoke tier asserts exactly this).

Errors get a Banner, not a Row badge (ADR-0016). This ticket raises it for one condition --
there is no compositor, so nothing can be applied -- and the error dialog behind it, the
`configerrors` attribution, and Quarantine are #60.
"""

from __future__ import annotations

from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from hyprtweaker.engine.apply import ApplyResult  # noqa: E402
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.pages.config import ConfigPage  # noqa: E402
from hyprtweaker.ui.pages.plan import plan_config_view  # noqa: E402
from hyprtweaker.ui.rows.factory import RowFactory  # noqa: E402

SHOW_ADVANCED_ACTION = "show-advanced"
"""One global switch, in the primary menu -- never per-Page (ADR-0013 §5).

Session-scoped for now: it belongs in the Prefs file with the View choice and the remembered
dialog answers, and that file is #71."""


class MainWindow(Adw.ApplicationWindow):
    """The Config view over one `Session`."""

    def __init__(self, session: Session, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._session = session
        self._factory = RowFactory(session)
        self._pages: list[ConfigPage] = []
        self._closing = False

        self.set_title("Hyprtweaker")
        self.set_default_size(1000, 700)

        self._sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar.connect("row-selected", self._on_section_selected)

        self._stack = Gtk.Stack(vexpand=True)
        self._banner = Adw.Banner(revealed=False)
        self._toasts = Adw.ToastOverlay()
        self._content_page = Adw.NavigationPage(title="Hyprtweaker")

        self._split = Adw.NavigationSplitView(
            sidebar=self._build_sidebar(),
            content=self._build_content(),
            min_sidebar_width=220,
        )
        self._toasts.set_child(self._split)
        self.set_content(self._toasts)

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

        toolbar = Adw.ToolbarView(content=body)
        toolbar.add_top_bar(Adw.HeaderBar())
        self._content_page.set_child(toolbar)
        return self._content_page

    def _menu_button(self) -> Gtk.MenuButton:
        menu = Gio.Menu()
        menu.append("Show advanced settings", f"win.{SHOW_ADVANCED_ACTION}")
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

    # --- the Config view ---------------------------------------------------------------------

    @property
    def pages(self) -> tuple[ConfigPage, ...]:
        """Every built Page. The UI smoke tier asserts against this."""
        return tuple(self._pages)

    @property
    def show_advanced(self) -> bool:
        return bool(self._advanced_action.get_state().get_boolean())

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

        self._select_section(selected or self._session.schema.section_names[0])
        self.sync()

    def sync(self) -> None:
        """Make every control agree with the model, and the Banner with the connection."""
        for page in self._pages:
            page.refresh()
            page.set_editable(self._session.live)

        reason = self._session.offline_reason
        self._banner.set_title("" if reason is None else f"{reason} — settings are read-only.")
        self._banner.set_revealed(reason is not None)

    def show_result(self, result: ApplyResult) -> None:
        """Report an apply that did not work. A working one needs no announcement.

        Instant apply's whole promise is that the change *is* the feedback (ADR-0003), so a
        toast per successful edit would be noise on every slider tick. The undo toast and the
        full error surface are #59 and #60.
        """
        if result.ok:
            return
        self._toasts.add_toast(Adw.Toast(title=_result_summary(result), timeout=5))

    # --- signals ------------------------------------------------------------------------------

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


def _result_summary(result: ApplyResult) -> str:
    """One line naming what went wrong, in the vocabulary `ApplyOutcome` already uses."""
    detail = f": {result.detail}" if result.detail else ""
    return f"Could not apply ({result.outcome.value}){detail}"
