"""The sidebar's search surface: the entry, its results, and the mode they imply (ADR-0017).

Split out of `window.py` rather than added to it. The window is "the window, the sidebar and
the two Views"; a finder is a second thing with its own widgets, its own keyboard handling
and its own idea of what the sidebar is currently showing, and folding it in would be one
module changing for two unrelated reasons.

The seam is deliberately narrow: this owns everything about *finding* -- the entry, the
query, the ranked list, and which of the two the sidebar header shows -- and knows nothing
about navigating to a hit. Opening one is the window's job, because it is the window that
holds the View, the Pages and the One-off reveal; this only says which `Hit` was chosen.

**Why the bar sits below the header rather than in the title slot.** ADR-0017 first said the
magnifier "swaps the sidebar title for a search entry"; §Surface was amended during #72
because the platform will not have it. GTK's type-to-search handler starts with
`if (!gtk_widget_get_mapped (bar)) return GDK_EVENT_PROPAGATE` (`gtksearchbar.c`), so the bar
must stay mapped even while the finder is closed -- which rules out a `GtkStack` page, and
rules out hiding or unparenting it between searches. Sharing the title slot with the title
keeps it mapped but charges that slot the bar's *horizontal* measurement forever: a closed
`GtkSearchBar` collapses only vertically (its revealer slides down), so it still measures
min 85 / nat 224 px, and the sidebar title rendered as "Hyp...".

Below the header is what the widget is built for -- the revealer slides *down* because that
is where a search bar is meant to live -- and it is the only one of the three arrangements
correct on both counts: the title reads, and typing opens the finder. Type-to-search is an
ADR requirement and the title-slot choreography is not, so the requirement wins.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from hyprtweaker.ui.pages.config import escaped  # noqa: E402
from hyprtweaker.ui.search import EntityHit, Hit, OptionHit, SearchIndex  # noqa: E402

RESULT_LIMIT = 50
"""How many hits the sidebar lists in each group.

Not a ranking decision -- the index ranks the whole corpus and this takes the head of it.
A cap exists because a two-letter query matches most of the Schema, and a sidebar rebuilding
300 rows per keystroke is a stutter the user reads as the app thinking. Fifty is well past
where anyone scrolls: someone who has not found it by then types another letter. Per group
rather than overall, so a two-letter query naming fifty Options still lists its binds."""

NAV_MODE = "nav"
RESULTS_MODE = "results"


class Finder:
    """The search entry, its results, and the sidebar mode they imply.

    Owns no navigation: `on_activate` is called with the chosen `Hit` and the window decides
    what opening it means.
    """

    def __init__(
        self,
        index: SearchIndex,
        *,
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
        self._groups: list[str] = []
        """Each listed hit's group, kept in step with `_hits` for the header function.

        Handed to GTK as the function's data rather than read off `self`: a bound method as
        the header function is a reference from the C side of the ListBox back to this
        Finder, a cycle the collector cannot see through, and the closed window it reaches
        through `on_activate` would never be released (#219's lifetime test)."""
        self.results.set_header_func(_result_header, self._groups)

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
        """Whether the finder is open -- the bar revealed, the entry showing."""
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

    def requery(self) -> None:
        """Re-run the open query against the index as it is now -- after the model moved.

        The window calls this from `sync`, so a foreign reload, an undo or an edit made on
        a Page refreshes a result list that is showing, rather than leaving rows that open
        something that is no longer there (settled S2b). A no-op while the nav list shows, and
        while the answer is unchanged -- the common case, and the one where rebuilding the
        rows would only cost the list its scroll position.
        """
        if self.mode != RESULTS_MODE:
            return
        if self._index.query(self.entry.get_text(), limit=RESULT_LIMIT) != self._hits:
            self._refresh()

    def activate_selected(self) -> None:
        """Enter in the entry opens the highlighted hit -- the no-mouse path."""
        row = self.results.get_selected_row()
        if row is not None:
            self._on_row_activated(self.results, row)

    # --- internals -----------------------------------------------------------------------

    def _on_mode_toggled(self) -> None:
        """Closing the finder drops the query and gives the nav list back.

        Clearing on close is what makes Escape a full undo of the search rather than a way
        to hide a query that is still filtering: reopening should offer an empty finder, not
        the last search's results (ADR-0017: "clearing or escaping restores the nav list").
        """
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
        selected = self.results.get_selected_row()
        previous = (
            self._hits[selected.get_index()]
            if selected is not None and 0 <= selected.get_index() < len(self._hits)
            else None
        )
        self._hits = self._index.query(self.entry.get_text(), limit=RESULT_LIMIT)
        self._groups[:] = [hit.group for hit in self._hits]
        self.results.remove_all()

        if self.mode == RESULTS_MODE:
            for hit in self._hits:
                self.results.append(_result_row(hit))
            if not self._hits:
                self.results.append(_no_matches_row())
            # A re-run keeps the highlight on the hit it was on, so a `sync` arriving while
            # the user walks the list with the arrows does not throw them back to the top.
            keep = self._hits.index(previous) if previous in self._hits else 0
            row = self.results.get_row_at_index(keep)
            if row is not None and self._hits:
                self.results.select_row(row)

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
    """One search hit, an Option's or an Entity's."""
    if isinstance(hit, EntityHit):
        return _entity_row(hit)
    return _option_row(hit)


def _option_row(hit: OptionHit) -> Gtk.ListBoxRow:
    """One Option hit: what the Row is called, and the key that addresses it.

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


def _entity_row(hit: EntityHit) -> Gtk.ListBoxRow:
    """One Entity hit: its row's title, then what kind it is and what its row says.

    The kind's noun leads the subtitle because the group mixes kinds, and "SUPER + Q" or
    "DP-1" alone does not say which Page opening it lands on. The badge comes next, in the
    row's own words (#139), since a bind Hyprland cannot load is the salient fact about it;
    then the row's detail line. The title may take three lines where an Option's takes one:
    an unlabelled rule's title is its whole summary, and six pavucontrol rules cut at one line
    all read "class ^(pavucontrol)$ → ..." -- the effect that tells them apart is at the end
    (probed over end-4). Plain text, as the Pages' rows are: Triggers, commands and
    Match patterns are user text, and as Pango markup `A&B` renders blank.
    """
    subtitle = " · ".join(part for part in (hit.kind.noun, hit.badge, hit.subtitle) if part)
    # `use_markup` first and the texts after: given together to the constructor, the texts
    # are parsed as markup before the flag lands, and GTK warns over every `&&` command.
    row = Adw.ActionRow(use_markup=False, title_lines=3, subtitle_lines=2)
    row.set_title(hit.title)
    row.set_subtitle(subtitle)
    row.set_activatable(True)
    return row


def _no_matches_row() -> Gtk.ListBoxRow:
    row = Adw.ActionRow(title="No matches", css_classes=["dim-label"])
    row.set_activatable(False)
    row.set_selectable(False)
    return row


def _result_header(
    row: Gtk.ListBoxRow, before: Gtk.ListBoxRow | None, groups: list[str]
) -> None:
    """A group heading above the first row of each group, and nothing above the rest.

    Asked of the hits' groups rather than of the rows: each hit knows its group (ADR-0017's
    Settings, then Keybinds, rules, displays & presets), and a row knows only where it sits.
    """
    index = row.get_index()
    group = groups[index] if 0 <= index < len(groups) else None
    above = before.get_index() if before is not None else -1
    if group is None or (0 <= above < len(groups) and groups[above] == group):
        row.set_header(None)
        return
    row.set_header(
        Gtk.Label(
            label=group,
            xalign=0.0,
            css_classes=["heading", "dim-label"],
            margin_start=12,
            margin_top=8,
            margin_bottom=4,
        )
    )
