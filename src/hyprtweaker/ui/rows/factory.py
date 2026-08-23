"""One Row per Option, chosen by the Schema's resolved widget (ADR-0013).

353 Options, no per-option code: `resolve` has already decided what each one *is*, so this
module only has to know how to build each *kind*. That is the whole architecture prototype
#8 settled, and the reason a new Hyprland option needs an Overlay entry rather than a patch
here.

This ticket builds the four basic controls -- switch, spinner, combo, entry. The rest of the
Row contract (state pills, Value summary, Dependency badge, reset button, ⓘ Help popover) is
#57, and the complex-value editors -- gradient, colour, vec2, css-gaps -- are #58. Until
then those Options render their value read-only rather than being left out: an Option
missing from its Page is one a user cannot find, and a blank control is the falsehood
prototype #8 measured (`[[EMPTY]]` rendering as an empty row).

Two conventions worth stating, both from ADR-0013:

* **A string Option is an `AdwActionRow` with a `GtkEntry` suffix**, never an `AdwEntryRow`.
  `AdwEntryRow` has no subtitle, and 24 description-less Rows is falsehood by omission.
* **The factory returns its control.** Dependency badges and the read-only state need to
  reach the inner widget; the ADR calls out walking the widget tree for it as the thing the
  prototype did and the real factory must not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model import UNSET, display_text  # noqa: E402
from hyprtweaker.engine.schema import (  # noqa: E402
    OptionType,
    ResolvedOption,
    Widget,
    humanise,
)
from hyprtweaker.session import Session  # noqa: E402

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


class RowFactory:
    """Builds Rows against one `Session`, which owns the model they edit."""

    def __init__(self, session: Session) -> None:
        self._session = session
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
        else:
            row = self._read_only(option)

        row.refresh()
        row.control.set_sensitive(self._session.live)
        return row

    # --- the shape every Row shares -----------------------------------------------------------

    def _row(self, option: ResolvedOption, control: Gtk.Widget) -> Adw.ActionRow:
        """An `AdwActionRow` carrying one Option's text, with its control as a suffix.

        Every widget type, including the ones libadwaita has a purpose-built Row for. Two
        reasons, both from ADR-0013:

        * **Only the control may go insensitive.** `AdwSwitchRow` and friends *are* their
          control, so dimming it takes the title and description with it -- the presentation
          the ADR rejects for dependency-disabled Rows, and it would arrive with #57 having
          to undo this decision.
        * **The suffix strip is fixed-order** -- pills, Value summary, Dependency badge,
          reset, ⓘ. A purpose-built Row puts its own control last and appends suffixes
          before it, which is the wrong end.
        """
        row = Adw.ActionRow()
        row.set_use_markup(False)
        # The unit belongs in the title: "`px` / `ms` / `deg` / `/s` in the title, so the
        # number means something" (prototype #8 FINDINGS, curation policy, 22 Options).
        row.set_title(f"{option.title} ({option.unit})" if option.unit else option.title)
        row.set_subtitle(option.description)
        row.add_suffix(control)
        if control.get_focusable():
            row.set_activatable_widget(control)
        return row

    # --- the four basic controls ------------------------------------------------------------

    def _toggle(self, option: ResolvedOption) -> OptionRow:
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        row = self._row(option, switch)

        def refresh() -> None:
            with self._quiet():
                switch.set_active(bool(self._session.effective_value(option)))

        def changed(*_: Any) -> None:
            if not self._echo_guard:
                self._session.set_option(option.name, switch.get_active())

        switch.connect("notify::active", changed)
        return OptionRow(option, row, switch, refresh)

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
        row = self._row(option, spin)

        def refresh() -> None:
            with self._quiet():
                spin.set_value(_as_number(self._session.effective_value(option), low))

        def changed(*_: Any) -> None:
            if self._echo_guard:
                return
            value = spin.get_value()
            # Spinners fire per keystroke and per held arrow-key repeat, so this is the
            # mid-gesture verb: the queue coalesces the burst into one reload once the
            # user stops, which is the whole reason `touch` exists (ADR-0010).
            self._session.touch_option(option.name, int(value) if integral else value)

        spin.connect("notify::value", changed)
        return OptionRow(option, row, spin, refresh)

    def _combo(self, option: ResolvedOption) -> OptionRow:
        choices = _choices(option)
        dropdown = Gtk.DropDown(
            model=Gtk.StringList.new([label for _, label in choices]),
            valign=Gtk.Align.CENTER,
        )
        row = self._row(option, dropdown)

        def refresh() -> None:
            with self._quiet():
                dropdown.set_selected(_index_of(choices, self._session.effective_value(option)))

        def changed(*_: Any) -> None:
            if self._echo_guard:
                return
            index = dropdown.get_selected()
            if index >= len(choices):
                return
            self._session.set_option(option.name, choices[index][0])

        dropdown.connect("notify::selected", changed)
        return OptionRow(option, row, dropdown, refresh)

    def _entry(self, option: ResolvedOption) -> OptionRow:
        entry = Gtk.Entry(
            valign=Gtk.Align.CENTER,
            width_chars=18,
            # A nullable string is never blank: the placeholder is the curated `null_label`
            # ("Device default", "None"), so an unset Option says what it falls back to
            # instead of showing an empty field (ADR-0013 §2). This is the basic entry Row's
            # own convention, not #57's honest-sentinel work -- the alternative is not "no
            # placeholder" but a field showing `[[EMPTY]]`, which is the falsehood prototype
            # #8 measured as its most damaging defect class.
            placeholder_text=option.null_label or "",
        )
        row = self._row(option, entry)

        def shown_text() -> str:
            value = self._session.value_of(option)
            return "" if value is None or value is UNSET else display_text(value)

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
                self._session.set_option(option.name, text)
            elif option.nullable:
                # Cleared, and this Option has a curated spelling for "no value".
                self._session.set_option(option.name, None)
            else:
                self._session.unset_option(option.name)

        # Enter and losing focus, the two moments a typed value is decided. Committing per
        # keystroke would reload the compositor once per character.
        entry.connect("activate", commit)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", commit)
        entry.add_controller(focus)

        return OptionRow(option, row, entry, refresh)

    # --- everything #58 has not built an editor for yet -------------------------------------

    def _read_only(self, option: ResolvedOption) -> OptionRow:
        """Gradients, colours, vec2s, css-gaps and font weights, shown but not editable.

        Their editors are #58. Shown as their display text so the Page still answers "what
        is this set to?", which is the question the ADR-0013 Value summary exists for and
        the one an omitted Row cannot answer at all.
        """
        label = Gtk.Label(valign=Gtk.Align.CENTER, css_classes=["dim-label"], selectable=True)
        row = self._row(option, label)

        def refresh() -> None:
            value = self._session.effective_value(option)
            if value is None:
                label.set_text(option.null_label or "")
            else:
                label.set_text(display_text(value))

        return OptionRow(option, row, label, refresh)

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
    """A model value as a spinner value. Anything unnumeric falls back to the lower bound."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return fallback
    return float(value)


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
