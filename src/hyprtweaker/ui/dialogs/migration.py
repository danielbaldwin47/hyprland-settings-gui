"""The Migration wizard: five `Adw.NavigationView` subpages over one `MigrationFlow`.

The dialog owns no migration logic. Every decision, every write and the rollback timer
live in `engine.migration.flow`, and this file is the rendering of them -- which is what
lets the whole flow, including its failure paths, be tested with no display, and what makes
a closed window unable to strand a switch that is still pending.

The one rule worth stating out loud: the buttons that advance the wizard are disabled while
the step behind them is running. Every step here writes files or talks to the compositor,
and a double-click on "Convert" would start a second migration over the first one's tree.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ...engine.bridge.wire import WireConsent  # noqa: E402
from ...engine.importer.loss import CLASS_ORDER, CLASS_TITLES, LossReport  # noqa: E402
from ...engine.importer.lua.sandbox import Cancelled, Consent  # noqa: E402
from ...engine.migration.backup import Backup  # noqa: E402
from ...engine.migration.bridge_setup import CannotSetUp, Offer, SetUp, ToolOffer  # noqa: E402
from ...engine.migration.detect import ConfigKind  # noqa: E402
from ...engine.migration.flow import (  # noqa: E402
    ROLLBACK_SECONDS,
    Decision,
    MigrationFlow,
    Offered,
    Preview,
    RollBackOutcome,
    SwitchResult,
    asks_consent,
)
from ...engine.migration.omarchy import is_omarchy_source  # noqa: E402
from .wire_consent import ConsentDialog  # noqa: E402

_log = logging.getLogger(__name__)

Spawn = Callable[[Any], None]

CONSENT_TEXT = (
    "Reading {name} means running it once: none of its commands run and no files change."
)
"""The consent page's promise (#190; wording decided in the #150 review, owner call 3).
`Consent(evaluate=True)` is `Policy.BLOCK`, which is what makes it true: the config's
commands and writes are faked, and the importer's own directory listing quotes the path it
is given (`runner.lua`), so a quote in a folder name cannot start one either."""

READING_TITLE = "Reading"
"""The progress page's title, while a read runs in a worker (#216)."""

READER_THREAD = "hyprtweaker: reading a config"
"""The read worker's thread name: what a test looks for to see that none is left."""

TOOLS_TITLE = "Theming tools"
"""The back-up step's Bridge setup page (#187)."""

TOOLS_TEXT = (
    "Setting one up changes its own config so its output keeps reaching Hyprland after the "
    "switch. Each one asks first, and nothing changes until you switch. You can also set "
    "them up later on the Theming page."
)

NOT_SET_UP = "Not set up"
WILL_SET_UP = "Set up when you switch"
SET_UP_LABEL = "Set up…"
UNDO_LABEL = "Don't set up"

LONG_READ_SECONDS = 5.0
"""When the progress page adds that a read can take up to a minute (the importer's
timeout, `sandbox.DEFAULT_TIMEOUT`). Ticket #216's open call 1."""


@dataclass(eq=False)
class _Read:
    """One read running in a worker. Compared by identity: a result is the dialog's only
    while its read is still `MigrationDialog._reading`."""

    cancel: threading.Event
    origin: Adw.NavigationPage
    """The page the read started from, insensitive until the read ends."""
    hint: int = 0
    """The pending "up to a minute" timer's source id; 0 once it ran or was removed."""


def _read_off_the_loop(
    read_preview: Callable[..., Preview],
    source: Path | None,
    consent: Consent,
    read: _Read,
    deliver: Callable[[_Read, Preview | Exception], bool],
) -> None:
    """The worker: read, and hand the outcome to the main loop. Touches no widget.

    A cancelled read hands back nothing: whoever cancelled it has already moved on.
    """
    try:
        outcome: Preview | Exception = read_preview(source, consent=consent, cancel=read.cancel)
    except Cancelled:
        return
    except Exception as error:
        outcome = error
    GLib.idle_add(deliver, read, outcome)


DETECTED_TITLES = {
    ConfigKind.LEGACY_CONF: "You have a hyprland.conf",
    ConfigKind.FOREIGN_LUA: "You have a hyprland.lua this app did not write",
}

DETECTED_BODIES = {
    ConfigKind.LEGACY_CONF: (
        "It can be converted to the new Lua config. Your hyprland.conf is never changed, "
        "moved or deleted: if anything goes wrong, deleting the generated hyprland.lua "
        "puts Hyprland back on it."
    ),
    ConfigKind.FOREIGN_LUA: (
        "It can be imported as your current settings. The original is kept beside it as "
        "hyprland.lua.bak, so nothing you wrote is lost."
    ),
}


OMARCHY_ENDS = (
    "Omarchy's theme menu and Omarchy updates will no longer change your Hyprland settings"
)
OMARCHY_ENDS_HELP = (
    "Change colors on the Theming page, or set up a color tool there. Restoring the backup "
    "this wizard makes puts you back."
)
"""What switching an Omarchy config costs, on the Preview page (#234)."""


class MigrationDialog(Adw.Dialog):
    """Detect -> Preview -> Back up -> Switch & verify -> Keep or roll back."""

    def __init__(
        self,
        flow: MigrationFlow,
        *,
        spawn: Spawn,
        on_finished: Callable[[Decision | None], None] | None = None,
        source: Path | None = None,
    ) -> None:
        super().__init__(title="Import configuration", content_width=680, content_height=560)
        self._flow = flow
        self._spawn = spawn
        self._on_finished = on_finished
        self._source = source
        """The file Import... chose, read instead of the detected one; `None` on first run."""
        self._decision: Decision | None = None
        self._answered: Decision | None = None
        self._countdown_label: Gtk.Label | None = None
        self._defaults: dict[Adw.NavigationPage, Gtk.Widget] = {}
        """Each page's safe button, made the dialog's default while that page shows."""
        self._reading: _Read | None = None
        """The read running in a worker, if one is (#216)."""
        self._confirm: ConsentDialog | None = None
        """The last Bridge setup confirm shown (#187): what a test or probe answers."""
        self._tool_rows: dict[str, tuple[Offer, Adw.ActionRow, Gtk.Button]] = {}
        """Each offered tool's row, so confirming one wallpaper color tool redraws the other."""
        self.connect("closed", lambda _dialog: self._stop_read())

        self._view = Adw.NavigationView()
        self._view.connect("notify::visible-page", self._on_visible_page)
        self.set_child(self._view)
        if source is not None and asks_consent(source):
            # Import... of a `.lua`: the user just chose the file, so the first question is
            # whether it may be run, not what was found in the config dir.
            self._view.push(self._consent_page(source))
        else:
            self._view.push(self._detect_page())
        # The first page is pushed before anything shows; its default is set here.
        self._on_visible_page(self._view, None)

    def _on_visible_page(self, view: Adw.NavigationView, _pspec: Any) -> None:
        # Cleared on every other page: a default left over from a page underneath would
        # make Enter on the Preview close the wizard.
        self.set_default_widget(self._defaults.get(view.get_visible_page()))

    # --- step 1: detect -------------------------------------------------------------------

    def _detect_page(self) -> Adw.NavigationPage:
        detection = self._flow.detection or self._flow.detect()
        kind = detection.kind

        page = _page("Detect")
        group = Adw.PreferencesGroup(
            title=DETECTED_TITLES.get(kind, "Nothing to import"),
            description=DETECTED_BODIES.get(kind, "There is no configuration file to read."),
        )
        if detection.source is not None:
            group.add(_row("Found", str(detection.source)))
        if detection.streamlined:
            group.add(
                _row(
                    "Hyprland's example config",
                    "This is the file Hyprland generates for a new user, so there is nothing "
                    "of yours to lose in converting it.",
                )
            )
        page.get_child().set_content(_column(group))

        convert = _suggested("Convert...")
        convert.connect("clicked", lambda _button: self._go_preview())
        page.get_child().add_bottom_bar(_actions(convert, self._close_button("Not now")))
        # Enter converts, wherever focus is on the page -- the selectable path included,
        # which took a Tab stop and did nothing on Enter (#148 hand-test 8).
        self._defaults[page] = convert
        return page

    def _go_preview(self) -> None:
        """Convert...: ask before running a `.lua`; a `hyprland.conf` is parsed, at once."""
        detected = self._flow.detection.source if self._flow.detection else None
        reading = self._source or detected
        if reading is not None and asks_consent(reading):
            self._view.push(self._consent_page(reading))
            return
        outcome: Preview | Exception
        try:
            outcome = self._flow.read_preview(self._source)
        except Exception as error:
            outcome = error
        self._land(outcome)

    def _land(self, outcome: Preview | Exception) -> None:
        """Show where a finished read leads: its failure, its commands, or its Preview."""
        if isinstance(outcome, Exception):
            # Any importer failure is a page, not a crash: the wizard always has somewhere
            # to show the user, and the config is untouched at this point either way.
            self._view.push(self._failed_page("Could not read the configuration", str(outcome)))
            return
        self._flow.hold(outcome)
        if outcome.offered:
            self._view.push(self._commands_page(outcome))
            return
        self._show_preview()

    # --- a read off the main loop (#216) ----------------------------------------------------

    def _read(self, consent: Consent) -> None:
        """Run the config with `consent` in a worker, behind a progress page.

        Running a config can take up to a minute, and the window has to keep drawing and
        answer Cancel and Close meanwhile. One read at a time: a press while one runs (a
        double click) starts nothing. The page it came from is insensitive underneath, and
        comes back as it was on Cancel.
        """
        if self._reading is not None:
            return
        origin = self._view.get_visible_page()
        assert origin is not None
        read = _Read(cancel=threading.Event(), origin=origin)
        self._reading = read
        origin.set_sensitive(False)
        self._view.push(self._progress_page(read))
        worker = threading.Thread(
            target=_read_off_the_loop,
            args=(self._flow.read_preview, self._source, consent, read, self._read_done),
            name=READER_THREAD,
            daemon=True,
        )
        worker.start()

    def _read_done(self, read: _Read, outcome: Preview | Exception) -> bool:
        """The worker's result, on the main loop. Dropped if the read was cancelled.

        Touches nothing until it knows the read is still the dialog's: after a Close the
        dialog may already be released, and its widgets with it.
        """
        if read is not self._reading:
            return GLib.SOURCE_REMOVE
        self._forget_read()
        self._return_to(read.origin)
        self._land(outcome)
        return GLib.SOURCE_REMOVE

    def _forget_read(self) -> _Read | None:
        """The running read, now no longer the dialog's, with its timer gone."""
        read = self._reading
        if read is None:
            return None
        self._reading = None
        if read.hint:
            GLib.source_remove(read.hint)
            read.hint = 0
        return read

    def _stop_read(self) -> _Read | None:
        """Stop the running read, if any: Cancel's path, and Close's.

        Close touches no widget past this point: the dialog is on its way to release.
        """
        read = self._forget_read()
        if read is not None:
            read.cancel.set()
        return read

    def _cancel_read(self) -> None:
        """Cancel: stop the read and return to the page it came from, as it was."""
        read = self._stop_read()
        if read is not None:
            self._return_to(read.origin)

    def _return_to(self, origin: Adw.NavigationPage) -> None:
        origin.set_sensitive(True)
        self._view.pop_to_page(origin)

    def _progress_page(self, read: _Read) -> Adw.NavigationPage:
        page = _page(READING_TITLE)
        page.set_can_pop(False)  # Cancel is the way back: it stops the read too
        status = Adw.StatusPage(title="Reading your config…", vexpand=True)
        status.set_paintable(Adw.SpinnerPaintable(widget=status))
        page.get_child().set_content(status)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self._cancel_read())
        page.get_child().add_bottom_bar(_actions(cancel))

        def say_it_is_long() -> bool:
            read.hint = 0
            if read is self._reading:
                status.set_description("This can take up to a minute.")
            return GLib.SOURCE_REMOVE

        read.hint = GLib.timeout_add(int(LONG_READ_SECONDS * 1000), say_it_is_long)
        return page

    def _show_preview(self) -> None:
        self._flow.save_report()
        if self._flow.preview is not None and self._flow.preview.detection.streamlined:
            # Hyprland's own example config: "no loss report, straight to convert"
            # (ADR-0009). There is nothing of the user's to lose, so a report of what
            # converting it costs them is a page about somebody else's boilerplate.
            self._go_backup()
            return
        self._view.push(self._preview_page())

    # --- before step 2: consent to run a .lua (#190) ---------------------------------------

    def _consent_page(self, source: Path) -> Adw.NavigationPage:
        """Ask before the Lua importer runs the file. Asked every time, never remembered.

        "Not now" is the default: the user agreed to look at their config, not yet to run
        it. There is no "Copy the Lua instead" here -- that exports the *converted* config,
        which needs the very read the user has not agreed to.
        """
        page = _page("Read your config")
        # A file name is the user's own text and may hold `&` or `<`: escaped, not markup.
        title = GLib.markup_escape_text(f"Read {source.name}?")
        # Named, as the title is: it may be any .lua chosen to import (N1 of the #148 review).
        description = GLib.markup_escape_text(CONSENT_TEXT.format(name=source.name))
        group = Adw.PreferencesGroup(title=title, description=description)
        group.add(_row("File", str(source)))
        page.get_child().set_content(_column(group))

        read = Gtk.Button(label="Read it")
        read.connect("clicked", lambda _button: self._read(Consent(evaluate=True)))
        not_now = self._close_button("Not now")
        page.get_child().add_bottom_bar(_actions(read, not_now))
        self._defaults[page] = not_now
        return page

    def _commands_page(self, preview: Preview) -> Adw.NavigationPage:
        """The second offer: run the config's own commands for real (#190).

        Run is never the default and never suggested: these are the user's commands running
        with the user's rights, and the only page in the app that runs anything it did not
        write. When the read without them already got settings, the page says how many and
        offers to continue with those -- the safe way forward, so it is the default
        (#150 review, owner call 2).
        """
        page = _page("Commands")
        group = Adw.PreferencesGroup(
            title="This config runs commands to build itself",
            description=_commands_description(preview.imported),
        )
        for offered in preview.offered:
            row = _row(offered.text, _offered_subtitle(offered))
            row.set_title_selectable(True)
            row.add_css_class("monospace")
            group.add(row)
        page.get_child().set_content(_scrolled(_column(group)))

        run = Gtk.Button(label="Run them and read", css_classes=["destructive-action"])
        run.connect(
            "clicked", lambda _button: self._read(Consent(evaluate=True, passthrough=True))
        )
        not_now = self._close_button("Not now")
        buttons = [run]
        safe: Gtk.Button = not_now
        if preview.imported:
            safe = _suggested("Continue without running them")
            safe.connect("clicked", lambda _button: self._show_preview())
            buttons.append(safe)
        page.get_child().add_bottom_bar(_actions(*buttons, not_now))
        self._defaults[page] = safe
        return page

    # --- step 2: preview ------------------------------------------------------------------

    def _preview_page(self) -> Adw.NavigationPage:
        preview = self._flow.preview
        assert preview is not None
        page = _page("Preview")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        summary = Adw.PreferencesGroup(
            title="What this changes",
            description=(
                "Nothing has been written yet. Reading this page costs you nothing — you can "
                "still close the wizard and stay exactly as you are."
            ),
        )
        summary.add(_row("Settings imported", str(len(preview.model))))
        if self._flow.report_path is not None:
            summary.add(_row("Report saved to", str(self._flow.report_path)))
        if is_omarchy_source(preview.detection.source):
            summary.add(_row(OMARCHY_ENDS, OMARCHY_ENDS_HELP))
        if (note := self._flow.app_data_note) is not None:
            summary.add(_row("Your presets and display profiles", note))
        column.append(summary)

        for group in _loss_groups(preview.loss, _MAX_ITEMS):
            column.append(group)

        column.append(_rescue_group(self._flow.rescue_command))
        page.get_child().set_content(_scrolled(column))

        proceed = _suggested("Back up and convert")
        proceed.connect("clicked", lambda _button: self._go_backup())
        copy = Gtk.Button(label="Copy the Lua instead")
        copy.connect("clicked", lambda _button: self._copy_lua())
        page.get_child().add_bottom_bar(_actions(proceed, copy, self._close_button("Cancel")))
        return page

    def _copy_lua(self) -> None:
        """The DIY exit: take the converted Lua, switch nothing (ADR-0009).

        Interop, not lock-in.
        """
        if self._flow.preview is None:
            return
        self.get_clipboard().set(self._flow.export_text())
        self._view.push(
            self._done_page(
                "Copied",
                "The converted configuration is on your clipboard. Nothing on this machine "
                "was changed.",
            )
        )

    # --- step 3: back up ------------------------------------------------------------------

    def _go_backup(self) -> None:
        """Back up, then offer Bridge setup if a tool can be set up, then the static gate.

        The offer sits between the two (ADR-0009 §Back up, bridge, static gate) so the gate
        judges the tree with the chosen tools' lines in it. No page when nothing can be set
        up: a user who only wants to migrate meets no extra step.
        """
        backup = self._flow.back_up()
        offers = self._flow.bridge_offers()
        if any(isinstance(offer, Offer) for offer in offers):
            self._view.push(self._tools_page(backup, offers))
            return
        self._gate(backup)

    def _tools_page(self, backup: Backup, offers: tuple[ToolOffer, ...]) -> Adw.NavigationPage:
        """One row per tool, each set up only through its own confirm (settled S3).

        "Continue" is the default, and it continues with whatever was confirmed: skipping
        every tool is the safe answer, since a tool left alone keeps working as it does now.
        """
        page = _page(TOOLS_TITLE)
        group = Adw.PreferencesGroup(
            title="Theming tools on this computer", description=TOOLS_TEXT
        )
        self._tool_rows.clear()
        for offer in offers:
            group.add(self._tool_row(offer))
        page.get_child().set_content(_scrolled(_column(group)))

        go_on = _suggested("Continue")
        go_on.connect("clicked", lambda _button: self._gate(backup))
        page.get_child().add_bottom_bar(_actions(go_on, self._close_button("Cancel")))
        self._defaults[page] = go_on
        return page

    def _tool_row(self, offer: ToolOffer) -> Adw.ActionRow:
        match offer:
            case SetUp(title=title):
                return _row(title, "Already set up. It keeps working after the switch.")
            case CannotSetUp(title=title, reason=reason):
                return _row(title, reason)
            case Offer():
                button = Gtk.Button(valign=Gtk.Align.CENTER)
                row = _row(offer.title, "", suffix=button)
                button.connect("clicked", lambda _button: self._toggle_tool(offer, row, button))
                self._tool_rows[offer.tool] = (offer, row, button)
                self._show_tool(offer, row, button)
                return row

    def _consented(self, tool: str) -> bool:
        return any(consent.plan.tool == tool for consent in self._flow.consents)

    def _show_tool(self, offer: Offer, row: Adw.ActionRow, button: Gtk.Button) -> None:
        chosen = self._consented(offer.tool)
        row.set_subtitle(WILL_SET_UP if chosen else NOT_SET_UP)
        button.set_label(UNDO_LABEL if chosen else SET_UP_LABEL)

    def _toggle_tool(self, offer: Offer, row: Adw.ActionRow, button: Gtk.Button) -> None:
        """Set up asks first; taking a choice back before the switch needs no question."""
        if self._consented(offer.tool):
            self._flow.withdraw(offer.tool)
            self._show_tool(offer, row, button)
            return

        def agree() -> None:
            # Confirming one wallpaper color tool withdraws the other: every row says so.
            self._flow.consent(WireConsent(offer.plan))
            for shown in self._tool_rows.values():
                self._show_tool(*shown)

        title = offer.title
        self._confirm = ConsentDialog(
            heading=f"Set up {title}?",
            body=(
                f"When you switch, these files change so that {title}'s output loads in "
                "Hyprland. Nothing changes before then."
                if offer.plan.files
                else f"Nothing of {title}'s changes. When you switch, Hyprland loads what "
                f"{title} writes."
            ),
            verb=f"Set up {title}",
            on_agree=agree,
            plan=offer.plan,
        )
        self._confirm.present(self)

    def _gate(self, backup: Backup) -> None:
        gate = self._flow.stage_and_gate()

        if gate.blocks:
            self._view.push(
                self._failed_page(
                    "Hyprland rejected the converted configuration",
                    "Nothing was switched, and your current configuration is untouched.\n\n"
                    + gate.output,
                )
            )
            return

        page = _page("Back up")
        group = Adw.PreferencesGroup(
            title="Backed up",
            description="A full copy of your config directory, before anything was changed.",
        )
        group.add(_row("Backup", str(backup.path)))
        group.add(_row("Files copied", str(backup.count())))
        chosen = [consent.plan.title for consent in self._flow.consents]
        if chosen:
            group.add(_row(WILL_SET_UP, ", ".join(chosen)))
        group.add(
            _row(
                "Checked",
                "Hyprland accepted the converted configuration"
                if gate.ran
                else "Hyprland is not installed here, so the configuration could not be "
                "checked before switching",
            )
        )
        page.get_child().set_content(_column(group))

        switch = _suggested("Switch and verify")
        switch.connect("clicked", lambda button: self._go_switch(button))
        page.get_child().add_bottom_bar(_actions(switch, self._close_button("Cancel")))
        self._view.push(page)

    # --- step 4: switch & verify ----------------------------------------------------------

    def _go_switch(self, button: Gtk.Button) -> None:
        button.set_sensitive(False)
        # Not closable until an ending shows: Esc on "Keep or roll back" let the clock run
        # unseen and roll back with no word (F22 of the #148 review).
        self.set_can_close(False)
        self._spawn(self._switch())

    async def _switch(self) -> None:
        try:
            await self._switch_and_say()
        except BaseException:
            self.set_can_close(True)
            raise

    async def _switch_and_say(self) -> None:
        result = await self._flow.switch()
        if not result.ok:
            outcome = await self._flow.roll_back_live()
            failures = "\n".join(check.detail for check in result.failures if check.detail)
            if outcome.complete:
                title = "The new configuration did not load, so it was rolled back"
                said = "You are back on the configuration you started with."
            else:
                title = "The new configuration did not load, and it could not be rolled back"
                said = outcome.rescue
            self._view.push(
                self._failed_page(
                    title,
                    "\n\n".join(part for part in (said, failures, *outcome.notes) if part),
                )
            )
            self.set_can_close(True)
            return
        if not result.live:
            # Nothing was switched, so there is nothing to keep or roll back. Starting a
            # countdown here would offer to undo a change that never happened.
            self._view.push(self._done_page("Written", result.detail))
            self.set_can_close(True)
            # An ending too: the config is written, and the window has to know (hand-test 21).
            if self._on_finished is not None:
                self._on_finished(None)
            return
        self._view.push(self._decide_page(result))
        self._spawn(self._countdown())

    # --- step 5: keep or roll back --------------------------------------------------------

    def _decide_page(self, result: SwitchResult) -> Adw.NavigationPage:
        page = _page("Keep or roll back")
        page.set_can_pop(False)

        group = Adw.PreferencesGroup(
            title="Is everything still working?",
            description=(
                result.detail
                or "Your new configuration is live. Try your keybinds. If you do nothing, "
                "it rolls back on its own."
            ),
        )
        self._countdown_label = Gtk.Label(
            label=_countdown_text(ROLLBACK_SECONDS), css_classes=["title-1"]
        )
        group.add(_row("Rolling back in", "", suffix=self._countdown_label))
        group.add(_row("If you are locked out", self._flow.rescue_command))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        column.append(group)

        # What the switch could not check, and what it could check but did not like. Both
        # belong in front of the person deciding whether to keep it -- a soft check that
        # only ever reached a comment in the source is not a check the user got.
        caveats = [*result.notes, *(check.detail for check in result.warnings)]
        if caveats:
            unverified = Adw.PreferencesGroup(
                title="What this could not confirm",
                description="Not failures — things the switch cannot see from here.",
            )
            for note in caveats:
                unverified.add(_row(note, ""))
            column.append(unverified)

        if result.bridges:
            tools = Adw.PreferencesGroup(title=TOOLS_TITLE)
            for note in result.bridges:
                tools.add(_row(note, ""))
            column.append(tools)

        page.get_child().set_content(_scrolled(column))

        keep = _suggested("Keep")
        keep.connect("clicked", lambda _button: self._answer(Decision.KEPT))
        back = Gtk.Button(label="Roll back", css_classes=["destructive-action"])
        back.connect("clicked", lambda _button: self._answer(Decision.ROLLED_BACK))
        page.get_child().add_bottom_bar(_actions(keep, back))
        return page

    async def _countdown(self) -> None:
        try:
            decision = await self._flow.decide(on_tick=self._tick)
        except Exception as error:  # any failure must reach a page that can close
            _log.warning("the switch's ending failed", exc_info=True)
            self._ending_failed(error)
            return
        self._decision = decision
        if decision is Decision.KEPT:
            kept = "Your settings are now set up here. Your old configuration is backed up."
            self._finish("Kept", " ".join(filter(None, (kept, self._flow.moved_aside))))
            return
        outcome = self._flow.rollback
        if outcome is not None and not outcome.complete:
            self._finish("Not rolled back", _incomplete_text(outcome))
            return
        said = (
            "Nothing was kept. You are on the configuration you started with."
            if decision is Decision.ROLLED_BACK
            else "Nobody confirmed the switch, so it was rolled back automatically."
        )
        notes = outcome.notes if outcome is not None else ()
        self._finish("Rolled back", "\n\n".join([said, *notes]))

    def _ending_failed(self, error: Exception) -> None:
        """Keep or Roll back raised: a page that can close, saying what is left (#268 AC4)."""
        which = "Keep" if self._answered is Decision.KEPT else "Roll back"
        unfinished = (
            "The switch is still recorded as unfinished, so the app offers to roll it back "
            "the next time it starts."
            if self._flow.pending_switch() is not None
            else ""
        )
        body = "\n\n".join(
            part
            for part in (
                f"{which} did not finish: {error}",
                unfinished,
                f"If you are locked out, run this from a TTY:\n{self._flow.rescue_command}",
            )
            if part
        )
        self._view.push(self._failed_page("The switch could not be finished", body))
        self.set_can_close(True)
        if self._on_finished is not None:
            self._on_finished(None)

    def _tick(self, remaining: float) -> None:
        if self._countdown_label is not None:
            self._countdown_label.set_label(_countdown_text(remaining))

    def _answer(self, decision: Decision) -> None:
        self._answered = decision
        self._flow.answer(decision)

    def _finish(self, title: str, body: str) -> None:
        self._view.push(self._done_page(title, body))
        self.set_can_close(True)
        if self._on_finished is not None:
            self._on_finished(self._decision)

    # --- shared pages ---------------------------------------------------------------------

    def _done_page(self, title: str, body: str) -> Adw.NavigationPage:
        page = _page(title)
        page.set_can_pop(False)
        group = Adw.PreferencesGroup(title=title, description=GLib.markup_escape_text(body))
        page.get_child().set_content(_column(group))
        page.get_child().add_bottom_bar(_actions(self._close_button("Close", suggested=True)))
        return page

    def _failed_page(self, title: str, body: str) -> Adw.NavigationPage:
        page = _page("Stopped")
        # `body` carries error text and paths, which may hold `&`: escaped, not markup.
        group = Adw.PreferencesGroup(title=title, description=GLib.markup_escape_text(body))
        page.get_child().set_content(_scrolled(_column(group)))
        page.get_child().add_bottom_bar(_actions(self._close_button("Close", suggested=True)))
        return page

    def _close_button(self, label: str, *, suggested: bool = False) -> Gtk.Button:
        button = Gtk.Button(label=label)
        if suggested:
            button.add_css_class("suggested-action")
        button.connect("clicked", lambda _button: self.close())
        return button


# --- construction helpers ------------------------------------------------------------------


def _incomplete_text(outcome: RollBackOutcome) -> str:
    """A Roll back that could not finish, said as what is still in place (#268 AC1)."""
    return "\n\n".join([outcome.rescue, *outcome.notes])


def _page(title: str) -> Adw.NavigationPage:
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    return Adw.NavigationPage(title=title, child=toolbar)


def _column(*children: Gtk.Widget) -> Gtk.Widget:
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_top=18,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
    )
    for child in children:
        box.append(child)
    return box


def _scrolled(child: Gtk.Widget) -> Gtk.Widget:
    if not isinstance(child, Gtk.Box):
        child = _column(child)
    else:
        for margin in ("margin_top", "margin_bottom", "margin_start", "margin_end"):
            child.set_property(margin, 18)
    return Gtk.ScrolledWindow(child=child, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)


def _row(title: str, subtitle: str, *, suffix: Gtk.Widget | None = None) -> Adw.ActionRow:
    row = Adw.ActionRow(use_markup=False, subtitle_selectable=True)
    row.set_title(title)
    row.set_subtitle(subtitle)
    if suffix is not None:
        row.add_suffix(suffix)
    return row


def _actions(*buttons: Gtk.Button) -> Gtk.Widget:
    box = Gtk.Box(
        orientation=Gtk.Orientation.HORIZONTAL,
        spacing=12,
        halign=Gtk.Align.END,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
    )
    for button in buttons:
        box.append(button)
    return box


def _suggested(label: str) -> Gtk.Button:
    return Gtk.Button(label=label, css_classes=["suggested-action"])


def _commands_description(imported: int) -> str:
    run = (
        "Reading it fully means running these for real, and any others the config goes on "
        "to run:"
    )
    if not imported:
        return run
    settings = "1 setting" if imported == 1 else f"{imported} settings"
    return (
        f"Without running them, the app read {settings} from this file. Anything these "
        f"commands build is missing.\n\n{run}"
    )


def _offered_subtitle(offered: Offered) -> str:
    """What a row on the Commands page does, in words, and how often it runs."""
    does = {
        "command": "",
        "delete": "Deletes this file",
        "move": "Moves or renames this file",
    }[offered.kind]
    if offered.times == 1:
        return does
    if not does:
        return f"Runs {offered.times} times"
    return f"{does}, {offered.times} times"


def _countdown_text(remaining: float) -> str:
    return f"{max(0, int(remaining + 0.5))}s"


def _loss_groups(report: LossReport, limit: int | None = None) -> list[Adw.PreferencesGroup]:
    """The Loss report as three groups, worst first (ADR-0009).

    Breakage is shown, never used to refuse: a config with a `hyprctl dispatch` in an exec
    string is still worth converting, and only the user can decide whether to fix the script
    or stay put.
    """
    groups = []
    for loss_class in CLASS_ORDER:
        items = report.of_class(loss_class)
        if not items:
            continue
        group = Adw.PreferencesGroup(
            title=f"{CLASS_TITLES[loss_class]} ({len(items)})",
            description=_CLASS_HELP.get(loss_class.value, ""),
        )
        shown = items if limit is None else items[:limit]
        for item in shown:
            group.add(_row(item.message, item.origin or ""))
        if len(items) > len(shown):
            group.add(
                _row(f"and {len(items) - len(shown)} more", "Main menu → Last import report.")
            )
        groups.append(group)
    return groups


def import_report_dialog(parent: Gtk.Widget, report: LossReport, *, kept: bool) -> Adw.Dialog:
    """The last import's Loss report as the wizard shows it, every finding, no Markdown.

    F25 of the #148 review: it was `report.render()` in an alert, so the user read headings,
    asterisks and loss codes. Says first whether that import is the config in use, since the
    report is saved at Preview and a backed-out import still shows here. Returned for tests.
    """
    page = Adw.PreferencesPage()
    summary = Adw.PreferencesGroup(
        title=f"Imported from {report.source}" if report.source else "Imported",
        description=(
            "It is the configuration this app works with now."
            if kept
            else "It was not kept: it was rolled back, or the switch never finished."
        ),
    )
    page.add(summary)
    groups = _loss_groups(report)
    for group in groups:
        page.add(group)
    if not groups:
        clean = Adw.PreferencesGroup()
        clean.add(_row("Nothing to report", "Everything converted as it was written."))
        page.add(clean)
    toolbar = Adw.ToolbarView(content=page)
    toolbar.add_top_bar(Adw.HeaderBar())
    dialog = Adw.Dialog(title="Last import", content_width=600, content_height=640)
    dialog.set_child(toolbar)
    dialog.present(parent)
    return dialog


def _rescue_group(rescue_command: str) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="If you ever get locked out",
        description="From a TTY (Ctrl+Alt+F2), this puts you back:",
    )
    group.add(_row("Rescue", rescue_command))
    return group


_MAX_ITEMS = 12

_CLASS_HELP = {
    "breakage": "Converting cannot fix these. They will need a change you make yourself.",
    "needs-review": "Converted, but a decision was made for you. Worth a look.",
    "info": "Converted with a change in how it is written, not in what it does.",
}


def migration_dialog(
    parent: Gtk.Widget,
    flow: MigrationFlow,
    *,
    spawn: Spawn,
    on_finished: Callable[[Decision | None], None] | None = None,
    source: Path | None = None,
) -> MigrationDialog:
    """Build, present and return the wizard.

    Returned rather than just presented, following `errors.py`: the dialog is the only
    handle a test has on what the wizard is showing.
    """
    dialog = MigrationDialog(flow, spawn=spawn, on_finished=on_finished, source=source)
    dialog.present(parent)
    return dialog


def pick_file(
    parent: Gtk.Widget,
    dialog: Gtk.FileDialog,
    *,
    saving: bool,
    on_chosen: Callable[[Path], None],
) -> Gtk.FileDialog:
    """Run a `Gtk.FileDialog` and hand back the chosen path, or nothing if cancelled.

    One helper for both directions: save and open differ only in which pair of methods
    they call, and a cancelled dialog is not a failure in either.
    """

    def finished(source: Gtk.FileDialog, result: Any) -> None:
        try:
            chosen = source.save_finish(result) if saving else source.open_finish(result)
        except GLib.Error:
            return  # Cancelled, or the portal declined. Neither is worth reporting.
        if chosen is not None and chosen.get_path():
            on_chosen(Path(chosen.get_path()))

    root = parent.get_root()
    if saving:
        dialog.save(root, None, finished)
    else:
        dialog.open(root, None, finished)
    return dialog


def export_dialog(parent: Gtk.Widget, on_chosen: Callable[[Path], None]) -> Gtk.FileDialog:
    """Ask where to write a flattened export, then hand the path back."""
    return pick_file(
        parent,
        Gtk.FileDialog(title="Export configuration", initial_name="hyprland.lua"),
        saving=True,
        on_chosen=on_chosen,
    )


def import_dialog(parent: Gtk.Widget, on_chosen: Callable[[Path], None]) -> Gtk.FileDialog:
    """Ask which `hyprland.lua` or `hyprland.conf` to import, then hand the path back."""
    return pick_file(
        parent,
        Gtk.FileDialog(title="Import configuration", filters=_config_filters()),
        saving=False,
        on_chosen=on_chosen,
    )


def _config_filters() -> Gio.ListStore:
    """The one file type Import accepts: a Lua or hyprlang Hyprland config."""
    filters = Gio.ListStore.new(Gtk.FileFilter)
    config = Gtk.FileFilter(name="Hyprland configuration")
    config.add_pattern("*.lua")
    config.add_pattern("*.conf")
    filters.append(config)
    return filters
