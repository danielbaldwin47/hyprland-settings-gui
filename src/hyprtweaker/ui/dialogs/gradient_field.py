"""The gradient editor for a rule's `border_color` (#156, ADR-0008).

One `Adw.ExpanderRow` (an `EffectHelper`, built on `GrammarRow`) showing a row of colour
stops and an angle scale, with the shared "Edit as text" toggle beside them. It carries
exactly one gradient, the single-gradient case of the Lua table
`{ colors = { ... }, angle = N }`. The legacy string for an active+inactive pair has no
table form: it opens as text, unchanged, and saves verbatim (ADR-0008).

**Commit model.** A rule editor is a modal dialog that commits once, on Save; nothing here
touches the Apply path. A stop is a modal `Gtk.ColorDialog` (ADR-0010 § Eval preview,
amended in spec #149 after grill #93): its button changes colour when the dialog closes,
never while a pointer is held. The angle is a plain scale read on Save, with no preview.

`RowFactory._gradient` is the Option version of this editor and stays apart from it: it is
bound to an Option, a gesture and the Apply pipeline, and this one is not.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine import rule_grammars  # noqa: E402
from hyprtweaker.engine.model.values import Color, Gradient  # noqa: E402
from hyprtweaker.ui.dialogs.effect_helpers import GrammarRow  # noqa: E402
from hyprtweaker.ui.rows.factory import color_of, gdk_rgba  # noqa: E402

_DEFAULT_STOP = Color(0xFFFFFFFF)
"""What a new `border_color` effect, and a stop the user adds to nothing, starts as."""


class GradientRow(GrammarRow[Gradient]):
    """Colour stops (add, remove, never fewer than one) and an angle from 0 to 360."""

    def __init__(self, original: object | None) -> None:
        super().__init__(
            original,
            parse=rule_grammars.parse_border_color,
            emit=rule_grammars.emit_border_color,
            default=Gradient((_DEFAULT_STOP,), 0.0),
            text=rule_grammars.border_color_text,
            source_text=rule_grammars.border_color_source_text,
        )

    def _build(self) -> list[Gtk.Widget]:
        self.stop_buttons: list[Gtk.ColorDialogButton] = []
        self.remove_buttons: list[Gtk.Button] = []
        self._stops = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.add_button = Gtk.Button(
            icon_name="list-add-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="Add a colour",
        )
        self.add_button.add_css_class("flat")
        self.add_button.connect("clicked", self._on_add)

        colours = Adw.ActionRow(
            title="Colours", subtitle="Blended in order, along the angle", use_markup=False
        )
        colours.add_suffix(self._stops)
        colours.add_suffix(self.add_button)

        self.angle = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(
                lower=0.0,
                upper=float(rule_grammars.MAX_ANGLE),
                step_increment=1.0,
                page_increment=15.0,
            ),
            digits=0,
            draw_value=True,
            value_pos=Gtk.PositionType.LEFT,
            hexpand=True,
            valign=Gtk.Align.CENTER,
            width_request=240,
        )
        self.angle.set_format_value_func(lambda _scale, value: f"{value:.0f}°")
        self.angle.connect("value-changed", self._changed)
        angle = Adw.ActionRow(title="Angle", use_markup=False)
        angle.add_suffix(self.angle)
        return [colours, angle]

    # --- stops ----------------------------------------------------------------------------

    def _add_stop(self, color: Color) -> None:
        button = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=True, modal=True),
            rgba=gdk_rgba(color),
            valign=Gtk.Align.CENTER,
        )
        button.connect("notify::rgba", self._changed)
        remove = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.set_tooltip_text("Remove this colour")
        remove.connect("clicked", self._on_remove, button)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.add_css_class("linked")
        box.append(button)
        box.append(remove)
        self._stops.append(box)
        self.stop_buttons.append(button)
        self.remove_buttons.append(remove)

    def _clear_stops(self) -> None:
        while (child := self._stops.get_first_child()) is not None:
            self._stops.remove(child)
        self.stop_buttons.clear()
        self.remove_buttons.clear()

    def _on_add(self, _button: Gtk.Button) -> None:
        self._add_stop(color_of(self.stop_buttons[-1]))
        self._stops_changed()
        self._changed()

    def _on_remove(self, _button: Gtk.Button, stop: Gtk.ColorDialogButton) -> None:
        if len(self.stop_buttons) <= 1:
            return
        index = self.stop_buttons.index(stop)
        colors = [color_of(b) for b in self.stop_buttons]
        del colors[index]
        self._clear_stops()
        for color in colors:
            self._add_stop(color)
        self._stops_changed()
        self._changed()

    def _stops_changed(self) -> None:
        """Keep the add and remove buttons to what the row can do: one stop to
        `MAX_STOPS`."""
        count = len(self.stop_buttons)
        self.add_button.set_sensitive(count < rule_grammars.MAX_STOPS)
        for remove in self.remove_buttons:
            remove.set_sensitive(count > 1)
        for number, button in enumerate(self.stop_buttons, start=1):
            button.set_tooltip_text(f"Colour {number}")

    # --- the typed value ------------------------------------------------------------------

    def _load(self, typed: Gradient) -> None:
        self._clear_stops()
        for color in typed.colors:
            self._add_stop(color)
        self._stops_changed()
        self.angle.set_value(typed.angle)

    def _read(self) -> Gradient:
        return Gradient(
            tuple(color_of(button) for button in self.stop_buttons),
            float(round(self.angle.get_value())),
        )
