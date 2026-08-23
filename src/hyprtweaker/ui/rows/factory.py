"""One Row per Option, chosen by the Schema's resolved widget (ADR-0013).

353 Options, no per-option code: `resolve` has already decided what each one *is*, so this
module only has to know how to build each *kind*. That is the whole architecture prototype
#8 settled, and the reason a new Hyprland option needs an Overlay entry rather than a patch
here.

The four basic controls -- switch, spinner, combo, entry -- the four complex-value editors
-- colour, gradient, css-gaps, vec2 -- and the suffix strip every one of them wears
(`chrome.py`: state pills, Value summary, Dependency badge, reset, ⓘ Help popover). Font
weights are the one type still rendered read-only: the two Options that have one take either
a number or a preset name, and offering the names is Overlay curation (`labels`) rather than
a widget this module can invent. They render their value rather than being left out -- an
Option missing from its Page is one a user cannot find, and a blank control is the falsehood
prototype #8 measured (`[[EMPTY]]` rendering as an empty row).

Five conventions worth stating, four from ADR-0013 and one from ADR-0010:

* **A string Option is an `AdwActionRow` with a `GtkEntry` suffix**, never an `AdwEntryRow`.
  `AdwEntryRow` has no subtitle, and 24 description-less Rows is falsehood by omission.
* **The factory returns its control.** Dependency badges and the read-only state need to
  reach the inner widget; the ADR calls out walking the widget tree for it as the thing the
  prototype did and the real factory must not.
* **No control ever renders a sentinel as data.** `state.shown_value` answers "is there a
  value here at all?" for every control, so an Option with none reads "Device default"
  rather than `-1`, `[[EMPTY]]`, or the bottom of a spin button's range.
* **A complex value gets an `AdwExpanderRow`, and therefore a Value summary.** Its editor
  goes in as *one* child widget rather than a stack of sub-Rows, which is what lets the
  Row return a single control handle for the dependency badge to desensitise.
* **A continuous gesture previews, it does not write.** A slider drag is a stream of Eval
  previews and exactly one Apply transaction, on release (ADR-0010 §Eval preview). The
  sequencing is `gesture.Gesture`; this module only wires the widget's signals to it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from hyprtweaker.engine.model import (  # noqa: E402
    Color,
    CssGaps,
    Gradient,
    Vec2,
    display_text,
    parse_value,
)
from hyprtweaker.engine.schema import (  # noqa: E402
    OptionType,
    ResolvedOption,
    Widget,
    humanise,
)
from hyprtweaker.session import Session  # noqa: E402
from hyprtweaker.ui.rows.chrome import Navigate, RowChrome  # noqa: E402
from hyprtweaker.ui.rows.gesture import Gesture  # noqa: E402
from hyprtweaker.ui.rows.state import (  # noqa: E402
    NO_VALUE,
    no_value_label,
    shown_value,
)

_COMBO_WIDGETS = frozenset({Widget.ENUM_MAP, Widget.ENUM_STRING, Widget.SEGMENTED})

_SPIN_WIDGETS = frozenset(
    {Widget.INT_RANGE, Widget.FREE_INT, Widget.FLOAT_RANGE, Widget.FREE_FLOAT}
)

_ENTRY_WIDGETS = frozenset(
    {
        Widget.STRING,
        Widget.REGEX,
        Widget.FILE_PICKER,
        Widget.FONT_PICKER,
        Widget.MONITOR_PICKER,
        Widget.XKB_LAYOUT,
        Widget.XKB_VARIANT,
        Widget.XKB_OPTIONS,
        Widget.XKB_MODEL,
        Widget.FLOAT_LIST,
    }
)
"""Every widget whose value is a string the user types.

The pickers are in here because a picker is an entry until someone builds its chooser
(#57): the Overlay has already recorded *that* `input:kb_layout` deserves an XKB picker, and
rendering it as free text in the meantime keeps the Option editable and truthful. What it
must never do is silently become a different type."""

_FREE_BOUND = 10_000.0
"""The spinner's reach for an Option whose Schema gives no usable upper bound.

`descriptions` reports INT_MAX for the unbounded ints and nothing at all for one float, and
a spin button that walks to two billion is not a control. The Overlay answers this properly
per Option with `soft_max`, which always wins over this; the constant is what a still
uncurated Option falls back to rather than rendering unusable."""

_FLOAT_STEP = 0.01
"""Two decimals: every float Option in 0.56.2 is an opacity, a scale or a strength, and all
of them read in hundredths. A curated `range.step` overrides it."""

_GAP_BOUND = 500.0
"""How far a gap spinner reaches. No css-gaps Option carries a curated range, and none needs
one: a gap is a distance in pixels between windows, so the useful part of the scale is the
first fifty. The floor is zero rather than the type's -- the negative a css-gaps Option can
hold is `general:float_gaps`'s `-1`, which is its *null* spelling ("same as outer gaps") and
is reached by the reset arrow, not by walking a spinner past zero into a marker."""

_DEFAULT_STOP = Color(0xFFFFFFFF)
"""What a colour control shows when its Option has none yet -- opaque white.

Reached two ways, both of them a user asking for a value where there was none: clicking a
nullable colour's placeholder, and adding a stop to a gradient. White because it is visible
against both themes and unmistakably a starting point rather than a considered choice; the
colour dialog is one click away, and the reset arrow is one click back."""

_ANGLE_MAX = 360.0
_ANGLE_PAGE = 15.0
"""Angles are a full turn in degrees, paged in 15° steps -- the increments a gradient is
actually reasoned about in (45°, 90°), reachable with Page Up rather than 45 arrow presses."""


@dataclass(frozen=True, slots=True)
class OptionRow:
    """One built Row and the two handles the Page needs to keep it honest."""

    option: ResolvedOption
    widget: Adw.PreferencesRow
    control: Gtk.Widget
    """The inner control. Made insensitive on its own, so the title and subtitle of a Row
    the user cannot currently edit stay readable (ADR-0013 §3)."""

    refresh: Callable[[], None]
    """Re-read the model into the control, without echoing an edit back to the model."""

    chrome: RowChrome
    """The suffix strip: state pills, Value summary, Dependency badge, reset, ⓘ (ADR-0013)."""

    gesture: Gesture | None = None
    """The continuous gesture this Row's control drives, on the Rows that have one.

    Exposed because the Page has to be able to *abandon* it. A drag is the one interaction
    with state outside the model -- an Eval preview the compositor is showing and no file
    contains -- and a reload wipes that without telling anyone. The Page refreshing controls
    from a freshly re-read model while a gesture still thinks it is mid-drag is how the
    user's half-chosen value would get committed by somebody else's reload.
    """

    def abandon_gesture(self) -> None:
        """Drop any gesture in progress without writing it. Safe on every Row."""
        if self.gesture is not None:
            self.gesture.abandon()


class RowFactory:
    """Builds Rows against one `Session`, which owns the model they edit."""

    def __init__(
        self,
        session: Session,
        *,
        on_edited: Callable[[str], None] | None = None,
        navigate: Navigate | None = None,
    ) -> None:
        """`on_edited` and `navigate` are the two things a Row cannot do for itself.

        A control that writes to the model has just changed what *other* Rows show -- its
        own reset arrow, and the dependency badge of everything gated on it -- and only the
        window knows where those Rows are. Same for the badge's click: it names an Option,
        and turning a name into a visible Row is the window's job. Both default to doing
        nothing so a Row is still buildable in isolation, which the smoke tier relies on.
        """
        self._session = session
        self._on_edited = on_edited
        self._navigate = navigate
        self._echo_guard = False

    def build(self, option: ResolvedOption) -> OptionRow:
        """The Row for one Option. Never raises on an unfamiliar widget."""
        if option.widget is Widget.TOGGLE:
            row = self._toggle(option)
        elif option.widget in _SPIN_WIDGETS:
            row = self._spin(option)
        elif option.widget in _COMBO_WIDGETS:
            row = self._combo(option)
        elif option.widget in _ENTRY_WIDGETS:
            row = self._entry(option)
        elif option.widget is Widget.COLOR:
            row = self._color(option)
        elif option.widget is Widget.GRADIENT:
            row = self._gradient(option)
        elif option.widget is Widget.CSS_GAPS:
            row = self._css_gaps(option)
        elif option.widget is Widget.VEC2:
            row = self._vec2(option)
        else:
            row = self._read_only(option)

        # Only the values: the chrome decided itself when `_row` built it.
        row.refresh()
        return row

    # --- the shape every Row shares -----------------------------------------------------------

    def _row(
        self, option: ResolvedOption, control: Gtk.Widget
    ) -> tuple[Adw.ActionRow, RowChrome]:
        """An `AdwActionRow` carrying one Option's text, its control, and its suffix strip.

        Every widget type, including the ones libadwaita has a purpose-built Row for. Two
        reasons, both from ADR-0013:

        * **Only the control may go insensitive.** `AdwSwitchRow` and friends *are* their
          control, so dimming it takes the title and description with it -- the presentation
          the ADR rejects for dependency-disabled Rows.
        * **The suffix strip is fixed-order** -- pills, Value summary, Dependency badge,
          reset, ⓘ. A purpose-built Row puts its own control last and appends suffixes
          before it, which is the wrong end.

        The subtitle is the description, never the dotted key: users scan for what an option
        *does*, and the key is expert metadata that lives in the ⓘ popover (ADR-0013 §1,
        superseding the older `CONTEXT.md` wording).
        """
        row = Adw.ActionRow()
        self._text(row, option)
        row.add_suffix(control)
        if control.get_focusable():
            row.set_activatable_widget(control)
        return row, self._chrome(row, control, option)

    def _expander(
        self, option: ResolvedOption, editor: Gtk.Widget
    ) -> tuple[Adw.ExpanderRow, RowChrome]:
        """An `AdwExpanderRow` whose one child is the whole editor for a complex value.

        One child rather than a sub-Row per part, and the reason is the same ADR-0013 §3
        rule that shapes the ActionRow above: only the *control* may go insensitive. A stack
        of sub-Rows would leave the factory with nothing single to hand back as the control,
        and the dependency badge would be back to walking the widget tree for it -- named in
        the ADR as the thing the prototype did and the real factory must not.

        The collapsed Row is not silent about what it holds: the Value summary in the suffix
        strip answers "what is it set to?" from the same model the editor edits
        (`state.value_summary`, ADR-0013 §4).
        """
        row = Adw.ExpanderRow()
        self._text(row, option)
        row.add_row(editor)
        return row, self._chrome(row, editor, option)

    def _text(self, row: Adw.ActionRow | Adw.ExpanderRow, option: ResolvedOption) -> None:
        row.set_use_markup(False)
        # The unit belongs in the title: "`px` / `ms` / `deg` / `/s` in the title, so the
        # number means something" (prototype #8 FINDINGS, curation policy, 22 Options).
        row.set_title(f"{option.title} ({option.unit})" if option.unit else option.title)
        row.set_subtitle(option.description)

    def _chrome(
        self,
        row: Adw.ActionRow | Adw.ExpanderRow,
        control: Gtk.Widget,
        option: ResolvedOption,
    ) -> RowChrome:
        return RowChrome(
            row,
            control,
            option,
            self._session,
            on_reset=self._unset,
            navigate=self._navigate,
        )

    # --- every write to the model goes through these ------------------------------------------

    def _set(self, option: ResolvedOption, value: Any) -> None:
        self._session.set_option(option.name, value)
        self._edited(option.name)

    def _touch(self, option: ResolvedOption, value: Any) -> None:
        self._session.touch_option(option.name, value)
        self._edited(option.name)

    def _unset(self, name: str) -> None:
        """Back to Unset, so Hyprland's own default applies (ADR-0013 §6).

        Both ways of asking for that -- clearing a non-nullable entry, and the reset arrow
        -- land here, because they are the same gesture with different chrome.
        """
        self._session.unset_option(name)
        self._edited(name)

    def _preview(self, option: ResolvedOption, value: Any) -> None:
        """A tick of a continuous gesture: `eval` only, nothing written (ADR-0010).

        Still reports the edit. The model *has* moved -- the Row's own reset arrow and the
        Value summary in its suffix strip both turn on it -- and a summary that only caught
        up when the drag ended would be the collapsed Row lying for the length of the drag,
        which is the one thing it exists not to do.
        """
        self._session.preview_option(option.name, value)
        self._edited(option.name)

    def _gesture(self, option: ResolvedOption) -> Gesture:
        """The preview/commit pair for one Option's continuous control.

        Per Option rather than per widget: a gradient's editor is rebuilt as its value
        changes shape (a stop added, a stop removed) and the pointer may well still be down.
        """
        return Gesture(
            option.name,
            preview=lambda _name, value: self._preview(option, value),
            commit=lambda _name, value: self._set(option, value),
        )

    def _edited(self, name: str) -> None:
        if self._on_edited is not None:
            self._on_edited(name)

    # --- the four basic controls ------------------------------------------------------------

    def _toggle(self, option: ResolvedOption) -> OptionRow:
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row, chrome = self._row(option, switch)

        def refresh() -> None:
            with self._quiet():
                switch.set_active(bool(self._session.effective_value(option)))

        def changed(*_: Any) -> None:
            if not self._echo_guard:
                self._set(option, switch.get_active())

        switch.connect("notify::active", changed)
        return OptionRow(option, row, switch, refresh, chrome)

    def _spin(self, option: ResolvedOption) -> OptionRow:
        low, high = _bounds(option)
        integral = option.type is not OptionType.FLOAT
        step = 1.0 if integral else _step(option)

        spin = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                lower=low,
                upper=high,
                step_increment=step,
                page_increment=step * 10,
            ),
            digits=0 if integral else _digits(step),
            numeric=True,
            valign=Gtk.Align.CENTER,
        )
        control = self._numeric_control(option, spin, low, high)
        row, chrome = self._row(option, control)

        def refresh() -> None:
            value = shown_value(option, self._session.value_of(option))
            with self._quiet():
                spin.set_value(_as_number(value, _parked(low, high)))
                if isinstance(control, Gtk.Stack):
                    control.set_visible_child_name("none" if value is NO_VALUE else "value")

        def changed(*_: Any) -> None:
            if self._echo_guard:
                return
            value = spin.get_value()
            # Spinners fire per keystroke and per held arrow-key repeat, so this is the
            # mid-gesture verb: the queue coalesces the burst into one reload once the
            # user stops, which is the whole reason `touch` exists (ADR-0010).
            self._touch(option, int(value) if integral else value)

        spin.connect("notify::value", changed)
        return OptionRow(option, row, control, refresh, chrome)

    def _numeric_control(
        self, option: ResolvedOption, spin: Gtk.SpinButton, low: float, high: float
    ) -> Gtk.Widget:
        """The spin button, or -- for a nullable number -- a placeholder that swaps to it.

        A spin button has no way to show "there is no value here": it always renders one,
        and for the two pressure-range floats that number would be `-1`, which is the tablet
        driver's word for "use my own default" and not a pressure anyone chose (ADR-0013's
        honest-sentinel rule; prototype #8's most damaging defect class).

        `GtkSpinButton` can be talked into rendering arbitrary text through its `output`
        signal, but not into *parsing* it back: PyGObject cannot marshal the `input`
        signal's out-parameter, so every focus-out re-parses the placeholder as a number and
        writes it to the model -- verified against GTK 4 here rather than assumed. A stack
        with a button on the other page has no such seam, and it also answers the question
        the placeholder alone leaves open: how the user gets a number in the first place.
        `_placeholder_stack` is that stack, shared with every other nullable control; the
        parked value is the part only a number has an opinion about.
        """
        if not option.nullable:
            return spin
        return self._placeholder_stack(
            option, spin, on_set=lambda: self._set(option, _parked(low, high))
        )

    def _placeholder_stack(
        self, option: ResolvedOption, control: Gtk.Widget, *, on_set: Callable[[], None]
    ) -> Gtk.Stack:
        """`control`, behind a click-to-set placeholder while the Option has no value.

        The shape every nullable control shares, whatever its type: a number, a colour, a
        set of gaps. `on_set` is the one part that differs -- the value a click means -- and
        each caller answers it in its own terms.

        Clicking *writes*, and that is deliberate rather than incidental. Under instant apply
        there is no other kind of gesture (ADR-0003), and revealing the control without
        writing would be worse: the Row would show a value the config does not contain, and
        the next refresh would snap it back to the placeholder mid-edit. So the click means
        exactly what its tooltip says -- "click to set a value" -- and the reset arrow it
        raises is one click back to "Device default".
        """
        placeholder = Gtk.Button(
            label=no_value_label(option),
            css_classes=["flat", "dim-label"],
            valign=Gtk.Align.CENTER,
            tooltip_text=f"{no_value_label(option)} — click to set a value",
        )
        placeholder.connect("clicked", lambda _button: on_set())

        stack = Gtk.Stack(valign=Gtk.Align.CENTER)
        stack.add_named(placeholder, "none")
        stack.add_named(control, "value")
        return stack

    def _combo(self, option: ResolvedOption) -> OptionRow:
        choices = _choices(option)
        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _, label in choices]),
            valign=Gtk.Align.CENTER,
        )
        row, chrome = self._row(option, dropdown)

        def refresh() -> None:
            value = shown_value(option, self._session.value_of(option))
            with self._quiet():
                dropdown.set_selected(_index_of(choices, None if value is NO_VALUE else value))

        def changed(*_: Any) -> None:
            if self._echo_guard:
                return
            index = dropdown.get_selected()
            if index >= len(choices):
                return
            self._set(option, choices[index][0])

        dropdown.connect("notify::selected", changed)
        return OptionRow(option, row, dropdown, refresh, chrome)

    def _entry(self, option: ResolvedOption) -> OptionRow:
        entry = Gtk.Entry(
            valign=Gtk.Align.CENTER,
            width_chars=18,
            # A nullable string is never blank: the placeholder is the curated `null_label`
            # ("Device default", "None"), so an unset Option says what it falls back to
            # instead of showing an empty field (ADR-0013 §2). This is the basic entry Row's
            # own convention, not #57's honest-sentinel work -- the alternative is not "no
            # placeholder" but a field showing `[[EMPTY]]`, which is the falsehood prototype
            # #8 measured as its most damaging defect class. Through `no_value_label` rather
            # than off `null_label` directly, so a Row that lost its curation reads "Not
            # set" here exactly as it does everywhere else, instead of going blank again.
            placeholder_text=no_value_label(option) if option.nullable else "",
        )
        row, chrome = self._row(option, entry)

        def shown_text() -> str:
            value = shown_value(option, self._session.value_of(option))
            return "" if value is NO_VALUE else display_text(value)

        def refresh() -> None:
            with self._quiet():
                entry.set_text(shown_text())

        def commit(*_: Any) -> None:
            text = entry.get_text()
            # Focus-out fires on every page switch and on window close, so an unchanged
            # entry must be silent -- otherwise merely looking at a Page reloads the
            # compositor once per string Option on it.
            if self._echo_guard or text == shown_text():
                return
            if text:
                self._set(option, text)
            elif option.nullable:
                # Cleared, and this Option has a curated spelling for "no value".
                self._set(option, None)
            else:
                self._unset(option.name)

        # Enter and losing focus, the two moments a typed value is decided. Committing per
        # keystroke would reload the compositor once per character.
        entry.connect("activate", commit)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", commit)
        entry.add_controller(focus)

        return OptionRow(option, row, entry, refresh, chrome)

    # --- the four complex-value editors -----------------------------------------------------

    def _color(self, option: ResolvedOption) -> OptionRow:
        """A colour button. The one control that *is* its own preview, so no summary.

        `GtkColorDialogButton` over the deprecated `GtkColorButton`, and with alpha on:
        every Hyprland colour is a 32-bit ARGB word and an editor that dropped the alpha
        channel would silently make half the config opaque.

        The dialog is modal, so a colour is one decided gesture and one Apply transaction --
        there is no per-tick drag to preview. The continuous half of ADR-0010's colour work
        lives on the gradient's angle slider, which is the only colour control in the app
        that moves under a held pointer.
        """
        button = Gtk.ColorDialogButton(
            dialog=Gtk.ColorDialog(with_alpha=True, modal=True),
            rgba=_rgba(_DEFAULT_STOP),
            valign=Gtk.Align.CENTER,
        )
        control = (
            self._placeholder_stack(
                option, button, on_set=lambda: self._set(option, _color_of(button))
            )
            if option.nullable
            else button
        )
        row, chrome = self._row(option, control)

        def refresh() -> None:
            value = shown_value(option, self._session.value_of(option))
            with self._quiet():
                if value is not NO_VALUE:
                    button.set_rgba(_rgba(_as_color(value)))
                if isinstance(control, Gtk.Stack):
                    control.set_visible_child_name("none" if value is NO_VALUE else "value")

        def changed(*_: Any) -> None:
            if not self._echo_guard:
                self._set(option, _color_of(button))

        button.connect("notify::rgba", changed)
        return OptionRow(option, row, control, refresh, chrome)

    def _gradient(self, option: ResolvedOption) -> OptionRow:
        """Colour stops plus an angle -- and the app's one genuinely continuous control.

        The angle is a `GtkScale`, so dragging it is ADR-0010's Eval preview tier in full:
        every tick previews over the socket, and the release lands one Apply transaction.
        The stops are colour dialogs, which are decided gestures and commit directly.

        The stop row is rebuilt whenever the gradient changes *shape* rather than patched in
        place. A stop is three widgets (its button, its remove arrow, the trailing add), and
        keeping index-bound closures agreeing with a list the user is adding to and removing
        from is a class of bug this simply does not have.
        """
        stops = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        angle = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(
                lower=0.0, upper=_ANGLE_MAX, step_increment=1.0, page_increment=_ANGLE_PAGE
            ),
            digits=0,
            draw_value=True,
            hexpand=True,
        )
        editor = _editor_box()
        editor.append(_field("Colours", stops))
        editor.append(_field("Angle (°)", angle, expand=True))

        row, chrome = self._expander(option, editor)
        gesture = self._gesture(option)

        def current() -> Gradient:
            value = self._typed(option, _DEFAULT_GRADIENT)
            return value if isinstance(value, Gradient) else _DEFAULT_GRADIENT

        def write(gradient: Gradient) -> None:
            self._set(option, gradient)
            refresh()

        def stop_changed(index: int, button: Gtk.ColorDialogButton) -> None:
            if self._echo_guard:
                return
            gradient = current()
            if index >= len(gradient.colors):
                return
            colors = list(gradient.colors)
            colors[index] = _color_of(button)
            self._set(option, replace(gradient, colors=tuple(colors)))

        def add_stop() -> None:
            gradient = current()
            write(replace(gradient, colors=(*gradient.colors, gradient.colors[-1])))

        def remove_stop(index: int) -> None:
            gradient = current()
            # `Gradient` refuses to hold none, and so does Hyprland's parser. The arrow is
            # only built while there is more than one, so this is belt and braces.
            if len(gradient.colors) <= 1:
                return
            colors = [*gradient.colors[:index], *gradient.colors[index + 1 :]]
            write(replace(gradient, colors=tuple(colors)))

        def rebuild(gradient: Gradient) -> None:
            while (child := stops.get_first_child()) is not None:
                stops.remove(child)
            removable = len(gradient.colors) > 1
            for index, color in enumerate(gradient.colors):
                # `rgba` in the constructor, not `set_rgba` after it: a property set at
                # construction emits no `notify`, so a freshly built button cannot echo the
                # value it was built from straight back into the model.
                button = Gtk.ColorDialogButton(
                    dialog=Gtk.ColorDialog(with_alpha=True, modal=True),
                    rgba=_rgba(color),
                    valign=Gtk.Align.CENTER,
                    tooltip_text=f"Colour {index + 1}",
                )
                button.connect(
                    "notify::rgba", lambda widget, _p, at=index: stop_changed(at, widget)
                )
                stops.append(button)
                if removable:
                    stops.append(
                        _icon_button(
                            "list-remove-symbolic",
                            f"Remove colour {index + 1}",
                            lambda _b, at=index: remove_stop(at),
                        )
                    )
            stops.append(
                _icon_button("list-add-symbolic", "Add a colour", lambda _b: add_stop())
            )

        def refresh() -> None:
            gradient = current()
            with self._quiet():
                angle.set_value(gradient.angle)
                rebuild(gradient)

        def angle_changed(*_: Any) -> None:
            if self._echo_guard:
                return
            gesture.tick(replace(current(), angle=angle.get_value()))

        angle.connect("value-changed", angle_changed)
        _ends_gesture(angle, gesture)
        return OptionRow(option, row, editor, refresh, chrome, gesture)

    def _css_gaps(self, option: ResolvedOption) -> OptionRow:
        """Four gap sides, entered as one number or as four (ADR-0013's "uniform or per-side").

        The uniform switch is the whole point of the control. Every rice in `tests/corpus`
        writes `gaps_in = 5`, so the common case has to be one number -- and the four-side
        form has to be there because Hyprland's type is four sides and hiding that would
        make the per-side configs the app imports uneditable.

        Switching *to* uniform flattens to the top side rather than refusing: the user asked
        for one number, and the alternative is a switch that silently does nothing whenever
        the sides currently differ, which is exactly when it was reached for.

        The two shapes get their own spinners rather than sharing the top one. A widget has
        exactly one parent, so a shared spinner cannot be in both pages of the stack -- and
        keeping them separate makes the sync on toggle explicit rather than incidental.
        """
        all_sides = _gap_spin()
        spins = {side: _gap_spin() for side in _SIDES}
        uniform = Gtk.CheckButton(label="Same on all sides")

        sides = Gtk.Grid(column_spacing=12, row_spacing=6)
        for column, side in enumerate(_SIDES):
            sides.attach(_caption(side.capitalize()), column, 0, 1, 1)
            sides.attach(spins[side], column, 1, 1, 1)

        shape = Gtk.Stack()
        shape.add_named(_field("All sides", all_sides), "uniform")
        shape.add_named(sides, "sides")

        editor = _editor_box()
        editor.append(uniform)
        editor.append(shape)
        control = (
            self._placeholder_stack(
                option, editor, on_set=lambda: self._set(option, CssGaps.uniform(0))
            )
            if option.nullable
            else editor
        )

        row, chrome = self._expander(option, control)

        def current() -> CssGaps:
            value = self._typed(option, _DEFAULT_GAPS)
            return value if isinstance(value, CssGaps) else _DEFAULT_GAPS

        def entered() -> CssGaps:
            """What the visible half of the editor currently says."""
            if uniform.get_active():
                return CssGaps.uniform(int(all_sides.get_value()))
            return CssGaps(*(int(spins[side].get_value()) for side in _SIDES))

        def show(gaps: CssGaps) -> None:
            """Fill both halves, so a toggle reveals a shape that already agrees."""
            all_sides.set_value(gaps.top)
            for side in _SIDES:
                spins[side].set_value(getattr(gaps, side))
            shape.set_visible_child_name("uniform" if uniform.get_active() else "sides")

        def edited(*_: Any) -> None:
            if self._echo_guard:
                return
            # Spin buttons fire per keystroke and per held arrow-key repeat, so this is the
            # mid-gesture verb: the queue coalesces the burst into one reload once the user
            # stops. A spinner is a discrete control, so it previews nothing (ADR-0010).
            self._touch(option, entered())

        def shape_toggled(*_: Any) -> None:
            if self._echo_guard:
                shape.set_visible_child_name("uniform" if uniform.get_active() else "sides")
                return
            # Each shape takes its opening value from the *other* one, which is the half the
            # user was looking at a moment ago. Switching to uniform flattens to the top
            # side: they asked for one number, and that is the number they were reading.
            source = spins["top"] if uniform.get_active() else all_sides
            gaps = CssGaps.uniform(int(source.get_value()))
            with self._quiet():
                show(gaps)
            self._touch(option, gaps)

        def refresh() -> None:
            gaps = current()
            with self._quiet():
                uniform.set_active(gaps.top == gaps.right == gaps.bottom == gaps.left)
                show(gaps)
                if isinstance(control, Gtk.Stack):
                    live = shown_value(option, self._session.value_of(option))
                    control.set_visible_child_name("none" if live is NO_VALUE else "value")

        uniform.connect("toggled", shape_toggled)
        all_sides.connect("notify::value", edited)
        for spin in spins.values():
            spin.connect("notify::value", edited)
        return OptionRow(option, row, control, refresh, chrome)

    def _vec2(self, option: ResolvedOption) -> OptionRow:
        """Two axes. Curated bounds per axis, because `vec2Range` gives each its own."""
        bounds = option.vec2_range
        x_spin = _axis_spin(
            bounds.min_x if bounds else -_FREE_BOUND, bounds.max_x if bounds else _FREE_BOUND
        )
        y_spin = _axis_spin(
            bounds.min_y if bounds else -_FREE_BOUND, bounds.max_y if bounds else _FREE_BOUND
        )

        editor = _editor_box()
        editor.append(_field("X", x_spin))
        editor.append(_field("Y", y_spin))
        row, chrome = self._expander(option, editor)

        def refresh() -> None:
            value = self._typed(option, _DEFAULT_VEC2)
            vector = value if isinstance(value, Vec2) else _DEFAULT_VEC2
            with self._quiet():
                x_spin.set_value(vector.x)
                y_spin.set_value(vector.y)

        def edited(*_: Any) -> None:
            if not self._echo_guard:
                self._touch(option, Vec2(x_spin.get_value(), y_spin.get_value()))

        x_spin.connect("notify::value", edited)
        y_spin.connect("notify::value", edited)
        return OptionRow(option, row, editor, refresh, chrome)

    def _typed(self, option: ResolvedOption, fallback: Any) -> Any:
        """This Option's current value as its own class, or `fallback` when it has none.

        Two things stand between the model and a typed value, and neither is an error. An
        Option with no value at all (`NO_VALUE`) is the nullable case, where `fallback` is
        the value its editor opens on rather than something the config contains. And a
        schema default arrives as the display text `descriptions` printed (`"ff444444
        0deg"`), never as a `Gradient` -- so every editor parses, and a spelling this
        Hyprland version no longer uses falls back rather than taking the Page down with it.
        """
        value = shown_value(option, self._session.value_of(option))
        if value is NO_VALUE:
            return fallback
        try:
            return parse_value(option.type, value)
        except (ValueError, TypeError):
            return fallback

    # --- what has no editor yet ---------------------------------------------------------------

    def _read_only(self, option: ResolvedOption) -> OptionRow:
        """Font weights, shown but not editable.

        The two Options that have one take either a number (`400`) or a preset name
        (`"bold"`), and a control that offered both would be inventing the preset list --
        which is Overlay curation (`labels`), not a widget. Shown as their display text so
        the Page still answers "what is this set to?", which is the question an omitted Row
        cannot answer at all.
        """
        label = Gtk.Label(valign=Gtk.Align.CENTER, css_classes=["dim-label"], selectable=True)
        row, chrome = self._row(option, label)

        def refresh() -> None:
            value = shown_value(option, self._session.value_of(option))
            if value is NO_VALUE:
                label.set_text(no_value_label(option))
            else:
                label.set_text(display_text(value))

        return OptionRow(option, row, label, refresh, chrome)

    # --- echo suppression -------------------------------------------------------------------

    @contextmanager
    def _quiet(self) -> Iterator[None]:
        """Suppress the change signals a programmatic write to a control emits.

        Without it every refresh looks exactly like a user edit: `set_active` fires
        `notify::active`, which would write the value straight back into the model and, on
        the re-read that follows a foreign reload, apply a config the user never touched.

        One flag for the whole factory, not one per Row: GTK is single-threaded and a
        refresh runs to completion before any other signal is dispatched, so there is never
        a second Row mid-refresh to confuse it with.
        """
        self._echo_guard = True
        try:
            yield
        finally:
            self._echo_guard = False


# --- the complex editors' widgets -------------------------------------------------------------

_SIDES = ("top", "right", "bottom", "left")
"""CSS order, and Hyprland's: `LuaConfigCssGap.cpp` reads a four-element table as TRBL, and
laying the spinners out in any other order would make the Row disagree with every `.conf`
and every wiki page the user has read."""

_DEFAULT_GRADIENT = Gradient((_DEFAULT_STOP,))
_DEFAULT_GAPS = CssGaps.uniform(0)
_DEFAULT_VEC2 = Vec2(0.0, 0.0)
"""What a complex editor opens on when its Option has no value to show. Never written by
being displayed -- an editor showing one of these is a Row whose Option is Unset, and it
stays Unset until the user moves something."""


def _editor_box() -> Gtk.Box:
    """The expander's one child: everything the editor is, in one insensitivity handle."""
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
    )


def _field(label: str, control: Gtk.Widget, *, expand: bool = False) -> Gtk.Box:
    """One labelled line inside an editor. Plain widgets, not sub-Rows -- see `_expander`.

    The slack goes to the label unless the control asks for it: a slider wants every pixel
    of width it can get, while a spin button stretched across 700 px is a text field with
    two tiny arrows a long way from the number (seen on the running app).
    """
    caption = _caption(label)
    caption.set_hexpand(not expand)
    control.set_hexpand(expand)
    control.set_halign(Gtk.Align.FILL if expand else Gtk.Align.END)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    box.append(caption)
    box.append(control)
    return box


def _caption(text: str) -> Gtk.Label:
    return Gtk.Label(label=text, xalign=0.0, css_classes=["caption", "dim-label"])


def _icon_button(icon: str, tooltip: str, clicked: Callable[..., None]) -> Gtk.Button:
    button = Gtk.Button(
        icon_name=icon, css_classes=["flat"], valign=Gtk.Align.CENTER, tooltip_text=tooltip
    )
    button.connect("clicked", clicked)
    return button


def _gap_spin() -> Gtk.SpinButton:
    return Gtk.SpinButton(
        adjustment=Gtk.Adjustment(
            lower=0.0, upper=_GAP_BOUND, step_increment=1.0, page_increment=10.0
        ),
        digits=0,
        numeric=True,
        valign=Gtk.Align.CENTER,
    )


def _axis_spin(low: float, high: float) -> Gtk.SpinButton:
    return Gtk.SpinButton(
        adjustment=Gtk.Adjustment(
            lower=low, upper=high, step_increment=1.0, page_increment=10.0
        ),
        digits=_digits(_FLOAT_STEP),
        numeric=True,
        valign=Gtk.Align.CENTER,
    )


def _ends_gesture(widget: Gtk.Widget, gesture: Gesture) -> None:
    """Wire every way a continuous gesture can finish to `Gesture.end`.

    Three controllers, because a `GtkScale` can be left in as many ways and a gesture that
    is never ended is a value previewed and never written:

    * **pointer.** `GtkGesture::end` rather than `::released`, and in the capture phase: the
      scale claims the sequence for its own drag handling, which cancels a bubble-phase
      click gesture instead of releasing it. `end` fires either way.
    * **keyboard.** Arrow keys move the scale too, and a key-release is that drag's release.
    * **focus.** The net under both: tabbing away, switching Section, closing the window.
      Without it a gesture interrupted by anything unusual would hold its value forever.

    `Gesture.end` is idempotent and no-ops without a tick, so the overlap between the three
    costs nothing -- which is exactly why it can afford to be belt, braces and a third one.
    """
    click = Gtk.GestureClick()
    click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    click.connect("end", lambda *_: gesture.end())
    widget.add_controller(click)

    keys = Gtk.EventControllerKey()
    keys.connect("key-released", lambda *_: gesture.end())
    widget.add_controller(keys)

    focus = Gtk.EventControllerFocus()
    focus.connect("leave", lambda *_: gesture.end())
    widget.add_controller(focus)


def _rgba(color: Color) -> Gdk.RGBA:
    """A model colour as GTK's. Through `#rrggbbaa` -- alpha last, as everywhere but ARGB."""
    rgba = Gdk.RGBA()
    rgba.parse(f"#{color.rgba:08x}")
    return rgba


def _color_of(button: Gtk.ColorDialogButton) -> Color:
    """GTK's colour as the model's packed ARGB word."""
    rgba = button.get_rgba()
    return Color(
        (_byte(rgba.alpha) << 24)
        | (_byte(rgba.red) << 16)
        | (_byte(rgba.green) << 8)
        | _byte(rgba.blue)
    )


def _byte(channel: float) -> int:
    return max(0, min(255, round(channel * 255)))


def _as_color(value: Any) -> Color:
    """A model value as a `Color`, in whatever spelling it came (a schema default is text)."""
    try:
        return Color.parse(value)
    except (ValueError, TypeError):
        return _DEFAULT_STOP


# --- Schema -> control parameters -----------------------------------------------------------


def _bounds(option: ResolvedOption) -> tuple[float, float]:
    """The spinner's reach: curated bounds where they exist, a usable fallback where not."""
    bounds = option.range
    low = bounds.min if bounds is not None and bounds.min is not None else -_FREE_BOUND
    high = bounds.soft_max if bounds is not None and bounds.soft_max is not None else None
    if high is None:
        high = bounds.max if bounds is not None and bounds.max is not None else None
    if high is None or high > _FREE_BOUND:
        high = _FREE_BOUND
    return float(low), float(max(high, low))


def _step(option: ResolvedOption) -> float:
    if option.range is not None and option.range.step:
        return float(option.range.step)
    return _FLOAT_STEP


def _digits(step: float) -> int:
    """Enough decimals to show the step. `0.01` needs two; `0.5` needs one."""
    text = f"{step:.10f}".rstrip("0")
    return max(0, min(6, len(text.partition(".")[2])))


def _as_number(value: Any, fallback: float) -> float:
    """A model value as a spinner value. Anything unnumeric falls back to `fallback`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return fallback
    return float(value)


def _parked(low: float, high: float) -> float:
    """Where a nullable spinner sits while its Option has no value.

    Zero wherever the range allows it, because the sentinel these Options carry is `-1` and
    parking *on* the sentinel would make the first click of the up-arrow the only way out of
    a value that means "no value". Clamped, so an Option whose range excludes zero still
    lands inside it.
    """
    return min(max(0.0, low), high)


def _choices(option: ResolvedOption) -> tuple[tuple[Any, str], ...]:
    """Every selectable `(value, label)` for a combo, in the order it should be offered.

    Three sources, because the Schema has three shapes for "this is really an enum":
    curated `labels` (a map-less int, or prettier text for a mapped one), the generated
    `map` from `descriptions`, and `known_values` for the string enums. A nullable Option
    leads with its `null_label`, so "Device default" is a choice rather than a blank.
    """
    entries: list[tuple[Any, str]] = []
    if option.nullable:
        entries.append((None, option.null_label or "Default"))

    if option.labels:
        entries.extend((_typed(option, key), label) for key, label in option.labels.items())
    elif option.map:
        entries.extend((value, humanise(name)) for name, value in option.map.items())
    elif option.known_values:
        entries.extend((value, humanise(value)) for value in option.known_values.values)

    return tuple(entries)


def _typed(option: ResolvedOption, key: str) -> Any:
    """A `labels` key as the value it stands for.

    The Overlay stores keys as strings whatever the Option holds -- JSON has no integer
    keys -- so `'2'` on an int Option is the number two, and `'flat'` on a string Option is
    the word. Handing the wrong one to the model would store the digit as text.
    """
    if option.type is OptionType.STRING:
        return key
    try:
        return int(key)
    except ValueError:
        return key


def _index_of(choices: tuple[tuple[Any, str], ...], value: Any) -> int:
    """Which choice a model value selects. Unknown values select nothing rather than lying.

    An open `known_values` list (`general:layout` accepts `lua:<name>` for any registered
    layout) can hold a value no choice offers. Selecting the first entry would quietly
    report the wrong layout; `Gtk.INVALID_LIST_POSITION` shows the combo as unset, which is
    the truth until #76 puts discovered layouts in the list.
    """
    for index, (candidate, _) in enumerate(choices):
        if candidate == value and isinstance(candidate, bool) == isinstance(value, bool):
            return index
    return Gtk.INVALID_LIST_POSITION
