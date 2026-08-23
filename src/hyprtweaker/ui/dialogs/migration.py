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

from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ...engine.importer.loss import CLASS_ORDER, CLASS_TITLES, LossReport  # noqa: E402
from ...engine.migration.detect import ConfigKind  # noqa: E402
from ...engine.migration.flow import (  # noqa: E402
    ROLLBACK_SECONDS,
    Decision,
    MigrationFlow,
    SwitchResult,
)

Spawn = Callable[[Any], None]

DETECTED_TITLES = {
    ConfigKind.LEGACY_CONF: "You have a hyprland.conf",
    ConfigKind.FOREIGN_LUA: "You have a hyprland.lua this app did not write",
}

DETECTED_BODIES = {
    ConfigKind.LEGACY_CONF: (
        "It can be converted to the new Lua config. Your hyprland.conf is never changed, "
        "moved or deleted -- if anything goes wrong, deleting the generated hyprland.lua "
        "puts you back exactly where you are now."
    ),
    ConfigKind.FOREIGN_LUA: (
        "It can be imported as your current settings. The original is kept beside it as "
        "hyprland.lua.bak, so nothing you wrote is lost."
    ),
}


class MigrationDialog(Adw.Dialog):
    """Detect -> Preview -> Back up -> Switch & verify -> Keep or roll back."""

    def __init__(
        self,
        flow: MigrationFlow,
        *,
        spawn: Spawn,
        on_finished: Callable[[Decision | None], None] | None = None,
    ) -> None:
        super().__init__(title="Import configuration", content_width=680, content_height=560)
        self._flow = flow
        self._spawn = spawn
        self._on_finished = on_finished
        self._decision: Decision | None = None
        self._countdown_label: Gtk.Label | None = None

        self._view = Adw.NavigationView()
        self.set_child(self._view)
        self._view.push(self._detect_page())

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
        return page

    def _go_preview(self) -> None:
        try:
            self._flow.build_preview()
        except Exception as error:
            # Any importer failure is a page, not a crash: the wizard always has somewhere
            # to show the user, and the config is untouched at this point either way.
            self._view.push(self._failed_page("Could not read the configuration", str(error)))
            return
        self._flow.save_report()
        if self._flow.preview is not None and self._flow.preview.detection.streamlined:
            # Hyprland's own example config: "no loss report, straight to convert"
            # (ADR-0009). There is nothing of the user's to lose, so a report of what
            # converting it costs them is a page about somebody else's boilerplate.
            self._go_backup()
            return
        self._view.push(self._preview_page())

    # --- step 2: preview ------------------------------------------------------------------

    def _preview_page(self) -> Adw.NavigationPage:
        preview = self._flow.preview
        assert preview is not None
        page = _page("Preview")
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)

        summary = Adw.PreferencesGroup(
            title="What this changes",
            description=(
                "Nothing has been written yet. Reading this page costs you nothing -- you can "
                "still close the wizard and stay exactly as you are."
            ),
        )
        summary.add(_row("Settings imported", str(len(preview.model))))
        if self._flow.report_path is not None:
            summary.add(_row("Report saved to", str(self._flow.report_path)))
        column.append(summary)

        for group in _loss_groups(preview.loss):
            column.append(group)

        column.append(_rescue_group(self._flow.rescue_line))
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
        backup = self._flow.back_up()
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
        self._spawn(self._switch())

    async def _switch(self) -> None:
        result = await self._flow.switch()
        if not result.ok:
            await self._flow.roll_back_live()
            detail = "\n".join(check.detail for check in result.failures if check.detail)
            self._view.push(
                self._failed_page(
                    "The new configuration did not load, so it was rolled back",
                    "You are back on the configuration you started with.\n\n" + detail,
                )
            )
            return
        if not result.live:
            # Nothing was switched, so there is nothing to keep or roll back. Starting a
            # countdown here would offer to undo a change that never happened.
            self._view.push(self._done_page("Written", result.detail))
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
        group.add(_row("If you are locked out", self._flow.rescue_line))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        column.append(group)

        # What the switch could not check, and what it could check but did not like. Both
        # belong in front of the person deciding whether to keep it -- a soft check that
        # only ever reached a comment in the source is not a check the user got.
        caveats = [*result.notes, *(check.detail for check in result.warnings)]
        if caveats:
            unverified = Adw.PreferencesGroup(
                title="What this could not confirm",
                description="Not failures -- things the switch cannot see from here.",
            )
            for note in caveats:
                unverified.add(_row(note, ""))
            column.append(unverified)

        page.get_child().set_content(_scrolled(column))

        keep = _suggested("Keep")
        keep.connect("clicked", lambda _button: self._answer(Decision.KEPT))
        back = Gtk.Button(label="Roll back", css_classes=["destructive-action"])
        back.connect("clicked", lambda _button: self._answer(Decision.ROLLED_BACK))
        page.get_child().add_bottom_bar(_actions(keep, back))
        return page

    async def _countdown(self) -> None:
        decision = await self._flow.decide(on_tick=self._tick)
        self._decision = decision
        if decision is Decision.KEPT:
            self._finish(
                "Kept",
                "Your settings are now set up here. Your old configuration is backed up.",
            )
        else:
            self._finish(
                "Rolled back",
                "Nothing was kept. You are on the configuration you started with."
                if decision is Decision.ROLLED_BACK
                else "Nobody confirmed the switch, so it was rolled back automatically.",
            )

    def _tick(self, remaining: float) -> None:
        if self._countdown_label is not None:
            self._countdown_label.set_label(_countdown_text(remaining))

    def _answer(self, decision: Decision) -> None:
        self._flow.answer(decision)

    def _finish(self, title: str, body: str) -> None:
        self._view.push(self._done_page(title, body))
        if self._on_finished is not None:
            self._on_finished(self._decision)

    # --- shared pages ---------------------------------------------------------------------

    def _done_page(self, title: str, body: str) -> Adw.NavigationPage:
        page = _page(title)
        page.set_can_pop(False)
        group = Adw.PreferencesGroup(title=title, description=body)
        page.get_child().set_content(_column(group))
        page.get_child().add_bottom_bar(_actions(self._close_button("Close", suggested=True)))
        return page

    def _failed_page(self, title: str, body: str) -> Adw.NavigationPage:
        page = _page("Stopped")
        group = Adw.PreferencesGroup(title=title, description=body)
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
    row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_selectable=True)
    row.set_use_markup(False)
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


def _countdown_text(remaining: float) -> str:
    return f"{max(0, int(remaining + 0.5))}s"


def _loss_groups(report: LossReport) -> list[Adw.PreferencesGroup]:
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
        for item in items[:_MAX_ITEMS]:
            group.add(_row(item.message, item.origin or ""))
        if len(items) > _MAX_ITEMS:
            group.add(_row(f"and {len(items) - _MAX_ITEMS} more", "See the saved report."))
        groups.append(group)
    return groups


def _rescue_group(rescue_line: str) -> Adw.PreferencesGroup:
    group = Adw.PreferencesGroup(
        title="If you ever get locked out",
        description="From a TTY (Ctrl+Alt+F2), this puts you back:",
    )
    group.add(_row("Rescue", rescue_line))
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
) -> MigrationDialog:
    """Build, present and return the wizard.

    Returned rather than just presented, following `errors.py`: the dialog is the only
    handle a test has on what the wizard is showing.
    """
    dialog = MigrationDialog(flow, spawn=spawn, on_finished=on_finished)
    dialog.present(parent)
    return dialog


def _pick_file(
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
    return _pick_file(
        parent,
        Gtk.FileDialog(title="Export configuration", initial_name="hyprland.lua"),
        saving=True,
        on_chosen=on_chosen,
    )


def import_dialog(parent: Gtk.Widget, on_chosen: Callable[[Path], None]) -> Gtk.FileDialog:
    """Ask which `hyprland.lua` or `hyprland.conf` to import, then hand the path back."""
    return _pick_file(
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
