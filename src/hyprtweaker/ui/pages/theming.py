"""The Theming page: where colours come from, and the tools that make them (ADR-0014, #164).

**Color source.** The first group always shows the one Color source -- `Wallpaper
(matugen)`, `Wallpaper (wallust)`, `Preset` or `Manual` -- read off the Entrypoint on every
refresh, never kept (`Session.color_source`), and says in one sentence what that means,
naming any other tool that still sets colours. Under Preset or Manual it offers "Resume
wallpaper colors" when a backend is set up.

**Backends.** matugen and wallust are tabs (a toggle in the group header). Exactly one is in
use; looking at the other tab changes nothing. Its "Switch to <tool>" opens a confirm and only
the confirm's verb changes the source: one Entrypoint transaction, which also sets the tool up
when it is not yet (`wire` with `Session.add_bridge(tool, source=...)`), after the confirm
showed every file that changes. The tab in use offers Regenerate (the registry builds the
argv; the injected `run` runs it), its options, and Remove. With neither tool found, one
sentence and "Check again" stand where the tabs would.

**Other tools.** noctalia, DMS and shell-switch, when found: Set up and Remove behind the
same confirm, their on/off read from their entries, and the sentence for a version this app
cannot load.

**Presets.** The group below the rest (`theming_presets.py`, #171) saves, applies, exports and
imports Presets; `reveal_preset(slug)` is its entry point for a search hit.

**Rebuilt in place.** The groups are made once and only their rows are replaced on refresh,
each released (#219), so a group another ticket adds below (`add_group`) never moves. Nothing
here runs a tool unless the user pressed Regenerate or agreed to a command a confirm named.
"""

from __future__ import annotations

import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from hyprtweaker.engine.bridge import (  # noqa: E402
    REGISTRY,
    BridgeEntry,
    Choice,
    ColorSource,
    ManualColors,
    Number,
    PresetColors,
    Several,
    Wallpaper,
)
from hyprtweaker.engine.bridge.wire import (  # noqa: E402
    ChangedFile,
    IfChanged,
    NeedsChoice,
    NotDone,
    ToolDetection,
    Unwired,
    WireConsent,
    Wired,
    WirePlan,
    detect,
    plan_wire,
    unwire,
    wire,
)
from hyprtweaker.engine.tools import (  # noqa: E402
    ToolRefused,
    ToolRun,
    ToolTimedOut,
    find_tool,
    run_tool,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.dialogs.wire_consent import ConsentDialog  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.tasks import entity_page_id  # noqa: E402
from hyprtweaker.ui.pages.theming_presets import PresetActions, PresetsGroup  # noqa: E402
from hyprtweaker.ui.pages.theming_state import (  # noqa: E402
    BACKENDS,
    IN_USE_STATES,
    OTHER_TOOLS,
    Tab,
    TabState,
    backend_tab,
    changed_since_setup,
    has_backend,
    other_tool,
    resume_target,
    source_detail,
    source_line,
)
from hyprtweaker.ui.release import release  # noqa: E402

SOURCE_TITLE = "Colors"
SOURCE_ROW = "Color source"
BACKENDS_TITLE = "Wallpaper colors"
BACKENDS_DESCRIPTION = "One tool at a time makes your colors from your wallpaper."
OPTIONS_TITLE = "Regenerate options"
OTHER_TITLE = "Other tools"
OTHER_DESCRIPTION = "Shells and tools that send their own settings to Hyprland."
EMPTY = (
    "No wallpaper color tool found. Install matugen or wallust to take colors from your "
    "wallpaper."
)
CHECK_AGAIN = "Check again"
RESUME = "Resume wallpaper colors"
REGENERATE = "Regenerate colors"
REGENERATE_ENTRYPOINT = "Regenerate hyprland.lua…"
RUN_TIMEOUT = 120.0
"""Seconds a Regenerate may run: matugen and wallust take about one on a large image."""

WALLPAPER_PLACEHOLDER = "<your wallpaper>"
NOT_UPDATED = "hyprland.lua could not be updated right now, so nothing was changed."

Find = Callable[[str], Path | None]
Run = Callable[..., ToolRun]
ChooseImage = Callable[[Gtk.Widget, Callable[[Path | None], None]], None]


def choose_image(parent: Gtk.Widget, done: Callable[[Path | None], None]) -> None:
    """Ask for a wallpaper image with the system file chooser."""
    images = Gtk.FileFilter()
    images.set_name("Images")
    images.add_mime_type("image/*")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(images)
    dialog = Gtk.FileDialog(title="Choose your wallpaper", filters=filters)
    root = parent.get_root()

    def answered(chooser: Gtk.FileDialog, result: Gio.AsyncResult) -> None:
        try:
            chosen = chooser.open_finish(result)
        except GLib.Error:
            done(None)
            return
        path = chosen.get_path() if chosen is not None else None
        done(Path(path) if path else None)

    dialog.open(root if isinstance(root, Gtk.Window) else None, None, answered)


@dataclass(frozen=True, slots=True)
class ThemingActions:
    """What the window lends the page. Tests and probes pass fakes for the tool seams."""

    toast: Callable[[str], None]
    current_wallpaper: Callable[[], Path | None] = field(default=lambda: None)
    """The wallpaper on screen, when a daemon says (#170); `None` asks with a chooser."""
    choose_image: ChooseImage = choose_image
    find: Find = find_tool
    run: Run = run_tool
    presets: PresetActions = field(default_factory=PresetActions)
    """What the Presets group asks of the window: apply with its toast, the remembered
    colour answer, file choosers (#171)."""


def _row(title: str, subtitle: str = "") -> Adw.ActionRow:
    """A row of plain text: a path or a tool's message may hold `&`."""
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def _button(label: str, on_click: Callable[[], None], *, sensitive: bool = True) -> Gtk.Button:
    button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
    button.set_sensitive(sensitive)
    button.connect("clicked", lambda _button: on_click())
    return button


class ThemingPage:
    """The Theming module's Page (ADR-0014)."""

    title = "Theming"
    section = entity_page_id("theming")

    def __init__(self, session: Session, *, actions: ThemingActions) -> None:
        self._session = session
        self._actions = actions
        self._page = Adw.PreferencesPage(title=self.title)
        self._source = Adw.PreferencesGroup(title=SOURCE_TITLE)
        self._backends = Adw.PreferencesGroup(
            title=BACKENDS_TITLE, description=BACKENDS_DESCRIPTION
        )
        self._options = Adw.PreferencesGroup(title=OPTIONS_TITLE)
        self._other = Adw.PreferencesGroup(title=OTHER_TITLE, description=OTHER_DESCRIPTION)
        # Linked toggle buttons rather than an `Adw.ToggleGroup`: releasing a ToggleGroup
        # whose toggles Python has touched crashes the process when they are collected.
        self._tabs = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["linked"])
        self._tab_buttons: dict[str, Gtk.ToggleButton] = {}
        for tool in BACKENDS:
            button = Gtk.ToggleButton(label=REGISTRY[tool].title)
            if self._tab_buttons:
                button.set_group(next(iter(self._tab_buttons.values())))
            button.connect("toggled", self._on_tab, tool)
            self._tabs.append(button)
            self._tab_buttons[tool] = button
        self._backends.set_header_suffix(self._tabs)
        self._presets = PresetsGroup(session, actions.presets)
        for group in (self._source, self._backends, self._options, self._other):
            self._page.add(group)
        self._page.add(self._presets.group)
        self._rows: dict[Adw.PreferencesGroup, list[Gtk.Widget]] = {
            group: [] for group in (self._source, self._backends, self._options, self._other)
        }
        self._shown: str | None = None
        self._values: dict[str, dict[str, str | float | None]] = {
            tool: {each.key: each.default for each in REGISTRY[tool].parameters}
            for tool in BACKENDS
        }
        self._running: str | None = None
        self._tab_rows: dict[str, Gtk.Widget] = {}
        self._other_rows: dict[str, Adw.ActionRow] = {}
        self._regenerate: Adw.ActionRow | None = None
        self._quiet = False
        self.dialog: Adw.AlertDialog | None = None
        """The confirm or message last shown: what a test or probe answers."""
        self._state = self._read()
        self.refresh()

    # --- what a test or probe reads -------------------------------------------------------

    @property
    def page(self) -> Adw.PreferencesPage:
        return self._page

    @property
    def color_source_text(self) -> str:
        return source_line(self._state.source)

    @property
    def color_source_detail(self) -> str:
        return self._source.get_description() or ""

    @property
    def tabs(self) -> tuple[str, ...]:
        """The tab labels, in order, or `()` when the empty state stands instead."""
        if not self._tabs.get_visible():
            return ()
        return tuple(button.get_label() or "" for button in self._tab_buttons.values())

    @property
    def shown_tab(self) -> str | None:
        return self._shown if self._tabs.get_visible() else None

    @property
    def rows(self) -> tuple[tuple[str, str, str], ...]:
        """Every row the page drew: (group title, row title, row subtitle), in order."""
        listed = []
        for group, rows in self._rows.items():
            if not group.get_visible():
                continue
            for row in rows:
                if isinstance(row, Adw.PreferencesRow):
                    subtitle = row.get_subtitle() if isinstance(row, Adw.ActionRow) else ""
                    listed.append((group.get_title(), row.get_title(), subtitle or ""))
        return tuple(listed)

    def button(self, label: str) -> Gtk.Button | None:
        """The visible button reading `label`, wherever on the page it is."""
        pending: list[Gtk.Widget] = [self._page]
        while pending:
            widget = pending.pop()
            if not widget.get_visible():
                continue
            if isinstance(widget, Gtk.Button) and widget.get_label() == label:
                return widget
            child = widget.get_first_child()
            while child is not None:
                pending.append(child)
                child = child.get_next_sibling()
        return None

    @property
    def set_up_count(self) -> int:
        """How many tools are set up here: the sidebar's count."""
        return len({entry.tool for entry in self._state.entries})

    @property
    def running(self) -> str | None:
        """The tool a Regenerate is running, while it runs."""
        return self._running

    # --- the API other tickets build on ---------------------------------------------------

    def add_group(self, group: Adw.PreferencesGroup) -> None:
        """Add a group below the page's own (Presets, #171; the wallpaper, #170).

        The page's groups are made once and only their rows are rebuilt, so a group added
        here stays where it was put across every refresh. Its owner refreshes it.
        """
        self._page.add(group)

    def reveal_backend(self, tool: str) -> Gtk.Widget | None:
        """Show `tool` and flash where it is, for a "Set by <tool>" pill (#165, settled S8).

        matugen and wallust select their tab; any other tool's row in "Other tools". The
        widget is returned for the window to scroll to. `None` (an unknown tool) leaves the
        page at its top.
        """
        if tool in BACKENDS:
            if self._tabs.get_visible():
                self._show(tool)
            target = self._tab_rows.get(tool) or self._first(self._backends)
        else:
            target = self._other_rows.get(tool)
        if target is not None:
            flash(target)
        return target

    @property
    def presets(self) -> PresetsGroup:
        """The Presets group (#171): its rows, buttons and dialogs, for the window and tests."""
        return self._presets

    def reveal_preset(self, slug: str) -> Gtk.Widget | None:
        """Flash Preset `slug`'s row in the Presets group and return it, for a search hit
        (#172, settled S8). `None` when there is no such Preset."""
        self._presets.refresh()
        return self._presets.reveal_preset(slug)

    # --- refresh --------------------------------------------------------------------------

    def refresh(self) -> None:
        """Read everything again and redraw: detection, the Manifest, the Entrypoint."""
        self._state = self._read()
        self._draw_backends()
        self._draw_source()
        self._draw_other()
        self._presets.refresh()

    def _read(self) -> _Read:
        manifest = self._session.manifest()
        hypr = self._session.paths.hypr_dir
        entries = manifest.bridges
        return _Read(
            source=self._session.color_source(),
            entries=entries,
            present=frozenset(e.file for e in entries if (hypr / e.file).is_file()),
            detections={
                tool: detect(
                    tool, paths=self._session.paths, manifest=manifest, find=self._actions.find
                )
                for tool in REGISTRY
            },
            blocked=self._session.color_source_blocked,
        )

    def _clear(self, group: Adw.PreferencesGroup) -> None:
        for row in self._rows[group]:
            group.remove(row)
            release(row)
        self._rows[group] = []

    def _add(self, group: Adw.PreferencesGroup, row: Gtk.Widget) -> None:
        group.add(row)
        self._rows[group].append(row)

    def _first(self, group: Adw.PreferencesGroup) -> Gtk.Widget | None:
        rows = self._rows[group]
        return rows[0] if rows else None

    def _draw_source(self) -> None:
        state = self._state
        self._clear(self._source)
        self._source.set_description(source_detail(state.source, state.entries))
        self._add(self._source, _row(SOURCE_ROW, source_line(state.source)))
        if state.blocked is not None:
            row = _row("Where colors come from cannot change now", state.blocked)
            if self._session.live and self._session.entrypoint_edited:
                row.add_suffix(_button(REGENERATE_ENTRYPOINT, self._regenerate_entrypoint))
            self._add(self._source, row)
        target = resume_target(state.source, state.entries, shown=self._shown or BACKENDS[0])
        if target is not None:
            row = _row(RESUME, f"Let {REGISTRY[target].title} make your colors again.")
            row.add_suffix(
                _button("Resume", lambda: self._resume(target), sensitive=state.blocked is None)
            )
            self._add(self._source, row)

    def _draw_backends(self) -> None:
        state = self._state
        self._clear(self._backends)
        self._tab_rows = {}
        self._regenerate = None
        found = has_backend(state.detections[tool] for tool in BACKENDS)
        self._tabs.set_visible(found)
        if not found:
            self._clear(self._options)
            self._options.set_visible(False)
            row = _row(EMPTY)
            row.add_suffix(_button(CHECK_AGAIN, self.refresh))
            self._add(self._backends, row)
            return
        tabs = {tool: self._tab(tool) for tool in BACKENDS}
        for tool, button in self._tab_buttons.items():
            button.set_label(tabs[tool].label)
        if self._shown is None:
            in_use = [tool for tool, tab in tabs.items() if tab.state in IN_USE_STATES]
            listed = [
                tool for tool, tab in tabs.items() if tab.state is not TabState.NOT_INSTALLED
            ]
            self._shown = (in_use or listed or list(BACKENDS))[0]
        self._select_tab(self._shown)
        self._draw_tab(tabs[self._shown])

    def _tab(self, tool: str) -> Tab:
        state = self._state
        return backend_tab(
            state.detections[tool], state.entries, state.source, present=state.present
        )

    def _select_tab(self, tool: str) -> None:
        """Press `tool`'s tab without the press counting as the user's."""
        self._quiet = True
        self._tab_buttons[tool].set_active(True)
        self._quiet = False

    def _on_tab(self, button: Gtk.ToggleButton, tool: str) -> None:
        if self._quiet or not button.get_active() or tool == self._shown:
            return
        self._show(tool)

    def _show(self, tool: str) -> None:
        """Look at `tool`'s tab. Changes nothing but what is on screen."""
        self._shown = tool
        self._draw_source()
        self._clear(self._backends)
        self._tab_rows = {}
        self._regenerate = None
        self._select_tab(tool)
        self._draw_tab(self._tab(tool))

    def _draw_tab(self, tab: Tab) -> None:
        spec = REGISTRY[tab.tool]
        title = spec.title
        blocked = self._state.blocked is not None
        match tab.state:
            case TabState.IN_USE:
                status = _row("In use", source_detail(Wallpaper(tab.tool), ()))
            case TabState.WAITING:
                status = _row(
                    f"Waiting for {title}'s first run",
                    "Its colors load once it has made them: press Regenerate, or change "
                    "your wallpaper the way you usually do.",
                )
            case TabState.LOAD_NOW:
                status = _row(
                    f"{title}'s colors are ready",
                    "They load into Hyprland when you press Load now.",
                )
                status.add_suffix(_button("Load now", self._load_now, sensitive=not blocked))
            case TabState.NOT_IN_USE:
                lead = f"Colors come from {_source_words(self._state.source)} now."
                if tab.wired:
                    status = _row("Not in use", f"{lead} {title} stays set up for a switch.")
                else:
                    status = _row(
                        "Not set up",
                        f"{lead} Switching sets {title} up to make Hyprland colors; you see "
                        "every file it changes first.",
                    )
                status.add_suffix(
                    _button(
                        f"Switch to {title}",
                        lambda: self.switch(tab.tool),
                        sensitive=not blocked,
                    )
                )
            case TabState.NOT_INSTALLED:
                status = _row(
                    f"{title} is not installed",
                    "Install it to take colors from your wallpaper with it, then check again.",
                )
                status.add_suffix(_button(CHECK_AGAIN, self.refresh))
        self._add(self._backends, status)
        self._tab_rows[tab.tool] = status

        if tab.state in IN_USE_STATES and spec.rerun:
            self._add(self._backends, self._regenerate_row(tab.tool))
        if tab.wired:
            row = _row(
                f"Remove {title}",
                "Stops loading its colors and puts back the files changed when it was set up.",
            )
            row.add_suffix(
                _button("Remove…", lambda: self.remove(tab.tool), sensitive=not blocked)
            )
            self._add(self._backends, row)
        self._draw_options(tab)

    def _regenerate_row(self, tool: str) -> Adw.ActionRow:
        row = _row(REGENERATE, self._command_text(tool))
        row.add_suffix(
            _button(
                "Running…" if self._running == tool else "Regenerate",
                lambda: self.regenerate(tool),
                sensitive=self._running is None,
            )
        )
        self._regenerate = row
        return row

    def _command_text(self, tool: str) -> str:
        """What Regenerate runs, as the user could type it (S3: the subtitle says it). The
        image is found when the button is pressed: asking the daemon runs a program."""
        spec = REGISTRY[tool]
        argv = spec.rerun_argv(
            Path(spec.detection.binaries[0]), Path(WALLPAPER_PLACEHOLDER), self._values[tool]
        )
        return f"Runs {shlex.join(argv)}"

    def _draw_options(self, tab: Tab) -> None:
        self._clear(self._options)
        parameters = REGISTRY[tab.tool].parameters
        visible = bool(parameters) and tab.state is not TabState.NOT_INSTALLED
        self._options.set_visible(visible)
        if not visible:
            return
        self._options.set_description(
            f"Used when this app runs {REGISTRY[tab.tool].title} for you, until you close "
            "it. Your own wallpaper script keeps its own settings."
        )
        values = self._values[tab.tool]
        for parameter in parameters:
            match parameter:
                case Choice():
                    self._add(self._options, self._choice_row(tab.tool, parameter, values))
                case Number():
                    self._add(self._options, self._number_row(tab.tool, parameter, values))

    def _choice_row(
        self, tool: str, parameter: Choice, values: dict[str, str | float | None]
    ) -> Adw.ActionRow:
        # A drop-down in a plain row, as the Option Rows do: an `Adw.ComboRow` released on
        # refresh logs a Gtk CRITICAL when it is finalized (its list view is already gone).
        row = _row(parameter.title)
        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new(list(parameter.choices)), valign=Gtk.Align.CENTER
        )
        current = values.get(parameter.key, parameter.default)
        if current in parameter.choices:
            dropdown.set_selected(parameter.choices.index(str(current)))

        def chosen(drop: Gtk.DropDown, _spec: object) -> None:
            values[parameter.key] = parameter.choices[drop.get_selected()]
            self._redraw_regenerate(tool)

        dropdown.connect("notify::selected", chosen)
        row.add_suffix(dropdown)
        row.set_activatable_widget(dropdown)
        return row

    def _number_row(
        self, tool: str, parameter: Number, values: dict[str, str | float | None]
    ) -> Adw.ExpanderRow:
        """A number the tool may also leave to its own default: a switch turns it on."""
        row = Adw.ExpanderRow(show_enable_switch=True)
        row.set_use_markup(False)
        row.set_title(parameter.title)
        title = REGISTRY[tool].title
        current = values.get(parameter.key)
        spin = Adw.SpinRow.new_with_range(parameter.minimum, parameter.maximum, 0.1)
        spin.set_use_markup(False)
        spin.set_title("Value")
        spin.set_digits(1)
        spin.set_value(float(current) if current is not None else 0.0)
        row.add_row(spin)
        row.set_enable_expansion(current is not None)
        row.set_subtitle("" if current is not None else f"{title}'s own default")

        def changed(*_args: object) -> None:
            on = row.get_enable_expansion()
            values[parameter.key] = spin.get_value() if on else None
            row.set_subtitle("" if on else f"{title}'s own default")
            self._redraw_regenerate(tool)

        row.connect("notify::enable-expansion", changed)
        spin.connect("notify::value", changed)
        return row

    def _redraw_regenerate(self, tool: str) -> None:
        """The Regenerate row's command follows the options as they change."""
        if self._regenerate is not None:
            self._regenerate.set_subtitle(self._command_text(tool))

    def _draw_other(self) -> None:
        state = self._state
        self._clear(self._other)
        self._other_rows = {}
        blocked = state.blocked is not None
        listed = [
            state.detections[tool] for tool in OTHER_TOOLS if state.detections[tool].found
        ]
        self._other.set_visible(bool(listed))
        for detection in listed:
            tool_state = other_tool(detection, state.entries, present=state.present)
            row = _row(tool_state.title, tool_state.status)
            for label, wanted, act in (
                ("Load now", tool_state.load, self._load_now),
                ("Set up…", tool_state.setup, lambda t=detection.tool: self.set_up(t)),
                ("Remove…", tool_state.remove, lambda t=detection.tool: self.remove(t)),
            ):
                if wanted:
                    row.add_suffix(_button(label, act, sensitive=not blocked))
            if tool_state.patch:
                row.add_suffix(
                    _button("Copy patch", lambda t=detection.tool: self._copy_patch(t))
                )
            self._add(self._other, row)
            self._other_rows[detection.tool] = row

    # --- actions --------------------------------------------------------------------------

    def switch(self, tool: str) -> None:
        """ "Switch to <tool>": one confirm, then one transaction (setting it up if need be).

        A tool that has not made its colors yet is run once in the same agreement when the
        wallpaper daemon says what is on screen (S3: the confirm names the command); asking
        runs the daemon's client, so the confirm waits for that answer off the main loop.
        """
        spec = REGISTRY[tool]
        title = spec.title
        plan: WirePlan | None = None
        if not any(entry.tool == tool for entry in self._state.entries):
            planned = plan_wire(tool, paths=self._session.paths, find=self._actions.find)
            if isinstance(planned, NotDone):
                self._tell(f"{title} cannot be set up", planned.reason)
                return
            plan = planned
        binary = self._actions.find(spec.detection.binaries[0])
        has_run = (self._session.paths.hypr_dir / spec.modules[0].file).is_file()
        if has_run or binary is None or not spec.rerun:
            self._confirm_switch(tool, plan, None)
            return
        current = self._actions.current_wallpaper

        def look() -> None:
            image = current()
            argv = spec.rerun_argv(binary, image, self._values[tool]) if image else None
            GLib.idle_add(self._confirm_switch, tool, plan, argv)

        threading.Thread(target=look, name=f"wallpaper-{tool}", daemon=True).start()

    def _confirm_switch(
        self, tool: str, plan: WirePlan | None, command: tuple[str, ...] | None
    ) -> bool:
        title = REGISTRY[tool].title
        body = (
            f"{title} will make your border and group colors from your wallpaper, instead of "
            f"{_source_words(self._state.source)}."
        )
        if plan is None:
            body += " Nothing outside hyprtweaker's own files changes."
        elif not plan.files:
            body += f" No file of {title}'s needs to change."
        has_run = (self._session.paths.hypr_dir / REGISTRY[tool].modules[0].file).is_file()
        if not has_run and command is None:
            body += (
                f" Its colors load after its first run: once switched, press Regenerate on "
                f"{title}'s tab."
            )
        self._confirm(
            ConsentDialog(
                heading=f"Switch to {title}?",
                body=body,
                verb=f"Switch to {title}",
                plan=plan,
                intro=f"To set {title} up, these files change:" if plan and plan.files else "",
                command=command,
                on_agree=lambda: self._switched(tool, plan, command),
            )
        )
        return False

    def _switched(
        self, tool: str, plan: WirePlan | None, command: tuple[str, ...] | None
    ) -> None:
        title = REGISTRY[tool].title
        self._shown = tool
        if plan is None:
            switched = self._session.set_color_source(Wallpaper(tool))
            if not switched:
                self._tell(f"Could not switch to {title}", self._why())
        else:
            done = wire(
                plan,
                WireConsent(plan),
                register=lambda t: self._session.add_bridge(t, source=Wallpaper(t)),
                unregister=self._session.remove_bridge,
            )
            self._report_wired(done)
            switched = isinstance(done, Wired)
        if switched and command is not None:
            # The command the confirm named, and nothing else: argv[0] is the tool found.
            self._running = tool
            self._start(tool, command)
        self.refresh()

    def set_up(self, tool: str) -> None:
        """ "Set up" for a tool without a tab: the plan's confirm, then `wire`."""
        spec = REGISTRY[tool]
        planned = plan_wire(tool, paths=self._session.paths, find=self._actions.find)
        if isinstance(planned, NotDone):
            self._tell(f"{spec.title} cannot be set up", planned.reason)
            return
        if spec.sets_colors:
            lead = f"Hyprland will load the colors {spec.title} makes, over your own."
        else:
            lead = f"Hyprland will load what {spec.title} writes for it."
        self._confirm(
            ConsentDialog(
                heading=f"Set up {spec.title}?",
                body=lead if planned.files else f"{lead} No file of its needs to change.",
                verb=f"Set up {spec.title}",
                plan=planned,
                intro="These files change:" if planned.files else "",
                on_agree=lambda: self._set_up(planned),
            )
        )

    def _set_up(self, plan: WirePlan) -> None:
        self._report_wired(
            wire(
                plan,
                WireConsent(plan),
                register=self._session.add_bridge,
                unregister=self._session.remove_bridge,
            )
        )
        self.refresh()

    def _report_wired(self, done: Wired | NotDone) -> None:
        title = REGISTRY[done.tool].title
        if isinstance(done, NotDone):
            self._tell(f"{title} was not set up", done.reason)
        elif done.written:
            self._actions.toast(f"{title} is set up. Copies of the files it changed are kept.")
        else:
            self._actions.toast(f"{title} is set up.")

    def remove(self, tool: str) -> None:
        """ "Remove": a confirm, then `unwire`; a file changed since setup is asked about."""
        spec = REGISTRY[tool]
        body = (
            f"hyprland.lua stops loading {spec.title}. Files changed when it was set up go "
            "back to the copies kept then, and files it added are deleted. "
            f"{spec.title} itself stays installed."
        )
        if isinstance(self._state.source, Wallpaper) and self._state.source.tool == tool:
            body += " Your own color settings apply again until you choose another source."
        self._confirm(
            ConsentDialog(
                heading=f"Remove {spec.title}?",
                body=body,
                verb=f"Remove {spec.title}",
                destructive=True,
                on_agree=lambda: self._unwire(tool, IfChanged.ASK),
            )
        )

    def _unwire(self, tool: str, choice: IfChanged) -> None:
        title = REGISTRY[tool].title
        done = unwire(
            tool,
            paths=self._session.paths,
            manifest=self._session.manifest(),
            unregister=self._session.remove_bridge,
            if_changed=choice,
        )
        match done:
            case NeedsChoice(changed=changed):
                self._ask_changed(tool, changed)
                return
            case NotDone(reason=reason):
                self._tell(f"{title} was not removed", reason)
            case Unwired(note=note) if note:
                self._actions.toast(note)
            case Unwired():
                self._actions.toast(f"{title} is removed.")
        self.refresh()

    def _ask_changed(self, tool: str, files: tuple[ChangedFile, ...]) -> None:
        """S3's question, showing both sides of each file: as it is now, and the copy that
        "Restore the copy" would put back. Nothing has changed yet; Cancel is the default."""
        title = REGISTRY[tool].title
        dialog = Adw.AlertDialog()
        dialog.set_heading("Files changed since setup")
        dialog.set_body(changed_since_setup(title, files))
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("leave", "Leave it as it is")
        dialog.add_response("restore", "Restore the copy")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "restore":
                self._unwire(tool, IfChanged.RESTORE)
            elif response == "leave":
                self._unwire(tool, IfChanged.LEAVE)

        dialog.connect("response", answered)
        self._confirm(dialog)

    def _regenerate_entrypoint(self) -> None:
        """The way out of a hand-edited `hyprland.lua` (finding 19 of the #153 review): a
        confirm that says what goes, then the Banner's Entrypoint Fix."""

        def agreed() -> None:
            if not self._session.regenerate_entrypoint():
                self._tell("hyprland.lua was not regenerated", NOT_UPDATED)
            self.refresh()

        self._confirm(
            ConsentDialog(
                heading="Regenerate hyprland.lua?",
                body=(
                    "This app writes hyprland.lua again from its own settings. The lines "
                    "added to it by hand are removed: put settings of your own in user.lua, "
                    "which this app never changes."
                ),
                verb="Regenerate",
                destructive=True,
                on_agree=agreed,
            )
        )

    def _resume(self, tool: str) -> None:
        if not self._session.set_color_source(Wallpaper(tool)):
            self._tell("Could not resume wallpaper colors", self._why())
        self.refresh()

    def _load_now(self) -> None:
        if not self._session.load_waiting_bridges() and self._state.blocked is not None:
            self._tell("Could not load the colors", self._state.blocked)
        self.refresh()

    def _copy_patch(self, tool: str) -> None:
        pack = REGISTRY[tool].template_pack
        if pack is not None:
            from gi.repository import Gdk

            self._page.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_value(pack.patch)
            )
            self._actions.toast("Patch copied.")

    def regenerate(self, tool: str) -> None:
        """Run `tool` on the wallpaper: the one way this page runs a program on its own.

        The image is the one the wallpaper daemon shows (asked off the main loop: that runs
        the daemon's client), else the one the user picks.
        """
        spec = REGISTRY[tool]
        binary = self._actions.find(spec.detection.binaries[0])
        if binary is None:
            self._tell(
                f"{spec.title} is not installed",
                f"{spec.title} was not found, so it cannot run. Install it, then try again.",
            )
            return
        self._running = tool
        self.refresh()
        current = self._actions.current_wallpaper

        def look() -> None:
            GLib.idle_add(self._have_image, tool, binary, current())

        threading.Thread(target=look, name=f"wallpaper-{tool}", daemon=True).start()

    def _have_image(self, tool: str, binary: Path, image: Path | None) -> bool:
        if image is not None:
            self._run(tool, binary, image)
            return False
        self._running = None
        self.refresh()

        def chosen(path: Path | None) -> None:
            if path is not None:
                self._running = tool
                self.refresh()
                self._run(tool, binary, path)

        self._actions.choose_image(self._page, chosen)
        return False

    def _run(self, tool: str, binary: Path, image: Path) -> None:
        self._start(tool, REGISTRY[tool].rerun_argv(binary, image, self._values[tool]))

    def _start(self, tool: str, argv: tuple[str, ...]) -> None:
        """Run `argv` off the main loop; `_ran` reports on it."""
        spec = REGISTRY[tool]
        run = self._actions.run

        def work() -> None:
            outcome: ToolRun | str
            try:
                outcome = run(argv, timeout=RUN_TIMEOUT)
            except ToolTimedOut:
                outcome = f"{spec.title} did not finish within {RUN_TIMEOUT:g} seconds."
            except (ToolRefused, OSError) as error:
                outcome = f"{spec.title} could not be started: {error}"
            GLib.idle_add(self._ran, tool, outcome)

        threading.Thread(target=work, name=f"regenerate-{tool}", daemon=True).start()

    def _ran(self, tool: str, outcome: ToolRun | str) -> bool:
        title = REGISTRY[tool].title
        self._running = None
        if isinstance(outcome, str):
            self._tell(f"{title} did not make new colors", outcome)
        elif outcome.returncode != 0:
            said = (outcome.stderr or outcome.stdout).strip().splitlines()
            self._tell(
                f"{title} did not make new colors",
                f"It stopped with code {outcome.returncode}."
                + (f" It said: {said[-1]}" if said else ""),
            )
        else:
            # A file a tool creates is not watched until it is required (S4).
            self._session.load_waiting_bridges()
            self._actions.toast(f"{title} made new colors.")
        self.refresh()
        return False

    # --- dialogs --------------------------------------------------------------------------

    def _confirm(self, dialog: Adw.AlertDialog) -> None:
        self.dialog = dialog
        dialog.present(self._page)

    def _tell(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.add_response("ok", "OK")
        self._confirm(dialog)

    def _why(self) -> str:
        return self._session.color_source_blocked or NOT_UPDATED


@dataclass(frozen=True, slots=True)
class _Read:
    """One refresh's reading of the disk; nothing in it outlives the next refresh."""

    source: ColorSource
    entries: tuple[BridgeEntry, ...]
    present: frozenset[str]
    detections: dict[str, ToolDetection]
    blocked: str | None


def _source_words(source: ColorSource) -> str:
    """The current source inside a sentence: "matugen", "the Preset you applied"."""
    match source:
        case Wallpaper(tool):
            return REGISTRY[tool].title if tool in REGISTRY else tool
        case PresetColors():
            return "the Preset you applied"
        case ManualColors():
            return "your own settings"
        case Several(tools):
            return " and ".join(REGISTRY[t].title if t in REGISTRY else t for t in tools)


__all__ = ["ThemingActions", "ThemingPage", "choose_image"]
