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

from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult  # noqa: E402
from hyprtweaker.engine.schema import Schema  # noqa: E402
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.pages.config import ConfigPage  # noqa: E402
from hyprtweaker.ui.pages.plan import plan_config_view  # noqa: E402
from hyprtweaker.ui.rows.factory import OptionRow, RowFactory  # noqa: E402

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

    def __init__(self, session: Session, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._session = session
        self._factory = RowFactory(
            session,
            on_edited=self._on_option_edited,
            navigate=self.reveal_option,
        )
        self._pages: list[ConfigPage] = []
        self._dependents = _dependents(session.schema)
        self._last_failure: str | None = None
        self._closing = False

        self.set_title("Hyprtweaker")
        self.set_default_size(1000, 700)

        self._sidebar = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self._sidebar.connect("row-selected", self._on_section_selected)

        self._stack = Gtk.Stack(vexpand=True)
        self._banner = Adw.Banner(revealed=False)
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

        self._select_section(selected or self._session.schema.section_names[0])
        self.sync()

    def sync(self) -> None:
        """Make every control agree with the model, and the Banner with the connection."""
        for page in self._pages:
            page.refresh()

        reason = self._session.offline_reason
        self._banner.set_title("" if reason is None else f"{reason} — settings are read-only.")
        self._banner.set_revealed(reason is not None)

    def show_result(self, result: ApplyResult) -> None:
        """What a finished Apply transaction changes about the view.

        Two things, and the first happens whether or not the write worked: a restart-flagged
        key that reached the file wants its "Pending restart" pill, and a key that was
        refused wants no pill at all (`ApplyResult.pending_restart` only names keys whose
        bytes actually landed -- ADR-0010).

        The failure toast is the second. Instant apply's whole promise is that the change
        *is* the feedback (ADR-0003), so a toast per *successful* edit would be noise on
        every slider tick. The undo toast and the full error surface are #59 and #60.
        """
        for name in result.pending_restart:
            self._refresh_chrome_for(name)

        if result.ok:
            return
        self._toasts.add_toast(Adw.Toast(title=_result_summary(result), timeout=5))

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
#: value -- "read-back-mismatch" is not a sentence to show a user -- and #60 replaces this
#: with the full error dialog and its per-Ownership-class actions.
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
