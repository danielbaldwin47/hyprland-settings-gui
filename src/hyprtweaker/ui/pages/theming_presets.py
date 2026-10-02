"""The Presets group of the Theming page: where a look is saved and brought back (#171).

One group, five verbs. **Save** keeps the current look by Capture scope (`SavePresetDialog`);
a name that is taken asks "Replace <name>?" and Cancel returns to the dialog. **Apply** opens
one confirm that says what changes, whose colours win while a wallpaper tool sets them, and
whether the wallpaper changes too, then hands the answers to the window's `apply_preset`
(one transaction, one Ctrl+Z, the undo toast). **Export** writes a Theme archive; **Import**
previews one before anything is added (`ThemeImportDialog`); **Delete** asks first, since a
file cannot be undone.

State is on screen, not guessed: an empty list says what a Preset is and how to save the
first; Apply is insensitive with the reason on a read-only session while Save, Export, Import
and Delete stay live (they touch files, not the config); a Preset with a wallpaper says so,
or says why the wallpaper will not change (no daemon, hyprpaper); and a remembered colour
answer is a row of its own with "Forget".

**Freshness.** Presets are files, so nothing announces a change. The group rebuilds after its
own save, delete and import, and in `refresh()` when what it shows has moved: the Session's
`presets_revision`, whether the session is live, the wallpaper daemon, the remembered answer.
Removed rows are released (#219). `reveal_preset(slug)` returns the row for a search hit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from hyprtweaker.engine.bridge import Several, Wallpaper  # noqa: E402
from hyprtweaker.engine.bridge.wire import shown as tilde_path  # noqa: E402
from hyprtweaker.engine.presets import (  # noqa: E402
    CaptureScope,
    ColorChoice,
    Preset,
    PresetApplied,
    PresetApplyResult,
    PresetNameTaken,
    PresetNotSaved,
    PresetSaved,
    PresetSaveResult,
)
from hyprtweaker.engine.presets_archive import (  # noqa: E402
    ARCHIVE_SUFFIX,
    ArchiveNotWritten,
    ArchiveWritten,
    archive_name,
    export_archive,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.dialogs.colour_conflict import (  # noqa: E402
    ColourConflictChoice,
    remembered_sentence,
)
from hyprtweaker.ui.dialogs.migration import _pick_file  # noqa: E402
from hyprtweaker.ui.dialogs.preset_apply import (  # noqa: E402
    ApplyPresetDialog,
    changes_text,
)
from hyprtweaker.ui.dialogs.preset_save import (  # noqa: E402
    WALLPAPER_SUBTITLE,
    SavePresetDialog,
    ScopeChoice,
    size_text,
)
from hyprtweaker.ui.dialogs.theme_import import ThemeImportDialog  # noqa: E402
from hyprtweaker.ui.flash import flash  # noqa: E402
from hyprtweaker.ui.pages.entity_text import preset_summary  # noqa: E402
from hyprtweaker.ui.release import release  # noqa: E402

TITLE = "Presets"
DESCRIPTION = "Save your current look, switch back to it later, or share it as a theme file."
READ_ONLY_DESCRIPTION = "Applying is off. {reason} You can still save, export and import."
"""Filled with `Session.offline_sentence`, which is true for an old Hyprland as well as for
one this app cannot reach."""
SAVE_LABEL = "Save current as preset…"
IMPORT_LABEL = "Import…"
EMPTY_TITLE = "No presets yet"
EMPTY_SUBTITLE = (
    "A preset keeps a look (colors, gaps, animation switches, fonts, wallpaper) so you can "
    "switch back to it later. Press “Save current as preset” to keep this one."
)
COLORS_NEED_HYPRLAND = "Wallpaper colors can only be captured while applying is on"
WALLPAPER_NEEDS_HYPRLAND = "The wallpaper can only be saved while applying is on"
FORGET = "Forget"


@dataclass(frozen=True, slots=True)
class PresetActions:
    """What the window lends the group. Defaults stand on the Session alone, so a page built
    without a window (a probe, a test) still applies; the window passes its own."""

    apply: Callable[..., PresetApplyResult] | None = None
    """`MainWindow.apply_preset(slug, *, wallpaper, colors, remember)`: asks, applies, and
    keeps the undo toast. `None` applies through the Session with no toast."""
    remembered: Callable[[], ColorChoice | None] = field(default=lambda: None)
    """The remembered colour answer, read from the window's prefs each time."""
    forget: Callable[[], None] = field(default=lambda: None)
    remember: Callable[[ColorChoice], None] = field(default=lambda _choice: None)
    toast: Callable[[str], None] = field(default=lambda _text: None)
    choose_export: Callable[[Gtk.Widget, str, Callable[[Path], None]], None] = field(
        default=lambda parent, name, done: choose_export_file(parent, name, done)
    )
    choose_import: Callable[[Gtk.Widget, Callable[[Path], None]], None] = field(
        default=lambda parent, done: choose_theme_file(parent, done)
    )


def choose_export_file(parent: Gtk.Widget, name: str, done: Callable[[Path], None]) -> None:
    """Ask where to write a Theme archive."""
    _pick_file(
        parent,
        Gtk.FileDialog(title="Export preset", initial_name=name),
        saving=True,
        on_chosen=done,
    )


def choose_theme_file(parent: Gtk.Widget, done: Callable[[Path], None]) -> None:
    """Ask which Theme archive to import."""
    themes = Gtk.FileFilter(name="Theme archive")
    themes.add_pattern(f"*{ARCHIVE_SUFFIX}")
    everything = Gtk.FileFilter(name="Any file")
    everything.add_pattern("*")
    filters = Gio.ListStore.new(Gtk.FileFilter)
    filters.append(themes)
    filters.append(everything)
    dialog = Gtk.FileDialog(title="Import theme", filters=filters, default_filter=themes)
    _pick_file(parent, dialog, saving=False, on_chosen=done)


def _icon_button(icon: str, label: str, on_click: Callable[[], None]) -> Gtk.Button:
    """A flat icon button that still has a name for a screen reader and a tooltip."""
    button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, css_classes=["flat"])
    button.set_tooltip_text(label)
    button.update_property([Gtk.AccessibleProperty.LABEL], [label])
    button.connect("clicked", lambda _button: on_click())
    return button


def _row(title: str, subtitle: str) -> Adw.ActionRow:
    """A row of plain text: a Preset's name is the user's and may hold `&`."""
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    return row


def subtitle_of(preset: Preset, wallpaper_note: str | None) -> str:
    """What a row says about a Preset: what it keeps, when, and what its wallpaper will do."""
    lines = [preset_summary(preset)]
    if wallpaper_note is not None:
        lines.append(wallpaper_note)
    return "\n".join(lines)


def remembered_row(choice: ColorChoice) -> tuple[str, str]:
    """The title and subtitle of the remembered-answer row: what the answer does, by name."""
    if choice is ColorChoice.USE_PRESET:
        return (
            "Remembered: a preset's colors win over your wallpaper's",
            "Applying a preset with colors uses them and pauses the tool that makes yours "
            "from your wallpaper. Resume it under Colors above. Forget this to be asked again.",
        )
    return (
        "Remembered: your wallpaper's colors win",
        "Applying a preset with colors keeps the ones your wallpaper makes and leaves the "
        "preset's out. Forget this to be asked again.",
    )


class PresetsGroup:
    """The Presets group. `group` is what the Theming page adds."""

    def __init__(self, session: Session, actions: PresetActions) -> None:
        self._session = session
        self._actions = actions
        self.group = Adw.PreferencesGroup(title=TITLE, description=DESCRIPTION)
        self.save_button = Gtk.Button(label=SAVE_LABEL, valign=Gtk.Align.CENTER)
        self.save_button.set_tooltip_text("Keep the current look as a preset")
        self.save_button.connect("clicked", lambda _button: self.save())
        self.import_button = Gtk.Button(label=IMPORT_LABEL, valign=Gtk.Align.CENTER)
        self.import_button.set_tooltip_text("Add a preset from a theme file")
        self.import_button.connect("clicked", lambda _button: self.import_theme())
        header = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        header.append(self.import_button)
        header.append(self.save_button)
        self.group.set_header_suffix(header)
        self._rows: dict[str, Adw.ActionRow] = {}
        self._others: list[Gtk.Widget] = []
        self._buttons: dict[tuple[str, str], Gtk.Button] = {}
        self._signature: tuple[object, ...] | None = None
        self.dialog: Adw.AlertDialog | Adw.Dialog | None = None
        """The dialog last presented: what a test or probe answers."""
        self.refresh()

    # --- what a test or probe reads -------------------------------------------------------

    @property
    def rows(self) -> dict[str, Adw.ActionRow]:
        """The Presets' rows by slug, in list order."""
        return dict(self._rows)

    @property
    def others(self) -> tuple[tuple[str, str], ...]:
        """(title, subtitle) of the rows that are not a Preset: the empty state, the
        remembered answer."""
        return tuple(
            (row.get_title(), row.get_subtitle() or "")
            for row in self._others
            if isinstance(row, Adw.ActionRow)
        )

    def button(self, slug: str, verb: str) -> Gtk.Button | None:
        """A Preset's "apply", "export" or "delete" button."""
        return self._buttons.get((slug, verb))

    # --- refresh --------------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> None:
        """Rebuild when what the group shows has moved, or when `force`d."""
        wallpaper = self._session.wallpaper_absent_reason()
        signature = (
            self._session.presets_revision,
            self._session.live,
            self._session.offline_reason,
            wallpaper,
            self._actions.remembered(),
        )
        if signature == self._signature and not force:
            return
        self._signature = signature
        self._rebuild(wallpaper)

    def _rebuild(self, wallpaper_reason: str | None) -> None:
        for row in (*self._rows.values(), *self._others):
            self.group.remove(row)
            release(row)
        self._rows = {}
        self._others = []
        self._buttons = {}
        live = self._session.live
        self.group.set_description(
            DESCRIPTION
            if live
            else READ_ONLY_DESCRIPTION.format(reason=self._session.offline_sentence or "")
        )
        presets = self._session.presets()
        remembered = self._actions.remembered()
        if remembered is not None:
            title, subtitle = remembered_row(remembered)
            row = _row(title, subtitle)
            forget = Gtk.Button(label=FORGET, valign=Gtk.Align.CENTER)
            forget.connect("clicked", lambda _button: self._forget())
            row.add_suffix(forget)
            self.group.add(row)
            self._others.append(row)
        if not presets:
            row = _row(EMPTY_TITLE, EMPTY_SUBTITLE)
            self.group.add(row)
            self._others.append(row)
        for slug, preset in presets:
            note = None
            if preset.wallpaper is not None:
                note = (
                    "Includes your wallpaper"
                    if wallpaper_reason is None
                    else f"Its wallpaper will not change. {wallpaper_reason}"
                )
            row = _row(preset.name, subtitle_of(preset, note))
            apply = Gtk.Button(label="Apply", valign=Gtk.Align.CENTER, sensitive=live)
            if not live:
                apply.set_tooltip_text(f"Applying is off. {self._session.offline_sentence}")
            else:
                apply.set_tooltip_text(f"Apply {preset.name}")
            apply.connect("clicked", lambda _button, s=slug: self.apply(s))
            export = _icon_button(
                "document-send-symbolic",
                f"Export {preset.name} as a theme file",
                lambda s=slug: self.export(s),
            )
            delete = _icon_button(
                "user-trash-symbolic", f"Delete {preset.name}", lambda s=slug: self.delete(s)
            )
            for button in (apply, export, delete):
                row.add_suffix(button)
            self._buttons[(slug, "apply")] = apply
            self._buttons[(slug, "export")] = export
            self._buttons[(slug, "delete")] = delete
            self.group.add(row)
            self._rows[slug] = row

    def reveal_preset(self, slug: str) -> Gtk.Widget | None:
        """Flash Preset `slug`'s row, for a search hit (#172). The row, or `None`."""
        row = self._rows.get(slug)
        if row is not None:
            row.grab_focus()
            flash(row)
        return row

    def _present(self, dialog: Adw.AlertDialog | Adw.Dialog) -> None:
        self.dialog = dialog
        dialog.present(self.group)

    def _tell(self, heading: str, body: str) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.add_response("ok", "OK")
        self._present(dialog)

    # --- save -----------------------------------------------------------------------------

    def save(self) -> None:
        """ "Save current as preset": the name and scopes, then the session saves."""
        self._open_save()

    def scope_choices(self) -> list[ScopeChoice]:
        """The checklist: each scope's size, and why one cannot be saved right now."""
        session = self._session
        live = session.live
        wallpaper_set_colors = isinstance(session.color_source(), Wallpaper | Several)
        choices = []
        for scope in CaptureScope:
            if scope is CaptureScope.WALLPAPER:
                reason = (
                    WALLPAPER_NEEDS_HYPRLAND if not live else session.wallpaper_absent_reason()
                )
                choices.append(
                    ScopeChoice(scope, reason or WALLPAPER_SUBTITLE, available=reason is None)
                )
            elif scope is CaptureScope.COLORS and wallpaper_set_colors and not live:
                choices.append(ScopeChoice(scope, COLORS_NEED_HYPRLAND, available=False))
            else:
                choices.append(ScopeChoice(scope, size_text(session.scope_size(scope))))
        return choices

    def _open_save(
        self,
        *,
        name: str = "",
        chosen: frozenset[CaptureScope] | None = None,
        problem: str | None = None,
    ) -> None:
        self._present(
            SavePresetDialog(
                self.scope_choices(),
                on_save=self._save,
                name=name,
                chosen=chosen,
                problem=problem,
            )
        )

    def _save(
        self, name: str, scopes: frozenset[CaptureScope], *, replace: bool = False
    ) -> None:
        self._session.save_preset(
            name,
            scopes,
            replace=replace,
            done=lambda result: self._saved(name, scopes, result),
        )

    def _saved(
        self, name: str, scopes: frozenset[CaptureScope], result: PresetSaveResult
    ) -> None:
        match result:
            case PresetSaved(slug, preset):
                self.refresh(force=True)
                self._actions.toast(f"Saved {preset.name}")
                self.reveal_preset(slug)
            case PresetNameTaken(_slug, taken):
                self._confirm_replace(name, taken, scopes)
            case PresetNotSaved(reason):
                self._open_save(name=name, chosen=scopes, problem=reason)

    def _confirm_replace(self, name: str, taken: str, scopes: frozenset[CaptureScope]) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Replace {taken}?")
        dialog.set_body(
            f"A preset named {taken} already exists. Replacing it overwrites its settings, "
            "and the old ones cannot be brought back."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("replace", "Replace")
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response == "replace":
                self._save(name, scopes, replace=True)
            else:
                self._open_save(name=name, chosen=scopes)

        dialog.connect("response", answered)
        self._present(dialog)

    # --- apply ----------------------------------------------------------------------------

    def apply(self, slug: str) -> None:
        """Say what applying changes, then apply on the user's word."""
        session = self._session
        preset = dict(session.presets()).get(slug)
        if preset is None:
            self.refresh(force=True)
            self._actions.toast("That preset is no longer there.")
            return
        preview = session.preview_preset(preset)
        changes = sum(len(section.changes) for section in preview.sections)
        wallpaper_reason = (
            session.wallpaper_absent_reason() if preset.wallpaper is not None else None
        )
        can_change_wallpaper = preset.wallpaper is not None and wallpaper_reason is None
        conflict = session.preset_color_conflict(preset)
        # Under a wallpaper Color source the model's colours are not the ones on screen, so
        # "already matches" would refuse a Preset that changes what the user sees: ask
        # whose colours win instead (finding 15 of the #153 review).
        if changes == 0 and not can_change_wallpaper and conflict is None:
            self._actions.toast(
                f"{preset.name} already matches your settings. Nothing changed."
            )
            return
        body = changes_text(preset.name, preview)
        remembered = self._actions.remembered()
        question: ColourConflictChoice | None = None
        if conflict is not None:
            if remembered is not None:
                body += f" {remembered_sentence(remembered)}"
            else:
                question = ColourConflictChoice(preset.name, conflict)
        if preset.wallpaper is not None and wallpaper_reason is not None:
            body += f" Its wallpaper will not change. {wallpaper_reason}"
        self._present(
            ApplyPresetDialog(
                preset.name,
                body,
                conflict=question,
                can_change_wallpaper=can_change_wallpaper,
                on_apply=lambda wallpaper, colors, remember: self._apply(
                    slug, wallpaper, colors, remember
                ),
            )
        )

    def _apply(
        self, slug: str, wallpaper: bool, colors: ColorChoice | None, remember: bool
    ) -> None:
        apply = self._actions.apply or self._apply_in_session
        result = apply(slug, wallpaper=wallpaper, colors=colors, remember=remember)
        if isinstance(result, PresetApplied) and result.skipped:
            n = len(result.skipped)
            self._actions.toast(
                f"{n} {'setting was' if n == 1 else 'settings were'} skipped: this version of "
                "Hyprland cannot set them."
            )

    def _apply_in_session(
        self, slug: str, *, wallpaper: bool, colors: ColorChoice | None, remember: bool
    ) -> PresetApplyResult:
        return self._session.apply_preset(slug, colors=colors, wallpaper=wallpaper)

    def _forget(self) -> None:
        self._actions.forget()
        self.refresh(force=True)

    # --- export, import, delete -----------------------------------------------------------

    def export(self, slug: str) -> None:
        preset = dict(self._session.presets()).get(slug)
        if preset is None:
            self.refresh(force=True)
            self._actions.toast("That preset is no longer there.")
            return
        self._actions.choose_export(
            self.group, archive_name(slug), lambda dest: self._exported(preset, dest)
        )

    def _exported(self, preset: Preset, dest: Path) -> None:
        result = export_archive(preset, dest)
        if isinstance(result, ArchiveNotWritten):
            self._tell(f"Could not export {preset.name}", result.reason)
            return
        assert isinstance(result, ArchiveWritten)
        text = f"Exported {preset.name} to {tilde_path(result.path, self._session.paths)}"
        if result.wallpaper_left_out:
            text += f" {result.wallpaper_left_out}"
        self._actions.toast(text)

    def import_theme(self) -> None:
        self._actions.choose_import(self.group, self._import)

    def _import(self, source: Path) -> None:
        dialog = ThemeImportDialog(
            self._session,
            source,
            apply=self._import_apply,
            remembered=self._actions.remembered(),
            on_remember=self._actions.remember,
            on_finished=lambda _imported: self.refresh(force=True),
        )
        self._present(dialog)

    def _import_apply(
        self, slug: str, *, colors: ColorChoice | None = None, wallpaper: bool = False
    ) -> PresetApplyResult:
        apply = self._actions.apply or self._apply_in_session
        return apply(slug, wallpaper=wallpaper, colors=colors, remember=False)

    def delete(self, slug: str) -> None:
        preset = dict(self._session.presets()).get(slug)
        if preset is None:
            self.refresh(force=True)
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Delete {preset.name}?")
        dialog.set_body(
            "The preset is removed from your list. Settings it already applied stay as they "
            "are. This cannot be undone."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def answered(_dialog: Adw.AlertDialog, response: str) -> None:
            if response != "delete":
                return
            refused = self._session.delete_preset(slug)
            self.refresh(force=True)
            if refused is None:
                self._actions.toast(f"Deleted {preset.name}")
            else:
                self._actions.toast(f"{preset.name} was not deleted. {refused}")

        dialog.connect("response", answered)
        self._present(dialog)
