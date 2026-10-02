"""`GapField`: four gap sides entered as one number or as four, for entity rows (#193).

The Option rows have this control already (`RowFactory._css_gaps`), but that one is bound
to a `ResolvedOption` and the Option write path. Entity fields -- a monitor's reserved
area, a workspace rule's gaps -- have neither, so they share this self-contained twin:
a value in, committed values out through `on_commit`, the same copy and the same rules.

**Commits, not keystrokes.** `on_commit` fires once per committed value: Enter in a spin
button, focus leaving it, or the "Same on all sides" toggle. The pages that host it
rebuild after every applied edit, so a per-keystroke write would tear the control down
under the user's cursor.

**The shape the file has.** An `int` (or no value) opens on one number, a mapping on four
sides, so the user sees what their config says. Uniform commits an `int`; per-side commits
a `{"top", "right", "bottom", "left"}` dict, Hyprland's css-gap table order.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.ui.rows.factory import caption  # noqa: E402

SIDES: tuple[str, ...] = ("top", "right", "bottom", "left")
"""CSS order, and Hyprland's: a four-element css-gap table reads top, right, bottom, left."""

GapValue = int | dict[str, int]
"""What a commit carries: one number for every side, or all four sides by name."""


def commit_on_settle(spin: Gtk.SpinButton, commit: Callable[[], None]) -> None:
    """Call `commit` when the user settles on a number: Enter, or focus leaving the spin.

    Arrow clicks and typing change the value without committing; a click on an arrow
    focuses the spin, so leaving it afterwards commits what the arrows reached.
    """

    def settle(*_: Any) -> None:
        spin.update()  # take typed text the spin has not parsed yet
        commit()

    spin.connect("activate", settle)
    focus = Gtk.EventControllerFocus()
    focus.connect("leave", settle)
    spin.add_controller(focus)


class GapField(Gtk.Box):
    """One number or four sides, committed through `on_commit` (see the module docstring).

    `value` is what the field holds: the original object while the user has committed
    nothing (`None` stays `None`, so a caller writes no key), else the last commit.
    """

    def __init__(
        self,
        value: int | Mapping[str, int] | None,
        *,
        on_commit: Callable[[GapValue], None] | None = None,
        lower: int = 0,
        upper: int = 500,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._value: int | Mapping[str, int] | GapValue | None = value
        self._on_commit = on_commit

        per_side = isinstance(value, Mapping)
        sides = _sides_of(value)
        self._last: GapValue = dict(sides) if per_side else sides["top"]

        def spin(number: int) -> Gtk.SpinButton:
            widget = Gtk.SpinButton(
                adjustment=Gtk.Adjustment(
                    lower=lower, upper=upper, step_increment=1, page_increment=10
                ),
                digits=0,
                numeric=True,
                valign=Gtk.Align.CENTER,
            )
            widget.set_value(number)
            commit_on_settle(widget, self._commit_entered)
            return widget

        self.all_sides = spin(sides["top"])
        self.sides = {side: spin(sides[side]) for side in SIDES}
        self.uniform_toggle = Gtk.CheckButton(label="Same on all sides", active=not per_side)

        uniform_box = Gtk.Box(spacing=12)
        uniform_box.append(caption("All sides", expand=True))
        uniform_box.append(self.all_sides)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6)
        for column, side in enumerate(SIDES):
            grid.attach(caption(side.capitalize()), column, 0, 1, 1)
            grid.attach(self.sides[side], column, 1, 1, 1)

        self._shape = Gtk.Stack()
        self._shape.add_named(uniform_box, "uniform")
        self._shape.add_named(grid, "sides")
        self._shape.set_visible_child_name("sides" if per_side else "uniform")

        self.append(self.uniform_toggle)
        self.append(self._shape)
        self.uniform_toggle.connect("toggled", self._on_toggled)

    @property
    def uniform(self) -> bool:
        return self.uniform_toggle.get_active()

    @property
    def value(self) -> int | Mapping[str, int] | None:
        return self._value

    def _entered(self) -> GapValue:
        """What the visible half of the control says."""
        if self.uniform:
            return int(self.all_sides.get_value())
        return {side: int(self.sides[side].get_value()) for side in SIDES}

    def _commit_entered(self) -> None:
        entered = self._entered()
        if entered != self._last:
            self._emit(entered)

    def _on_toggled(self, _toggle: Gtk.CheckButton) -> None:
        # Each shape opens on the half the user was just reading. Switching to uniform
        # flattens to the top side: they asked for one number, and that is the one shown
        # first. Switching to per-side spreads the one number to all four. Setting a
        # spin's value commits nothing: only Enter and focus-out do.
        if self.uniform:
            self.all_sides.set_value(self.sides["top"].get_value())
        else:
            for side in SIDES:
                self.sides[side].set_value(self.all_sides.get_value())
        self._shape.set_visible_child_name("uniform" if self.uniform else "sides")
        self._emit(self._entered())

    def _emit(self, value: GapValue) -> None:
        self._last = value
        self._value = value
        if self._on_commit is not None:
            self._on_commit(value)


def _sides_of(value: int | Mapping[str, int] | None) -> dict[str, int]:
    if isinstance(value, Mapping):
        return {side: _int(value.get(side, 0)) for side in SIDES}
    number = _int(value)
    return dict.fromkeys(SIDES, number)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def gap_row(
    title: str,
    field: GapField,
    *,
    subtitle: str | None = None,
    suffix: Gtk.Widget | None = None,
) -> Adw.PreferencesRow:
    """The row a `GapField` sits in: its title (and `suffix`, a trash button say) on one
    line, the help under it, the control below. A box in a plain `PreferencesRow`, because
    the field is too wide to be an `Adw.ActionRow` suffix."""
    heading = Gtk.Box(spacing=6)
    heading.append(Gtk.Label(label=title, xalign=0.0, hexpand=True))
    if suffix is not None:
        heading.append(suffix)
    box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=6,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
    )
    box.append(heading)
    if subtitle:
        box.append(
            Gtk.Label(
                label=subtitle, xalign=0.0, wrap=True, css_classes=["dim-label", "caption"]
            )
        )
    box.append(field)
    row = Adw.PreferencesRow(title=title, activatable=False)
    row.set_child(box)
    return row
