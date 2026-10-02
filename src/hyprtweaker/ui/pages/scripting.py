"""The Scripting Page: what `user.lua` and `legacy.lua` do, listed read-only (ADR-0018).

Event handlers, timers and Lua layouts live inside Hyprland's VM and no query lists them,
so this Page shows what a static read of the two files finds (`engine.scripting`). Every
row names the call, its `file:line`, and opens the file; nothing here edits anything.

**Groups.** The inventory's groups are held in their own list and rebuilt in place on
every `refresh()`. Groups added to the Page before them -- #174's Plugins load list goes
first -- stay where they are, because `refresh()` removes and re-appends only its own.

**When it reads.** On construction, on every window `sync` (a foreign reload lands
there, and Hyprland reloads when a `require`d file changes), and whenever the window
shows this Page. No timer and no file monitor: two small files are cheap to read.

**Failure.** The scan never raises for I/O; a bug in it still must not take the window
down, so `refresh()` catches it and says so in the Page. The list is informational, never
state the Writer reads, so a failed scan costs the user this list and nothing else.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.scripting import (  # noqa: E402
    CallKind,
    IndirectUse,
    ScriptingHit,
    ScriptingScan,
    UnfinishedText,
    scan_scripting,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402

_log = logging.getLogger(__name__)

LEAD_TITLE = "In your Lua files"
CAVEAT = (
    "Found by reading user.lua and legacy.lua. Calls built at runtime, in loops or "
    "through other names may not appear."
)
EMPTY = "No event handlers, timers, custom layouts or plugin loads found"

GROUP_TITLES: dict[CallKind, str] = {
    CallKind.ON: "Event handlers",
    CallKind.TIMER: "Timers",
    CallKind.LAYOUT: "Custom layouts",
    CallKind.PLUGIN_LOAD: "Plugin loads",
}
"""One group per call kind, in this order; a kind with no hits shows no group."""

UNNAMED: dict[CallKind, str] = {
    CallKind.ON: "Event name set at runtime",
    CallKind.TIMER: "Timer settings set at runtime",
    CallKind.LAYOUT: "Layout name set at runtime",
    CallKind.PLUGIN_LOAD: "Plugin path set at runtime",
}
"""A hit's title when the file computes the name instead of writing it out."""

SPELLING: dict[CallKind, str] = {
    CallKind.ON: "hl.on",
    CallKind.TIMER: "hl.timer",
    CallKind.LAYOUT: "hl.layout.register",
    CallKind.PLUGIN_LOAD: "hl.plugin.load",
}


@dataclass(frozen=True, slots=True)
class ScriptingActions:
    """The verbs the window wires in. Opening needs the window as the launcher's parent."""

    open_file: Callable[[Path], None]


class ScriptingRow:
    """One listed line: a hit, a gap the scan reports, or the Page's empty/failed state."""

    def __init__(
        self, title: str, subtitle: str, *, open_file: Callable[[], None] | None, file: str
    ) -> None:
        self.title = title
        self.subtitle = subtitle
        # User text (an event name, a path): as Pango markup an `&` renders blank.
        self.widget = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        self.open_button: Gtk.Button | None = None
        if open_file is not None:
            button = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER)
            button.add_css_class("flat")
            button.set_tooltip_text(f"Open {file}")
            button.connect("clicked", lambda _button: open_file())
            self.widget.add_suffix(button)
            self.open_button = button


class ScriptingPage:
    """The read-only inventory of `hl.on`, `hl.timer`, `hl.layout.register` and
    `hl.plugin.load` calls in the user's Lua."""

    title = "Scripting"
    section = entity_page_id("scripting")

    def __init__(self, session: Session, *, actions: ScriptingActions) -> None:
        self._session = session
        self._actions = actions
        self._page = Adw.PreferencesPage(title=self.title)
        self._inventory: list[Adw.PreferencesGroup] = []
        self._listed: list[tuple[str, ScriptingRow]] = []
        self._hit_count = 0
        self.refresh()

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def hit_count(self) -> int:
        """How many calls the last read found: the sidebar's count."""
        return self._hit_count

    @property
    def caveat(self) -> str:
        """The best-effort sentence, on the inventory's lead group."""
        return CAVEAT

    def listed_rows(self) -> tuple[tuple[str, ScriptingRow], ...]:
        """Every inventory row with its group's title, in Page order. The UI tier's view."""
        return tuple(self._listed)

    def refresh(self) -> None:
        """Read the files again and rebuild the inventory groups in place."""
        for group in self._inventory:
            self._page.remove(group)
        self._inventory = []
        self._listed = []

        lead = Adw.PreferencesGroup(title=LEAD_TITLE, description=CAVEAT)
        self._add_group(lead)
        try:
            scan = scan_scripting(self._session.paths)
        except Exception:  # a scanner bug must not break the window
            _log.exception("scanning user.lua and legacy.lua failed")
            self._hit_count = 0
            self._add_row(
                lead,
                ScriptingRow(
                    "Could not search your Lua files",
                    "This list is for reading only, so your settings are not affected.",
                    open_file=None,
                    file="",
                ),
            )
            return

        self._hit_count = len(scan.hits)
        self._list_problems(lead, scan)
        if not scan.hits and not scan.unreadable and not scan.gaps:
            self._add_row(lead, ScriptingRow(EMPTY, "", open_file=None, file=""))

        for kind, title in GROUP_TITLES.items():
            hits = [hit for hit in scan.hits if hit.kind is kind]
            if not hits:
                continue
            group = Adw.PreferencesGroup(title=title)
            self._add_group(group)
            for hit in hits:
                self._add_row(group, self._hit_row(hit))

    # --- rows -------------------------------------------------------------------------

    def _list_problems(self, lead: Adw.PreferencesGroup, scan: ScriptingScan) -> None:
        """What the scan could not read, above what it found: a partial list must say so."""
        for path in scan.unreadable:
            self._add_row(
                lead,
                ScriptingRow(
                    f"Could not read {path.name}",
                    "Nothing in it is listed. Check that it is a file you can read.",
                    open_file=None,
                    file=path.name,
                ),
            )
        for gap in scan.gaps:
            match gap:
                case IndirectUse(kind=kind, path=path, line=line):
                    row = ScriptingRow(
                        f"{SPELLING[kind]} is used through another name",
                        f"{self._where(path, line)} · calls made that way are not listed",
                        open_file=self._opener(path),
                        file=path.name,
                    )
                case UnfinishedText(path=path, line=line):
                    row = ScriptingRow(
                        f"Stopped reading {path.name} at line {line}",
                        "A comment or string starts there and never closes, so nothing "
                        "after it is listed.",
                        open_file=self._opener(path),
                        file=path.name,
                    )
            self._add_row(lead, row)

    def _hit_row(self, hit: ScriptingHit) -> ScriptingRow:
        return ScriptingRow(
            hit.name or UNNAMED[hit.kind],
            self._where(hit.path, hit.line),
            open_file=self._opener(hit.path),
            file=hit.path.name,
        )

    def _where(self, path: Path, line: int) -> str:
        """`user.lua:12`, or `hyprtweaker/legacy.lua:3`: the path from the hypr folder."""
        try:
            shown = path.relative_to(self._session.paths.hypr_dir)
        except ValueError:
            shown = path
        return f"{shown}:{line}"

    def _opener(self, path: Path) -> Callable[[], None]:
        return lambda: self._actions.open_file(path)

    def _add_group(self, group: Adw.PreferencesGroup) -> None:
        self._page.add(group)
        self._inventory.append(group)

    def _add_row(self, group: Adw.PreferencesGroup, row: ScriptingRow) -> None:
        group.add(row.widget)
        self._listed.append((group.get_title(), row))


__all__ = ["ScriptingActions", "ScriptingPage", "ScriptingRow"]
