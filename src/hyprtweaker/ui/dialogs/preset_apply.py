"""Apply a Preset: what it will change and what one Ctrl+Z puts back, before it is done (#171).

One confirm carries every question applying can raise, so the user answers once and sees the
whole of it: how many settings change and where, whose colours win when a wallpaper tool sets
them now (#170's `ColourConflictChoice`, unless the user remembered an answer), and whether
the Preset's wallpaper is changed too. Apply is the default: the user pressed Apply, and it
is one Ctrl+Z from undone. Escape and Cancel change nothing.

The words are made by pure functions so tests and the Presets group read them without a
dialog. The dialog owns no applying: `on_apply(wallpaper, colors, remember)` is the window's.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.presets import ColorChoice, PresetPreview  # noqa: E402
from hyprtweaker.ui.dialogs.colour_conflict import ColourConflictChoice  # noqa: E402

CHANGE_WALLPAPER = "Change my wallpaper too"
UNDO_LINE = "One Ctrl+Z puts everything back."


def heading(name: str) -> str:
    return f"Apply {name}?"


def _list(words: Sequence[str]) -> str:
    if len(words) <= 2:
        return " and ".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


def changes_text(name: str, preview: PresetPreview) -> str:
    """What applying changes, by section, and what this Hyprland cannot take."""
    changes = sum(len(section.changes) for section in preview.sections)
    if changes == 0:
        text = f"Every setting in {name} already matches yours."
    else:
        where = _list([section.title for section in preview.sections])
        count = "1 setting" if changes == 1 else f"{changes} settings"
        text = f"{name} changes {count}, in {where}. {UNDO_LINE}"
    left = len(preview.unknown) + len(preview.invalid)
    if left == 1:
        text += " This Hyprland cannot set one setting, so it is skipped."
    elif left:
        text += f" This Hyprland cannot set {left} settings, so they are skipped."
    return text


class ApplyPresetDialog(Adw.AlertDialog):
    """The confirm. `conflict` is the embedded colour question, when it is being asked;
    `wallpaper` is the "change my wallpaper" check, when a daemon can do it."""

    def __init__(
        self,
        name: str,
        body: str,
        *,
        on_apply: Callable[[bool, ColorChoice | None, bool], None],
        conflict: ColourConflictChoice | None = None,
        can_change_wallpaper: bool = False,
    ) -> None:
        super().__init__()
        self.set_heading(heading(name))
        self.set_body(body)
        self._on_apply = on_apply
        self.conflict = conflict
        self.wallpaper: Gtk.CheckButton | None = None
        extras = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        if conflict is not None:
            extras.append(conflict)
        if can_change_wallpaper:
            self.wallpaper = Gtk.CheckButton(label=CHANGE_WALLPAPER, active=True)
            extras.append(self.wallpaper)
        if conflict is not None or can_change_wallpaper:
            self.set_extra_child(extras)
        self.add_response("cancel", "Cancel")
        self.add_response("apply", "Apply")
        self.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("apply")
        self.set_close_response("cancel")
        self.connect("response", self._on_response)

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if response != "apply":
            return
        wallpaper = self.wallpaper is not None and self.wallpaper.get_active()
        colors = self.conflict.choice if self.conflict is not None else None
        remember = self.conflict is not None and self.conflict.remember.get_active()
        self._on_apply(wallpaper, colors, remember)
