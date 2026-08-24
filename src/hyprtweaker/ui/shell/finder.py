"""The sidebar's search surface: entry, results list, and the title it replaces (ADR-0017).

Split out of `window.py` rather than added to it. The window is "the window, the sidebar and
the two Views"; a finder is a second thing with its own widgets, its own keyboard handling
and its own idea of what the sidebar is currently showing, and folding it in would be one
module changing for two unrelated reasons.

The seam is deliberately narrow: this owns everything about *finding* -- the entry, the
query, the ranked list, and which of the two the sidebar header shows -- and knows nothing
about navigating to a hit. Opening one is the window's job, because it is the window that
holds the View, the Pages and the One-off reveal; this only says which `Hit` was chosen.

**Why the title is a `GtkStack`.** ADR-0017's surface is "the magnifier button in the
sidebar header swaps the sidebar title for a search entry" -- so the entry lives in the
header's title slot, not on a row of its own beneath it. A stack rather than
`set_title_widget` calls on each toggle, because the `GtkSearchBar` has to stay parented
even while the title is showing: it owns the type-to-search key capture, and a widget that
is unparented between searches captures nothing.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from hyprtweaker.ui.pages.config import escaped  # noqa: E402
from hyprtweaker.ui.search import Hit, SearchIndex  # noqa: E402

RESULT_LIMIT = 50
"""How many hits the sidebar lists.

Not a ranking decision -- the index ranks the whole corpus and this takes the head of it.
A cap exists because a two-letter query matches most of the Schema, and a sidebar rebuilding
300 rows per keystroke is a stutter the user reads as the app thinking. Fifty is well past
where anyone scrolls: someone who has not found it by then types another letter."""

NAV_MODE = "nav"
RESULTS_MODE = "results"

_TITLE_CHILD = "title"
_SEARCH_CHILD = "search"

SETTINGS_GROUP = "Settings"
"""ADR-0017's first result group. The second -- "Rules & entities" -- is #75."""


class Finder:
    """The search entry, its results, and the sidebar mode they imply.

    Owns no navigation: `on_activate` is called with the chosen `Hit` and the window decides
    what opening it means.
    """

    def __init__(
        self,
        index: SearchIndex,
        *,
        title: str,
        on_activate: Callable[[Hit], None],
        on_mode_changed: Callable[[str], None],
    ) -> None:
        self._index = index
        self._on_activate = on_activate
        self._on_mode_changed = on_mode_changed
        self._hits: tuple[Hit, ...] = ()
        """The hits currently listed, positionally matched to the rows in `results`.

        A parallel tuple rather than an attribute on each row: a `GtkListBoxRow` can only
        carry a string name, and stuffing a dotted key through that would make the row the
        source of truth for which Option it means -- which is how a stale row navigates
        somewhere the list no longer shows."""

        self.entry = Gtk.SearchEntry(placeholder_text="Search settings", hexpand=True)
        self.entry.connect("search-changed", lambda *_: self._refresh())
        self.entry.connect("activate", lambda *_: self.activate_selected())
        # Arrows reach the results without the hands leaving the entry (ADR-0017: "the
        # sidebar's search-results mode needs keyboard traversal ... since Ctrl+F users
        # won't reach for the mouse"). Moving the *selection* rather than the focus is what
        # keeps typing possible mid-traversal: refining the query after two Downs should
        # still go into the entry.
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.entry.add_controller(keys)

        self.bar = Gtk.SearchBar()
        self.bar.set_child(self.entry)
        self.bar.connect_entry(self.entry)
        self.bar.connect("notify::search-mode-enabled", lambda *_: self._on_mode_toggled())

        self.results = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.results.connect("row-activated", self._on_row_activated)
        self.results.set_header_func(_result_header)

        self.title = Gtk.Stack()
        self.title.add_named(Adw.WindowTitle(title=title), _TITLE_CHILD)
        self.title.add_named(self.bar, _SEARCH_CHILD)

        self.button = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Search settings (Ctrl+F)",
        )
        # A property binding rather than two handlers: the bar's mode also changes from
        # Ctrl+F, from type-to-search and from Escape, and a button kept in step by hand
        # goes wrong the first time one of those three fires.
        self.button.bind_property(
            "active",
            self.bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

    # --- state ---------------------------------------------------------------------------

    @property
    def hits(self) -> tuple[Hit, ...]:
        return self._hits

    @property
    def active(self) -> bool:
        """Whether the finder is open -- the entry showing instead of the title."""
        return self.bar.get_search_mode()

    @property
    def mode(self) -> str:
        """Which list the sidebar should be showing: `nav` or `results`."""
        return RESULTS_MODE if self.entry.get_text().strip() else NAV_MODE

    # --- driving it ----------------------------------------------------------------------

    def capture_keys_from(self, widget: Gtk.Widget) -> None:
        """Type-to-search: any keystroke on `widget` that is not already going into text.

        Delegated to the toolkit rather than hand-rolled -- GTK already knows a keypress
        landing in a Row's entry or spin button is not the start of a search, and every Row
        on every Page of this app is one of those.
        """
        self.bar.set_key_capture_widget(widget)

    def start(self) -> None:
        """Open the finder and put the cursor in it -- Ctrl+F, and the magnifier."""
        self.bar.set_search_mode(True)
        self.entry.grab_focus()

    def close(self) -> None:
        self.bar.set_search_mode(False)

    def search(self, text: str) -> None:
        """Open the finder on `text`, as though it had been typed.

        The results are refreshed here rather than left to the entry's own signal because
        `Gtk.SearchEntry` deliberately holds `search-changed` back for ~150 ms -- the right
        behaviour for someone typing, and a race for a caller that sets the whole query at
        once and expects to be able to read the answer.
        """
        self.start()
        self.entry.set_text(text)
        self._refresh()

    def activate_selected(self) -> None:
        """Enter in the entry opens the highlighted hit -- the no-mouse path."""
        row = self.results.get_selected_row()
        if row is not None:
            self._on_row_activated(self.results, row)

    # --- internals -----------------------------------------------------------------------

    def _on_mode_toggled(self) -> None:
        """Opening swaps the title for the entry; closing puts the title back and clears.

        Clearing on close is what makes Escape a full undo of the search rather than a way
        to hide a query that is still filtering: reopening should offer an empty finder, not
        the last search's results (ADR-0017: "clearing or escaping restores the nav list").
        """
        self.title.set_visible_child_name(_SEARCH_CHILD if self.active else _TITLE_CHILD)
        if not self.active:
            self.entry.set_text("")
        self._refresh()

    def _refresh(self) -> None:
        """Re-run the query and re-fill the list, then tell the sidebar which mode it is in.

        An empty query and a query that matches nothing are deliberately different: the
        first restores the nav list, the second stays in results mode showing "No matches",
        because silently reverting to the nav list would read as the search having been
        forgotten rather than answered.
        """
        self._hits = self._index.query(self.entry.get_text(), limit=RESULT_LIMIT)
        self.results.remove_all()

        if self.mode == RESULTS_MODE:
            for hit in self._hits:
                self.results.append(_result_row(hit))
            if not self._hits:
                self.results.append(_no_matches_row())
            first = self.results.get_row_at_index(0)
            if first is not None and self._hits:
                self.results.select_row(first)

        self._on_mode_changed(self.mode)

    def _on_key(self, _controller: Gtk.EventControllerKey, keyval: int, *_: object) -> bool:
        """Down/Up walk the results while the cursor stays in the entry."""
        step = {Gdk.KEY_Down: 1, Gdk.KEY_Up: -1}.get(keyval)
        if step is None or not self._hits:
            return False
        selected = self.results.get_selected_row()
        index = 0 if selected is None else selected.get_index() + step
        row = self.results.get_row_at_index(max(0, min(index, len(self._hits) - 1)))
        if row is not None:
            self.results.select_row(row)
        return True

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        index = row.get_index()
        if 0 <= index < len(self._hits):
            self._on_activate(self._hits[index])


def _result_row(hit: Hit) -> Gtk.ListBoxRow:
    """One search hit: what the Row is called, and the key that addresses it.

    The dotted key as subtitle, which is the one place outside the Help popover it belongs
    (ADR-0013 §1) -- in a result list it is what disambiguates the four Options all titled
    "Corner rounding", and someone who typed a key wants to see the key they matched.
    """
    row = Adw.ActionRow(
        title=escaped(hit.title),
        subtitle=escaped(hit.dotted_key),
        # The sidebar is 220px at its narrowest and a dotted key is long: left alone,
        # `group.groupbar.gradient_rounding_power` wraps to three hyphenated lines and the
        # list stops being scannable. One line for the title, two for the key -- and *two*
        # rather than one because these lines ellipsise at the end, and a key's leaf is
        # exactly what tells `...gradient_rounding` from `...gradient_rounding_power`.
        title_lines=1,
        subtitle_lines=2,
    )
    row.set_name(hit.name)
    row.set_activatable(True)
    return row


def _no_matches_row() -> Gtk.ListBoxRow:
    row = Adw.ActionRow(title="No matches", css_classes=["dim-label"])
    row.set_activatable(False)
    row.set_selectable(False)
    return row


def _result_header(row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None) -> None:
    """The group heading above the first result, and nothing above the rest.

    One group today, because #72 indexes Options only. The second group ADR-0017 names --
    "Rules & entities" -- arrives with #75, at which point this grows a real test of which
    group a row belongs to; until then the honest implementation is "the first row gets the
    one heading there is".
    """
    if before is not None or not row.get_selectable():
        row.set_header(None)
        return
    row.set_header(
        Gtk.Label(
            label=SETTINGS_GROUP,
            xalign=0.0,
            css_classes=["heading", "dim-label"],
            margin_start=12,
            margin_top=8,
            margin_bottom=4,
        )
    )
