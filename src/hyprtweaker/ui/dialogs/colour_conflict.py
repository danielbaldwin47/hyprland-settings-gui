"""Whose colours win when a Preset carries Colors and a wallpaper sets them (ADR-0014).

Two shapes of one question. `ColourConflictDialog` asks it when a Preset is applied: its two
buttons are the answers, and "Remember my choice" stores the answer in `Prefs.remembered`
under `COLOR_CONFLICT_DIALOG`, so the next apply does not ask (the main menu's "Forget
remembered choices" clears it). `ColourConflictChoice` is the same question as a widget
whose answer is read later, for a dialog with its own buttons: the import preview embeds it.

The words say what "Use preset's colors" does to the wallpaper tool -- it is paused, not
removed -- and that one Ctrl+Z puts both back, so neither answer is a dead end.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.bridge import REGISTRY, Several, Wallpaper  # noqa: E402
from hyprtweaker.engine.presets import ColorChoice  # noqa: E402

COLOR_CONFLICT_DIALOG = "preset-colors"
"""The key the remembered answer is stored under in `Prefs.remembered`."""

USE_LABEL = "Use preset's colors"
KEEP_LABEL = "Keep wallpaper colors"
REMEMBER_LABEL = "Remember my choice"
REMEMBER_HINT = "Forget it any time from the main menu."

_RESPONSES = {"use": ColorChoice.USE_PRESET, "keep": ColorChoice.KEEP_WALLPAPER}


def remembered_choice(remembered: Mapping[str, str]) -> ColorChoice | None:
    """The stored answer, or `None` to ask. A value this build does not know asks again."""
    stored = remembered.get(COLOR_CONFLICT_DIALOG)
    try:
        return ColorChoice(stored) if stored is not None else None
    except ValueError:
        return None


def conflict_heading(preset: str) -> str:
    return f"Use {preset}'s colors?"


def conflict_body(preset: str, source: Wallpaper | Several) -> str:
    """What each answer does, naming the tool that sets the colours now."""
    names = [
        spec.title if (spec := REGISTRY.get(tool)) is not None else tool
        for tool in ((source.tool,) if isinstance(source, Wallpaper) else source.tools)
    ]
    if len(names) == 1:
        tools, sets, them = names[0], "sets", "it"
    else:
        tools, sets, them = f"{', '.join(names[:-1])} and {names[-1]}", "set", "one"
    return (
        f"{tools} {sets} your colors from the wallpaper. Using {preset}'s colors pauses "
        f"{tools} until you choose {them} again on the Theming page. Ctrl+Z puts both back."
    )


def remembered_sentence(choice: ColorChoice) -> str:
    """A remembered answer as one sentence, for the confirm that is not asking again."""
    if choice is ColorChoice.USE_PRESET:
        return (
            "You asked to remember using a preset's colors, so they replace the ones your "
            "wallpaper makes and that tool is paused. Forget it in the Presets group."
        )
    return (
        "You asked to remember keeping your wallpaper's colors, so the preset's colors are "
        "left out. Forget it in the Presets group."
    )


def _remember_check() -> Gtk.CheckButton:
    check = Gtk.CheckButton(label=REMEMBER_LABEL)
    check.set_tooltip_text(REMEMBER_HINT)
    return check


class ColourConflictDialog(Adw.AlertDialog):
    """The question as a dialog: its two buttons answer, and the check remembers.

    "Use preset's colors" is the default, because the user asked for the Preset and it is
    one Ctrl+Z from undone. Escape or closing applies nothing.
    """

    def __init__(
        self,
        preset: str,
        source: Wallpaper | Several,
        *,
        on_choice: Callable[[ColorChoice, bool], None],
    ) -> None:
        super().__init__(heading=conflict_heading(preset), body=conflict_body(preset, source))
        self._on_choice = on_choice
        self.add_response("keep", KEEP_LABEL)
        self.add_response("use", USE_LABEL)
        self.set_response_appearance("use", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("use")
        self.set_close_response("cancel")
        self.remember = _remember_check()
        self.set_extra_child(self.remember)
        self.connect("response", self._on_response)

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        choice = _RESPONSES.get(response)
        if choice is not None:
            self._on_choice(choice, self.remember.get_active())


class ColourConflictChoice(Gtk.Box):
    """The question as a widget, for a dialog that has its own Apply: read `choice` and
    `remember` when it is answered."""

    def __init__(self, preset: str, source: Wallpaper | Several) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        heading = Gtk.Label(label=conflict_heading(preset), xalign=0)
        heading.add_css_class("heading")
        body = Gtk.Label(label=conflict_body(preset, source), xalign=0, wrap=True)
        body.add_css_class("dim-label")
        self.use = Gtk.CheckButton(label=USE_LABEL, active=True)
        self.keep = Gtk.CheckButton(label=KEEP_LABEL, group=self.use)
        self.remember = _remember_check()
        for child in (heading, body, self.use, self.keep, self.remember):
            self.append(child)

    @property
    def choice(self) -> ColorChoice:
        return ColorChoice.USE_PRESET if self.use.get_active() else ColorChoice.KEEP_WALLPAPER
