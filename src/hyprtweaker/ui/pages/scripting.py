"""The Scripting Page: the plugin load list, then what user.lua and legacy.lua do (ADR-0018).

**Plugins** (#174) come first and are the one editable part: the ordered `plugins.lua`
list, with add, remove, an enable switch and the drag/Alt+Up/Alt+Down reorder the Rules
Pages use. Each row says whether Hyprland has the plugin loaded, asked fresh on every
refresh (`Session.fetch_loaded_plugins`), because a reload can load or unload one.

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

import enum
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GObject, Gtk  # noqa: E402

from hyprtweaker.engine.model.entities import PluginLoad  # noqa: E402
from hyprtweaker.engine.scripting import (  # noqa: E402
    CallKind,
    IndirectUse,
    LoadsFile,
    ScriptingHit,
    ScriptingScan,
    UnfinishedText,
    UnsearchedFile,
    scan_scripting,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.pages.rules import REORDER_HINT  # noqa: E402
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

LEAD_TITLE = "In your Lua files"
CAVEAT = (
    "Found by reading user.lua and legacy.lua only, not the files they load. Calls built "
    "at runtime, in loops or through other names may not appear."
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


PLUGINS_TITLE = "Plugins"
PLUGINS_DESCRIPTION = "Loaded in this order each time Hyprland reads its config."
PLUGINS_EMPTY = "No plugins load from this app. Add a plugin's .so file to load it at startup."
ADD_PLUGIN = "Add plugin"


class PluginStatus(enum.Enum):
    """What one row says about its plugin, beside the switch."""

    LOADED = "Loaded"
    NOT_LOADED = "Not loaded"
    MISSING = "File not found"
    UNKNOWN = ""


STATUS_TIPS: dict[PluginStatus, str] = {
    PluginStatus.LOADED: "Hyprland has this plugin loaded now.",
    PluginStatus.NOT_LOADED: (
        "Hyprland did not load this file. Check that it was built for your Hyprland version."
    ),
    PluginStatus.MISSING: "There is no file at this path, so Hyprland skips it.",
    PluginStatus.UNKNOWN: "",
}


def plugin_status(
    plugin: PluginLoad, loaded: frozenset[str] | None, *, exists: Callable[[str], bool]
) -> PluginStatus:
    """A missing file first (it can never load), then the compositor's answer, if any.

    `loaded` holds the loaded names lower-cased, or `None` when nobody answered. A disabled
    entry Hyprland has loaded anyway -- `user.lua` loads it too -- still says "Loaded":
    the marker reports the compositor, the switch reports the list.
    """
    if not exists(plugin.path):
        return PluginStatus.MISSING
    if loaded is None:
        return PluginStatus.UNKNOWN
    if plugin.name in loaded:
        return PluginStatus.LOADED
    return PluginStatus.NOT_LOADED if plugin.enabled else PluginStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class PluginActions:
    """The plugin list's verbs; every index is into `Session.declarations("plugins")`."""

    add: Callable[[], None]
    remove: Callable[[int], None]
    enable: Callable[[int, bool], None]
    move: Callable[[int, int], None]


class PluginRow:
    """One entry: drag handle, file name over its path, loaded marker, switch, remove.

    The reorder is the Rules Pages' (ADR-0008): the handle is the drag source, the row the
    drop target, and Alt+Up / Alt+Down move it past its neighbour.
    """

    def __init__(
        self,
        plugin: PluginLoad,
        index: int,
        count: int,
        *,
        status: PluginStatus,
        actions: PluginActions | None,
    ) -> None:
        self.plugin = plugin
        self.index = index
        name = plugin.path.rsplit("/", 1)[-1] or plugin.path
        # A path is user text: as Pango markup an `&` renders blank.
        self.widget = Adw.ActionRow(title=name, subtitle=plugin.path, use_markup=False)
        self.widget.set_subtitle_selectable(True)

        self.handle = Gtk.Image.new_from_icon_name("list-drag-handle-symbolic")
        self.handle.add_css_class("dim-label")
        self.widget.add_prefix(self.handle)

        self.status = Gtk.Label(label=status.value, css_classes=["caption"])
        self.status.set_tooltip_text(STATUS_TIPS[status] or None)
        self.status.set_visible(status is not PluginStatus.UNKNOWN)
        if status in (PluginStatus.MISSING, PluginStatus.NOT_LOADED):
            self.status.add_css_class("warning")
        elif status is PluginStatus.LOADED:
            self.status.add_css_class("success")
        self.widget.add_suffix(self.status)
        if not plugin.enabled:
            self.widget.add_css_class("dim-label")

        self.enabled_switch = Gtk.Switch(active=plugin.enabled, valign=Gtk.Align.CENTER)
        self.enabled_switch.set_tooltip_text("Load this plugin")
        self.enabled_switch.set_sensitive(actions is not None)
        self.widget.add_suffix(self.enabled_switch)

        self.remove_button: Gtk.Button | None = None
        if actions is None:
            return
        self.enabled_switch.connect("state-set", self._on_switch, actions)
        remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove from the list")
        remove.connect("clicked", lambda _button: actions.remove(index))
        self.widget.add_suffix(remove)
        self.remove_button = remove

        self.handle.set_tooltip_text(REORDER_HINT)
        self._wire_keys(actions, count)
        self._wire_drag(actions)

    def _on_switch(self, _switch: Gtk.Switch, state: bool, actions: PluginActions) -> bool:
        actions.enable(self.index, state)
        # Handled: the refresh rebuilds the row from the list, which is the truth.
        return True

    def _wire_keys(self, actions: PluginActions, count: int) -> None:
        above = self.index - 1 if self.index > 0 else None
        below = self.index + 1 if self.index + 1 < count else None
        keys = Gtk.ShortcutController()
        for accelerator, neighbour in (("<Alt>Up", above), ("<Alt>Down", below)):
            keys.add_shortcut(
                Gtk.Shortcut.new(
                    Gtk.ShortcutTrigger.parse_string(accelerator),
                    Gtk.CallbackAction.new(self._on_step, neighbour, actions),
                )
            )
        self.widget.add_controller(keys)

    def _on_step(
        self, widget: Gtk.Widget, _args: object, neighbour: int | None, actions: PluginActions
    ) -> bool:
        if neighbour is None:
            widget.error_bell()  # already first (or last)
            return True
        actions.move(self.index, neighbour)
        return True

    def _wire_drag(self, actions: PluginActions) -> None:
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect(
            "prepare",
            lambda *_: Gdk.ContentProvider.new_for_value(
                GObject.Value(GObject.TYPE_INT, self.index)
            ),
        )
        self.handle.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)
        target.connect("drop", self._on_drop, actions)
        self.widget.add_controller(target)

    def _on_drop(
        self, _target: Gtk.DropTarget, value: int, _x: float, _y: float, actions: PluginActions
    ) -> bool:
        origin = int(value)
        if origin == self.index:
            return False
        actions.move(origin, self.index)
        return True


class PluginsGroup:
    """The `plugins.lua` list as one group: rows, the empty state, and what else is loaded.

    `footer` is the slot #175 fills with how plugin settings appear; empty and hidden here.
    """

    def __init__(self, session: Session, *, actions: PluginActions | None) -> None:
        self._session = session
        self._actions = actions
        self._loaded: frozenset[str] | None = None
        self._loaded_names: tuple[str, ...] = ()
        self.rows: list[PluginRow] = []

        self.group = Adw.PreferencesGroup(title=PLUGINS_TITLE, description=PLUGINS_DESCRIPTION)
        self.add_button = Gtk.Button(label=ADD_PLUGIN, valign=Gtk.Align.CENTER)
        self.add_button.set_tooltip_text("Choose a plugin's .so file")
        if actions is not None:
            self.add_button.connect("clicked", lambda _button: actions.add())
        self.group.set_header_suffix(self.add_button)

        self.empty_row = Adw.ActionRow(title=PLUGINS_EMPTY, use_markup=False)
        self.empty_row.add_css_class("dim-label")
        self.also_loaded = Adw.ActionRow(
            subtitle="Loaded by Hyprland, but not from this list", use_markup=False
        )
        self.footer = Gtk.Label(
            wrap=True, xalign=0, css_classes=["dim-label", "caption"], visible=False
        )
        self.footer.set_margin_top(12)
        self.group.add(self.footer)

    def refresh(self) -> None:
        """Rebuild the rows from the list, then ask Hyprland what it has loaded."""
        self._render()
        self._session.fetch_loaded_plugins(self._on_loaded)

    def _on_loaded(self, names: tuple[str, ...] | None) -> None:
        loaded = None if names is None else frozenset(name.lower() for name in names)
        if loaded == self._loaded and (names or ()) == self._loaded_names:
            return
        self._loaded = loaded
        self._loaded_names = names or ()
        self._render()

    def _render(self) -> None:
        for row in self.rows:
            self.group.remove(row.widget)
            release(row.widget)
        for widget in (self.empty_row, self.also_loaded):
            if widget.get_parent() is not None:
                self.group.remove(widget)

        plugins: list[PluginLoad] = self._session.declarations("plugins")
        editable = self._actions is not None and self._session.live
        self.add_button.set_sensitive(editable)
        self.rows = [
            PluginRow(
                plugin,
                index,
                len(plugins),
                status=plugin_status(plugin, self._loaded, exists=_is_file),
                actions=self._actions if editable else None,
            )
            for index, plugin in enumerate(plugins)
        ]
        for row in self.rows:
            self.group.add(row.widget)

        self.empty_row.set_visible(not plugins)
        if not plugins:
            self.group.add(self.empty_row)
        listed = {plugin.name for plugin in plugins}
        others = [name for name in self._loaded_names if name.lower() not in listed]
        self.also_loaded.set_title(f"Also loaded: {', '.join(others)}")
        self.also_loaded.set_visible(bool(others))
        if others:
            self.group.add(self.also_loaded)


def _is_file(path: str) -> bool:
    try:
        return Path(path).is_file()
    except OSError:
        return False


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

    def __init__(
        self,
        session: Session,
        *,
        actions: ScriptingActions,
        plugin_actions: PluginActions | None = None,
    ) -> None:
        self._session = session
        self._actions = actions
        self._page = Adw.PreferencesPage(title=self.title)
        # First, and outside `_inventory`, so the inventory's rebuild never moves it.
        self._plugins = PluginsGroup(session, actions=plugin_actions)
        self._page.add(self._plugins.group)
        self._inventory: list[Adw.PreferencesGroup] = []
        self._listed: list[tuple[str, ScriptingRow]] = []
        self._hit_count = 0
        self.refresh()

    @property
    def plugins(self) -> PluginsGroup:
        """The editable plugin load list at the top of the Page (#174)."""
        return self._plugins

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def hit_count(self) -> int:
        """How many calls the last read found: the sidebar's count."""
        return self._hit_count

    def listed_rows(self) -> tuple[tuple[str, ScriptingRow], ...]:
        """Every inventory row with its group's title, in Page order. The UI tier's view."""
        return tuple(self._listed)

    def refresh(self) -> None:
        """Rebuild the plugin list, read the files again, rebuild the inventory in place."""
        self._plugins.refresh()
        for group in self._inventory:
            self._page.remove(group)
            release(group)
        self._inventory = []
        self._listed = []

        lead = Adw.PreferencesGroup(title=LEAD_TITLE, description=CAVEAT)
        self._add_group(lead)
        scan = scan_scripting(self._session.paths)
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
                case LoadsFile(path=path, line=line):
                    row = ScriptingRow(
                        "Loads another file; calls in it are not listed",
                        self._where(path, line),
                        open_file=self._opener(path),
                        file=path.name,
                    )
                case UnsearchedFile(path=path):
                    row = ScriptingRow(
                        f"Could not search {path.name}",
                        "Nothing in it is listed. This list is for reading only, so your "
                        "settings are not affected.",
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


__all__ = [
    "PluginActions",
    "PluginRow",
    "PluginStatus",
    "PluginsGroup",
    "ScriptingActions",
    "ScriptingPage",
    "ScriptingRow",
    "plugin_status",
]
