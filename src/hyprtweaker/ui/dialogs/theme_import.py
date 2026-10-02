"""Import a Theme archive: what it holds and what it would change, before anything is done.

The migration wizard's idiom (`ui/dialogs/migration.py`): an `Adw.NavigationView` of pages,
a bottom bar of actions, and a default button that changes nothing. An archive is untrusted
input, as a foreign `hyprland.lua` is (#190), but it is never run, so there is no consent
page: reading it writes nothing, and the preview is where the user agrees. The words on the
confirm button are what will happen: "Add to Presets" offline, "Import and Apply" live.

The dialog owns no import logic. Reading is `engine.presets_archive.read_archive`, the
preview is `Session.preview_preset`, adding is `Session.import_preset` (never an overwrite),
and applying is the `apply` it is given, `Session.apply_preset` by default: one Apply
transaction, one Ctrl+Z (#168).

The colour question (#170, #171). When the Preset carries colours and a wallpaper tool sets
them now, the preview asks whose win, in `slot` under the summary, because the answer changes
what "Import and Apply" does. A remembered answer is not asked again: one sentence says it
applies. The choice reaches the apply as `apply(slug, colors=...)`, and a ticked "Remember my
choice" goes to `on_remember`. The file chooser and the menu entry are the Presets group's.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ...engine.presets import (  # noqa: E402
    ColorChoice,
    PresetApplied,
    PresetApplyResult,
    PresetColorConflict,
    PresetImported,
    PresetNotImported,
    PresetPreview,
)
from ...engine.presets_archive import (  # noqa: E402
    ArchiveRefused,
    Codec,
    ThemeArchive,
    find_codec,
    read_archive,
)
from ...session import Session  # noqa: E402
from ..rows.state import value_label  # noqa: E402
from .colour_conflict import ColourConflictChoice, remembered_sentence  # noqa: E402
from .migration import _actions, _column, _page, _scrolled, _suggested  # noqa: E402

UNKNOWN_SUBTITLE = "This version of Hyprland does not have it"
INVALID_SUBTITLE = "Its value does not fit this setting"


class ThemeImportDialog(Adw.Dialog):
    """Preview a Theme archive, then add it to the Presets and apply it, or not."""

    def __init__(
        self,
        session: Session,
        source: Path,
        *,
        apply: Callable[..., PresetApplyResult] | None = None,
        remembered: ColorChoice | None = None,
        on_remember: Callable[[ColorChoice], None] | None = None,
        on_finished: Callable[[PresetImported], None] | None = None,
        find: Callable[[], Codec | None] = find_codec,
    ) -> None:
        super().__init__(title="Import theme", content_width=560, content_height=620)
        self._session = session
        self._apply = apply or session.apply_preset
        self._on_finished = on_finished
        self._remembered = remembered
        self._on_remember = on_remember
        self.choice: ColourConflictChoice | None = None
        """The colour question, while it is being asked in the preview."""
        self._defaults: dict[Adw.NavigationPage, Gtk.Widget] = {}
        """Each page's safe button, made the dialog's default while that page shows."""
        self.slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        """Empty: where the caller adds a row of its own under the summary (#171). Shown
        only when something is in it, so an empty slot leaves no gap in the column."""
        self.slot.set_visible(False)
        self._slot_children = self.slot.observe_children()
        self._slot_children.connect(
            "items-changed",
            lambda children, *_: self.slot.set_visible(children.get_n_items() > 0),
        )

        self._view = Adw.NavigationView()
        self._view.connect("notify::visible-page", self._on_visible_page)
        self.set_child(self._view)
        read = read_archive(source, find=find)
        if isinstance(read, ArchiveRefused):
            self.set_content_height(260)  # one sentence: not a tall empty sheet
            self._view.push(self._stopped_page("Can't import this theme", read.reason))
        else:
            self._view.push(self._preview_page(read))

    def _on_visible_page(self, view: Adw.NavigationView, _pspec: Any) -> None:
        self.set_default_widget(self._defaults.get(view.get_visible_page()))

    # --- the preview ------------------------------------------------------------------------

    def _preview_page(self, archive: ThemeArchive) -> Adw.NavigationPage:
        preset = archive.preset
        preview = self._session.preview_preset(preset)
        live = self._session.live
        page = _page("Import theme")

        summary = Adw.PreferencesGroup(
            # The name is the archive's text and may hold `&` or `<`: escaped, not markup.
            title=GLib.markup_escape_text(f"Import {preset.name}?"),
            description=GLib.markup_escape_text(
                _summary(
                    preset.name,
                    preview,
                    archive,
                    self._session.offline_sentence,
                    self._session.import_name(preset),
                )
            ),
        )
        groups: list[Gtk.Widget] = [summary]
        source = self._session.preset_color_conflict(preset) if live else None
        if source is not None and self._remembered is None:
            self.choice = ColourConflictChoice(preset.name, source)
            self.slot.append(self.choice)
        elif source is not None and self._remembered is not None:
            note = Gtk.Label(label=remembered_sentence(self._remembered), xalign=0, wrap=True)
            note.add_css_class("dim-label")
            self.slot.append(note)
        picture = _wallpaper_group(archive)
        if picture is not None:
            groups.append(picture)
        groups.append(self.slot)
        groups.extend(_change_groups(preview))
        groups.extend(_unchanged_group(self._session, preview))
        groups.extend(_left_out_group(preview, archive.dropped))
        page.get_child().set_content(_scrolled(_column(*groups)))

        confirm = _suggested("Import and apply" if live else "Add to presets")
        confirm.connect("clicked", lambda button: self._confirm(button, archive, live))
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda _button: self.close())
        page.get_child().add_bottom_bar(_actions(confirm, cancel))
        self._defaults[page] = cancel
        return page

    def _confirm(self, button: Gtk.Button, archive: ThemeArchive, live: bool) -> None:
        button.set_sensitive(False)  # a second click would import a second copy
        imported = self._session.import_preset(archive)
        if isinstance(imported, PresetNotImported):
            self._view.push(self._stopped_page("Nothing was imported", imported.reason))
            return
        if live:
            colors = self.choice.choice if self.choice is not None else self._remembered
            remembering = self.choice is not None and self.choice.remember.get_active()
            if self.choice is not None and remembering and self._on_remember is not None:
                self._on_remember(self.choice.choice)
            applied = self._apply(imported.slug, colors=colors)
            if not isinstance(applied, PresetApplied):
                # A colour question this dialog did not ask (the sources moved between the
                # preview and the click) applied nothing: the Presets list asks it.
                reason = (
                    "Its colors and the ones your wallpaper sets would compete, so apply it "
                    "from your presets to choose which win."
                    if isinstance(applied, PresetColorConflict)
                    else applied.reason
                )
                self._finish(imported)
                self._view.push(
                    self._stopped_page(
                        "Added, but not applied",
                        f"{imported.preset.name} is in your presets. {reason}",
                    )
                )
                return
        # Applied: the window's toast says so and offers Ctrl+Z, so the dialog gets out of
        # the way of it. Added only: the Presets list shows the new row.
        self._finish(imported)
        self.close()

    def _finish(self, imported: PresetImported) -> None:
        if self._on_finished is not None:
            self._on_finished(imported)

    # --- shared pages -----------------------------------------------------------------------

    def _stopped_page(self, title: str, body: str) -> Adw.NavigationPage:
        page = _page(title)
        page.set_can_pop(False)
        # `body` quotes the archive's member names and error text: escaped, not markup.
        group = Adw.PreferencesGroup(title=title, description=GLib.markup_escape_text(body))
        page.get_child().set_content(_column(group))
        close = _suggested("Close")
        close.connect("clicked", lambda _button: self.close())
        page.get_child().add_bottom_bar(_actions(close))
        self._defaults[page] = close
        return page


# --- what the preview shows -------------------------------------------------------------------


def _summary(
    name: str,
    preview: PresetPreview,
    archive: ThemeArchive,
    offline: str | None,
    landing: str,
) -> str:
    """What importing does, in words. `offline` is why applying is off, or `None` when on."""
    changes = sum(len(section.changes) for section in preview.sections)
    if changes == 0:
        effect = "Every setting it holds already matches yours."
    elif offline is None:
        effect = (
            f"Importing adds {name} to your presets and applies it, changing "
            f"{_count(changes)}. Press Ctrl+Z afterwards to put them back."
        )
    else:
        effect = (
            f"Importing adds {name} to your presets. Applying it would change "
            f"{_count(changes)}, but applying is off. {offline}"
        )
    if changes == 0:
        effect += f" Importing adds {name} to your presets."
    if landing != name:
        effect += (
            f" You already have a preset called {name}, so this one is added as {landing}."
        )
    if archive.newer_format:
        effect += (
            " It was made by a newer version of this app, so anything this version does not "
            "know is left out."
        )
    return effect


def _count(changes: int) -> str:
    return "1 setting" if changes == 1 else f"{changes} settings"


def _wallpaper_group(archive: ThemeArchive) -> Adw.PreferencesGroup | None:
    """The archive's image, decoded from its bytes, never from a path the archive names.

    The engine has already checked its type and bounded its pixel size.
    """
    image = archive.wallpaper
    if image is None:
        return None
    group = Adw.PreferencesGroup(
        title="Wallpaper",
        description=(
            "Saved with the preset. Importing leaves your wallpaper as it is: to show this "
            "one, apply the preset and choose to change the wallpaper."
        ),
    )
    try:
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(image.data))
    except GLib.Error:
        group.add(_text_row("Wallpaper", "This image cannot be previewed here."))
        return group
    picture = Gtk.Picture(
        paintable=texture,
        content_fit=Gtk.ContentFit.CONTAIN,
        can_shrink=True,
        height_request=180,
    )
    picture.set_alternative_text(f"The wallpaper of {archive.preset.name}")
    picture.add_css_class("card")
    group.add(picture)
    return group


def _change_groups(preview: PresetPreview) -> list[Adw.PreferencesGroup]:
    groups = []
    for section in preview.sections:
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(section.title))
        for change in section.changes:
            before = value_label(change.option, change.before)
            after = value_label(change.option, change.after)
            group.add(_text_row(change.option.title, f"{before} → {after}"))
        groups.append(group)
    return groups


def _unchanged_group(session: Session, preview: PresetPreview) -> list[Adw.PreferencesGroup]:
    if not preview.unchanged:
        return []
    group = Adw.PreferencesGroup(
        title="Already the same",
        description="The theme sets these to what they already are.",
    )
    for name in preview.unchanged:
        option = session.schema[name]
        group.add(_text_row(option.title, value_label(option, session.effective_value(option))))
    return [group]


def _left_out_group(
    preview: PresetPreview, dropped: tuple[str, ...]
) -> list[Adw.PreferencesGroup]:
    if not (preview.unknown or preview.invalid or dropped):
        return []
    group = Adw.PreferencesGroup(
        title="Left out",
        description="These are skipped; everything else is imported.",
    )
    for name in preview.unknown:
        group.add(_text_row(name, UNKNOWN_SUBTITLE))
    for name in (*preview.invalid, *dropped):
        group.add(_text_row(name, INVALID_SUBTITLE))
    return [group]


def _text_row(title: str, subtitle: str) -> Adw.ActionRow:
    """A Row of plain text. Markup is off before the text is set: a key or a name from an
    archive is someone else's text, and `&` in it must not be parsed first."""
    row = Adw.ActionRow()
    row.set_use_markup(False)
    row.set_title(title)
    row.set_subtitle(subtitle)
    row.set_subtitle_selectable(True)
    return row
