"""Helper widgets for the rule effects whose value is a string grammar (#155, ADR-0008).

A helper is one `Adw.ExpanderRow` that shows a grammar's controls, with an "Edit as text"
toggle beside them. It keeps the contract the rule editor builds on (`EffectHelper`):

- `widget` is the row, an `Adw.PreferencesRow` with `add_suffix`, which takes the editor's
  remove button;
- `value()` is what Save collects: the original object while nothing was edited, else the
  string the grammar emits (or the text, in text mode);
- `blank()` is true when there is nothing to save, which the editor refuses.

**Text mode.** A value `parse_*` cannot carry unchanged opens in text mode with its text
as it was, and the toggle cannot leave text mode while the text does not parse. Nothing a
helper cannot show is dropped or rewritten.

`GrammarRow` is the shared half: the toggle, the text entry, the mode switch and the
"untouched keeps the original" rule. A grammar subclasses it and supplies its controls
(`_build`) and how to move a typed value in and out of them. The gradient editor (#156,
`gradient_field`) is one such subclass in a file of its own; its value is a table, so the
emit step may return any object, and `text` says how the typed value reads as text.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Generic, Protocol, TypeVar

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine import rule_grammars  # noqa: E402
from hyprtweaker.engine.rule_grammars import (  # noqa: E402
    FULLSCREEN_STATE_CHOICES,
    SUPPRESS_EVENTS,
    FullscreenState,
    Opacity,
    OpacityState,
)

T = TypeVar("T")


class EffectHelper(Protocol):
    """What the rule editor needs from a helper row."""

    @property
    def widget(self) -> Adw.PreferencesRow:
        """The row to mount in the Effects group; `add_suffix` takes the remove button."""
        ...

    def value(self) -> object:
        """The effect's value to save: the original object when untouched."""
        ...

    def blank(self) -> bool:
        """True when there is nothing to save; the editor refuses to save a blank effect."""
        ...


EffectHelperBuilder = Callable[[object | None], EffectHelper]
"""A registry entry: builds a helper from the effect's original value (`None` for an
effect the user just added)."""


def effect_text(value: Any) -> str:
    """A raw effect value as entry text, and the identity check for "untouched".

    A list renders as its space-joined items because that *is* the string grammar the
    vec2 effects accept (`move`/`size` take `"x y"` or `{x, y}`) -- so a user who edits
    the shown text saves a value the compositor still understands, instead of a Python
    repr. Untouched rows never reach this conversion; they keep the original object.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


class GrammarRow(Generic[T]):
    """An `EffectHelper` for one string grammar, in controls or in text."""

    def __init__(
        self,
        original: object | None,
        *,
        parse: Callable[[object], T | None],
        emit: Callable[[T], object],
        default: T,
        text: Callable[[T], str] | None = None,
        source_text: Callable[[object], str] = effect_text,
        explain: Callable[[str], str | None] | None = None,
    ) -> None:
        """`emit` is the value to save for the controls; `text` is how a typed value reads
        as text (the emitted string when `emit` returns one); `source_text` is how an
        original value reads as text, for one `parse` rejected or text mode opened on;
        `explain` says why the controls cannot show a text, where the grammar knows more
        than "cannot show this value"."""
        self._original = original
        self._explain = explain
        self._parse = parse
        self._emit = emit
        self._text_of = text
        self._source_text_of = source_text
        self._loading = True
        # True once the user changed anything; a new effect has no original to keep.
        self._edited = original is None

        self.widget = Adw.ExpanderRow(use_markup=False, expanded=True)
        self.raw_toggle = Gtk.ToggleButton(label="Edit as text", valign=Gtk.Align.CENTER)
        self.raw_toggle.add_css_class("flat")
        self.raw_toggle.connect("toggled", self._on_toggled)
        self.widget.add_suffix(self.raw_toggle)

        self.raw_entry = Adw.EntryRow(title="Value", use_markup=False)
        self.raw_entry.connect("changed", self._on_text_changed)
        self.widget.add_row(self.raw_entry)

        self._rows = self._build()
        for row in self._rows:
            self.widget.add_row(row)

        typed = None if original is None else parse(original)
        if typed is None and original is not None:
            # The controls stay loaded behind the text, so reading them never meets a
            # half-built row (the entry's `changed` summarises the controls first).
            self._load(default)
            self.raw_entry.set_text(self._source_text_of(original))
            self._show_mode(raw=True)
        else:
            self._load(default if typed is None else typed)
            self._show_mode(raw=False)
        self._loading = False

    # --- what a grammar supplies ----------------------------------------------------------

    def _build(self) -> list[Gtk.Widget]:
        """Create the controls (connect their change signals to `_changed`) and return
        them in display order."""
        raise NotImplementedError

    def _load(self, typed: T) -> None:
        """Show `typed` in the controls."""
        raise NotImplementedError

    def _read(self) -> T:
        """The typed value the controls show."""
        raise NotImplementedError

    def _empty(self, typed: T) -> bool:
        """True when `typed` is a value with nothing in it (an empty checklist)."""
        return False

    # --- the EffectHelper contract --------------------------------------------------------

    def value(self) -> object:
        if self._original is not None and not self._edited:
            return self._original
        if self._raw:
            return str(self.raw_entry.get_text()).strip()
        return self._emit(self._read())

    def blank(self) -> bool:
        if self._raw:
            return not self.raw_entry.get_text().strip()
        return self._empty(self._read())

    # --- modes ----------------------------------------------------------------------------

    @property
    def _raw(self) -> bool:
        return bool(self.raw_toggle.get_active())

    def _text(self) -> str:
        """The value as text: what the entry holds, or what the controls say."""
        if self._raw:
            return str(self.raw_entry.get_text()).strip()
        return self._controls_text()

    def _controls_text(self) -> str:
        typed = self._read()
        return self._text_of(typed) if self._text_of is not None else str(self._emit(typed))

    def _changed(self, *_args: object) -> None:
        """A control changed: the row is no longer untouched."""
        if not self._loading:
            self._edited = True
            self._summarize()

    def _on_text_changed(self, _entry: Adw.EntryRow) -> None:
        if not self._loading:
            self._edited = True
        self._summarize()

    def _on_toggled(self, _toggle: Gtk.ToggleButton) -> None:
        if self._loading:
            return
        if self._raw:
            self._loading = True
            self.raw_entry.set_text(self._source_text())
            self._loading = False
            self._show_mode(raw=True)
            return
        typed = self._parse(self.raw_entry.get_text())
        if typed is None:
            # The sensitivity rule already stops this; a programmatic toggle lands here.
            self._loading = True
            self.raw_toggle.set_active(True)
            self._loading = False
            return
        self._loading = True
        self._load(typed)
        self._loading = False
        self._show_mode(raw=False)

    def _source_text(self) -> str:
        """The text to open text mode with: the original's own spelling while untouched."""
        if self._original is not None and not self._edited:
            return self._source_text_of(self._original)
        return self._controls_text()

    def _show_mode(self, *, raw: bool) -> None:
        was = self._loading
        self._loading = True
        self.raw_toggle.set_active(raw)
        self._loading = was
        self.raw_entry.set_visible(raw)
        for row in self._rows:
            row.set_visible(not raw)
        self._summarize()

    def _summarize(self) -> None:
        """The subtitle and the toggle's state, for the mode and the text as they are."""
        if not self._raw:
            self.raw_toggle.set_sensitive(True)
            self.raw_toggle.set_tooltip_text("Edit the value as text")
            summary = self._text()
            self.widget.set_subtitle(summary)
            return
        text = self.raw_entry.get_text()
        fits = self._parse(text) is not None
        self.raw_toggle.set_sensitive(fits)
        if fits:
            self.raw_toggle.set_tooltip_text("Back to the controls")
            self.widget.set_subtitle(text.strip())
        else:
            self.raw_toggle.set_tooltip_text("The controls cannot show this text as it is")
            reason = self._explain(text) if self._explain is not None else None
            if reason is None:
                reason = (
                    "The controls cannot show this value. Edit it as text."
                    if text.strip()
                    else "Needs a value."
                )
            self.widget.set_subtitle(reason)


def border_pair_reason(text: str) -> str | None:
    """Why a `border_color` text stays text, when it is the active+inactive pair: two
    colours and no angle, which the legacy string means and one gradient cannot say."""
    tokens = text.split()
    if len(tokens) == 2 and all(
        rule_grammars.parse_border_color(t) is not None for t in tokens
    ):
        return (
            "Two colors without an angle are the active and inactive border. Edit them as text."
        )
    return None


# --- opacity ------------------------------------------------------------------------------

_OPACITY_STATES = ("Active window", "Inactive window", "Fullscreen window")
_FOLLOWS_ACTIVE = "Same as the active window"
_OVERRIDE_TIP = "Use this value as it is, instead of multiplying it with the global opacity"


class OpacityRow(GrammarRow[Opacity]):
    """Three spin buttons (active, inactive, fullscreen), each with an override check.

    One value copies to the other two states in the grammar, so the inactive and
    fullscreen rows follow the active one until the user sets either of them."""

    def __init__(self, original: object | None) -> None:
        self._linked = True
        super().__init__(
            original,
            parse=rule_grammars.parse_opacity,
            emit=rule_grammars.emit_opacity,
            default=Opacity(OpacityState(1.0), OpacityState(1.0), OpacityState(1.0)),
        )

    def _build(self) -> list[Gtk.Widget]:
        self.spins: tuple[Adw.SpinRow, ...] = tuple(
            Adw.SpinRow.new_with_range(0.0, 1.0, 0.05) for _ in _OPACITY_STATES
        )
        self.overrides: tuple[Gtk.CheckButton, ...] = tuple(
            Gtk.CheckButton(
                label="Override", valign=Gtk.Align.CENTER, tooltip_text=_OVERRIDE_TIP
            )
            for _ in _OPACITY_STATES
        )
        for index, (title, spin, override) in enumerate(
            zip(_OPACITY_STATES, self.spins, self.overrides, strict=True)
        ):
            spin.set_title(title)
            spin.set_digits(2)
            spin.add_suffix(override)
            spin.connect("notify::value", self._on_state_changed, index)
            override.connect("toggled", self._on_state_changed, index)
        return list(self.spins)

    def _on_state_changed(self, _widget: object, *args: object) -> None:
        if self._loading:
            return
        index = args[-1]
        if self._linked and index == 0:
            self._loading = True
            for spin, override in zip(self.spins[1:], self.overrides[1:], strict=True):
                spin.set_value(self.spins[0].get_value())
                override.set_active(self.overrides[0].get_active())
            self._loading = False
        elif index != 0:
            self._linked = False
            self._label_links()
        self._changed()

    def _label_links(self) -> None:
        for spin in self.spins[1:]:
            spin.set_subtitle(_FOLLOWS_ACTIVE if self._linked else "")

    def _load(self, typed: Opacity) -> None:
        for spin, override, state in zip(self.spins, self.overrides, typed.states, strict=True):
            spin.set_value(state.value)
            override.set_active(state.override)
        self._linked = typed.active == typed.inactive == typed.fullscreen
        self._label_links()

    def _read(self) -> Opacity:
        active, inactive, fullscreen = (
            OpacityState(round(spin.get_value(), 2), override.get_active())
            for spin, override in zip(self.spins, self.overrides, strict=True)
        )
        return Opacity(active, inactive, fullscreen)


# --- fullscreen_state ---------------------------------------------------------------------


class FullscreenStateRow(GrammarRow[FullscreenState]):
    """Two pickers, 0 to 3: what Hyprland treats the window as, what the app is told."""

    def __init__(self, original: object | None) -> None:
        super().__init__(
            original,
            parse=rule_grammars.parse_fullscreen_state,
            emit=rule_grammars.emit_fullscreen_state,
            default=FullscreenState(0, 0),
        )

    def _build(self) -> list[Gtk.Widget]:
        self.internal = self._picker("Internal state", "What Hyprland treats the window as")
        self.client = self._picker("Client state", "What the app is told")
        return [self.internal, self.client]

    def _picker(self, title: str, subtitle: str) -> Adw.ComboRow:
        combo = Adw.ComboRow(
            title=title,
            subtitle=subtitle,
            use_markup=False,
            model=Gtk.StringList.new(list(FULLSCREEN_STATE_CHOICES)),
        )
        combo.connect("notify::selected", self._changed)
        return combo

    def _load(self, typed: FullscreenState) -> None:
        self.internal.set_selected(typed.internal)
        self.client.set_selected(typed.client)

    def _read(self) -> FullscreenState:
        return FullscreenState(self.internal.get_selected(), self.client.get_selected())


# --- suppress_event -----------------------------------------------------------------------

_EVENT_WORDS = {
    "fullscreen": ("Fullscreen requests", "The window cannot make itself fullscreen"),
    "maximize": ("Maximize requests", "The window cannot maximize itself"),
    "activate": ("Activation requests", "The window cannot bring itself to the front"),
    "activatefocus": (
        "Focus on activation",
        "The window can ask for attention but not take focus",
    ),
    "fullscreenoutput": (
        "Fullscreen monitor choice",
        "The window goes fullscreen where it is, not on the monitor it asks for",
    ),
    "x11configurerequest": (
        "X11 move and resize requests",
        "A floating X11 window cannot move or resize itself",
    ),
}
"""Each event's title and what suppressing it does, read from Hyprland 0.56.2's
`Window.cpp`. The keys themselves are one toggle away, in Edit as text."""


class SuppressEventRow(GrammarRow[tuple[str, ...]]):
    """A checklist of the events a window is kept from triggering."""

    def __init__(self, original: object | None) -> None:
        super().__init__(
            original,
            parse=rule_grammars.parse_suppress_event,
            emit=rule_grammars.emit_suppress_event,
            default=(),
        )

    def _build(self) -> list[Gtk.Widget]:
        self.switches: dict[str, Adw.SwitchRow] = {}
        for event in SUPPRESS_EVENTS:
            title, subtitle = _EVENT_WORDS[event]
            switch = Adw.SwitchRow(title=title, subtitle=subtitle, use_markup=False)
            switch.connect("notify::active", self._changed)
            self.switches[event] = switch
        return list(self.switches.values())

    def _load(self, typed: tuple[str, ...]) -> None:
        for event, switch in self.switches.items():
            switch.set_active(event in typed)

    def _read(self) -> tuple[str, ...]:
        return tuple(event for event, switch in self.switches.items() if switch.get_active())

    def _empty(self, typed: tuple[str, ...]) -> bool:
        return not typed
