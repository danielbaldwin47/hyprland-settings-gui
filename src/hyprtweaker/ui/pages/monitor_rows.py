"""The connected display's ADR-0008 rows that hold state of their own (#193).

Resolution and Refresh rate repopulate each other, Scale reveals a free value, the
luminance rows switch between "Not set" and a number, and the Reserved area and Advanced
colour rows are each several controls under one title. The Monitors Page builds one set per
connected display and hands every one the same `apply(fields)`, which routes each edit to
its lane by `DISPLAY_BREAKING_FIELDS` and does nothing while the Page is building.

Every row writes one field per committed gesture -- a choice, Enter, focus leaving a spin
-- never per keystroke: the Page rebuilds after each applied edit, and a breaking edit
opens the Confirm-or-revert countdown. "Not set" writes `UNSET`, which removes the key
from the rule (the Session's `patch_monitor_rule`), so the file never gains a default the
user did not choose.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from hyprtweaker.engine.model import UNSET  # noqa: E402
from hyprtweaker.engine.monitors_catalog import (  # noqa: E402
    CM_PRESETS,
    MODELINE_PREFIX,
    SDR_EOTF_NAMES,
    SPECIAL_MODES,
    format_mode,
    mode_rates,
    mode_sizes,
    parse_mode,
    sdr_eotf_name,
)
from hyprtweaker.ui.rows.gap_field import GapField, commit_on_settle, gap_row  # noqa: E402

Apply = Callable[[Mapping[str, Any]], None]
"""One edit to the display's rule: `{field: value}`, `UNSET` meaning "remove this key"."""

NOT_SET = "Not set"

FRACTIONAL_WARNING = "Fractional scales can look blurry in apps that don't support them."

_CUSTOM_MODELINE = "Custom modeline"
_MODE_LABELS: dict[str, str] = {
    "preferred": "Display's preferred",
    "highres": "Highest resolution",
    "highrr": "Highest refresh rate",
    "maxwidth": "Widest resolution",
}
"""What Resolution shows for each of Hyprland's mode words; the word is what is saved."""
_CUSTOM_SCALE = "Custom"
_SCALE_PRESETS: tuple[str, ...] = ("auto", "1", "1.25", "1.5", "2")

Size = tuple[int, int]


# --- Resolution and Refresh rate --------------------------------------------------------


class ModeRows:
    """Resolution (sizes, the special modes, a custom modeline) and Refresh rate.

    Both write the one `mode` field. Choosing a size writes it at its highest rate and
    repopulates Refresh rate with that size's rates; a special mode or a modeline leaves
    the rate to the mode, so Refresh rate goes insensitive.
    """

    def __init__(
        self,
        monitor: Mapping[str, Any],
        fields: Mapping[str, Any],
        apply: Apply,
        *,
        editable: bool,
    ) -> None:
        self._apply = apply
        self._editable = editable
        self._available = [str(mode) for mode in monitor.get("availableModes", ())]
        self._quiet = False

        live: Size = (int(monitor.get("width", 0)), int(monitor.get("height", 0)))
        rule_mode = fields.get("mode")
        rule_text = rule_mode.strip() if isinstance(rule_mode, str) else ""
        parsed = parse_mode(rule_text)
        shown: str | Size
        wanted: float | None = None
        specified = True  # False only for a size the rule names without a rate
        if rule_text in SPECIAL_MODES:
            shown = rule_text
        elif rule_text.startswith(MODELINE_PREFIX):
            shown = _CUSTOM_MODELINE
        elif parsed is not None:
            shown, wanted = parsed[:2], parsed[2]
            specified = wanted is not None
        else:  # no mode in the rule: show what the display runs now
            shown = live
            wanted = float(monitor.get("refreshRate", 0.0)) or None

        sizes = mode_sizes(self._available)
        if isinstance(shown, tuple) and shown not in sizes:
            sizes.append(shown)
        self._entries: list[str | Size] = [*SPECIAL_MODES, *sizes, _CUSTOM_MODELINE]

        self.resolution = Adw.ComboRow(
            title="Resolution",
            subtitle="What this display is asked to run, not merely what it runs now.",
            model=Gtk.StringList.new([_size_label(entry) for entry in self._entries]),
        )
        self.resolution.set_selected(self._entries.index(shown))
        self.resolution.set_sensitive(editable)

        self.refresh = Adw.ComboRow(title="Refresh rate")
        self._rates: list[float | None] = []

        self.modeline = Adw.EntryRow(title="Modeline", show_apply_button=True)
        self.modeline.set_tooltip_text(
            "clock hdisplay hsync_start hsync_end htotal "
            "vdisplay vsync_start vsync_end vtotal [flags]"
        )
        if shown == _CUSTOM_MODELINE:
            self.modeline.set_text(rule_text[len(MODELINE_PREFIX) :].strip())
        self.modeline.set_sensitive(editable)

        self._show(shown, wanted, specified=specified)
        self.resolution.connect("notify::selected", self._on_resolution)
        self.refresh.connect("notify::selected", self._on_refresh)
        self.modeline.connect("apply", self._on_modeline)

    @property
    def rows(self) -> tuple[Adw.PreferencesRow, ...]:
        return (self.resolution, self.refresh, self.modeline)

    def _show(self, shown: str | Size, wanted: float | None, *, specified: bool) -> None:
        """Fill Refresh rate for `shown`, selecting the rate closest to `wanted`."""
        if isinstance(shown, tuple):
            rates: list[float | None] = list(mode_rates(self._available, shown))
            if not specified:
                rates.insert(0, None)
                selected = 0
            elif wanted is None or not rates:
                selected = 0
            else:
                distance, selected = min(
                    (abs((rate or 0.0) - wanted), index) for index, rate in enumerate(rates)
                )
                if distance >= 1:  # a rate this display does not offer: show it anyway
                    rates.append(wanted)
                    selected = len(rates) - 1
            labels = [_rate_label(rate) for rate in rates] or [_rate_label(None)]
        else:
            rates = [None]
            selected = 0
            labels = [
                "Set by the modeline" if shown == _CUSTOM_MODELINE else "Chosen by the mode"
            ]

        self._quiet = True
        try:
            self._rates = rates or [None]
            self.refresh.set_model(Gtk.StringList.new(labels))
            self.refresh.set_selected(selected)
        finally:
            self._quiet = False
        self.refresh.set_sensitive(self._editable and isinstance(shown, tuple))
        self.modeline.set_visible(shown == _CUSTOM_MODELINE)

    def _on_resolution(self, combo: Adw.ComboRow, _param: Any) -> None:
        shown = self._entries[combo.get_selected()]
        if isinstance(shown, tuple):
            rates = mode_rates(self._available, shown)
            self._show(shown, rates[0] if rates else None, specified=True)
            self._apply({"mode": format_mode(*shown, self._rates[0])})
            return
        self._show(shown, None, specified=True)
        if shown != _CUSTOM_MODELINE:  # a modeline waits for its text
            self._apply({"mode": shown})

    def _on_refresh(self, combo: Adw.ComboRow, _param: Any) -> None:
        shown = self._entries[self.resolution.get_selected()]
        if self._quiet or not isinstance(shown, tuple):
            return
        self._apply({"mode": format_mode(*shown, self._rates[combo.get_selected()])})

    def _on_modeline(self, entry: Adw.EntryRow) -> None:
        text = entry.get_text().strip()
        if text:
            self._apply({"mode": f"{MODELINE_PREFIX}{text}"})


def _size_label(entry: str | Size) -> str:
    if isinstance(entry, str):
        return _MODE_LABELS.get(entry, entry)
    return f"{entry[0]}x{entry[1]}"


def _rate_label(rate: float | None) -> str:
    if rate is None:
        return "Not specified"
    return f"{rate:.2f}".rstrip("0").rstrip(".") + " Hz"


# --- Scale ------------------------------------------------------------------------------


class ScaleRows:
    """Scale presets plus a free value of at least 0.25, and the fractional-blur warning."""

    def __init__(self, current: Any, apply: Apply, *, editable: bool) -> None:
        self._apply = apply
        presets = [*_SCALE_PRESETS, _CUSTOM_SCALE]
        self._presets = presets
        text = _scale_text(current)

        self.combo = Adw.ComboRow(title="Scale", model=Gtk.StringList.new(presets))
        self.combo.set_selected(presets.index(text) if text in presets else len(presets) - 1)
        self.combo.set_sensitive(editable)

        self._committed = round(_float_or(current, 1.0), 2)
        self.spin = Gtk.SpinButton(
            adjustment=Gtk.Adjustment(
                lower=0.25, upper=10.0, step_increment=0.05, page_increment=0.25
            ),
            digits=2,
            numeric=True,
            valign=Gtk.Align.CENTER,
        )
        self.spin.set_value(self._committed)
        commit_on_settle(self.spin, self._commit_custom)
        self.custom = Adw.ActionRow(
            title="Custom scale", subtitle="Any scale from 0.25 up; Enter applies it."
        )
        self.custom.add_suffix(self.spin)
        self.custom.set_sensitive(editable)
        self.custom.set_visible(text not in _SCALE_PRESETS)

        self._warn(current)
        self.combo.connect("notify::selected", self._on_selected)

    @property
    def rows(self) -> tuple[Adw.PreferencesRow, ...]:
        return (self.combo, self.custom)

    def _warn(self, scale: Any) -> None:
        self.combo.set_subtitle(FRACTIONAL_WARNING if _fractional(scale) else "")

    def _write(self, scale: Any) -> None:
        self._warn(scale)
        self._apply({"scale": scale})

    def _on_selected(self, combo: Adw.ComboRow, _param: Any) -> None:
        choice = self._presets[combo.get_selected()]
        self.custom.set_visible(choice == _CUSTOM_SCALE)
        if choice == _CUSTOM_SCALE:
            self._warn(self._committed)
            return  # the spin writes once the user settles on a number
        self._write("auto" if choice == "auto" else _whole_or(float(choice)))

    def _commit_custom(self) -> None:
        value = round(self.spin.get_value(), 2)
        if value != self._committed:
            self._committed = value
            self._write(_whole_or(value))


def _scale_text(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "auto"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _fractional(value: Any) -> bool:
    try:
        return not float(value).is_integer()
    except (TypeError, ValueError):
        return False  # "auto": Hyprland picks, and picks a whole number where it can


# --- Reserved area ----------------------------------------------------------------------


def reserved_row(value: Any, apply: Apply, *, editable: bool) -> Adw.PreferencesRow:
    """The css-gap row (ADR-0008): space kept free at the display's edges. Benign lane."""
    field = GapField(
        value if isinstance(value, int | Mapping) else None,
        on_commit=lambda gaps: apply({"reserved": gaps}),
    )
    field.set_sensitive(editable)
    return gap_row(
        "Reserved area",
        field,
        subtitle="Space kept free at the edges, in pixels, for bars and docks.",
    )


# --- Advanced colour --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Luminance:
    key: str
    title: str
    subtitle: str
    digits: int
    upper: float
    seed: float
    """Where the spin starts when the user sets a value that was not set."""


_LUMINANCE: tuple[_Luminance, ...] = (
    _Luminance(
        "sdr_min_luminance",
        "SDR minimum luminance",
        "Black level for SDR content in HDR mode, in nits.",
        3,
        10.0,
        0.2,
    ),
    _Luminance(
        "sdr_max_luminance",
        "SDR maximum luminance",
        "White level for SDR content in HDR mode, in nits.",
        0,
        10000.0,
        80.0,
    ),
    _Luminance(
        "min_luminance",
        "Minimum luminance",
        "The display's black level, in nits. Not set uses what it reports.",
        3,
        10.0,
        0.0,
    ),
    _Luminance(
        "max_luminance",
        "Maximum luminance",
        "The display's peak brightness, in nits. Not set uses what it reports.",
        0,
        10000.0,
        1000.0,
    ),
    _Luminance(
        "max_avg_luminance",
        "Maximum average luminance",
        "The display's full-screen brightness, in nits. Not set uses what it reports.",
        0,
        10000.0,
        400.0,
    ),
)
"""The five HDR luminance fields the Importer maps, so no imported value is uneditable."""

_ON_OFF: tuple[tuple[Any, str], ...] = ((1, "On"), (0, "Off"))


def colour_rows(
    fields: Mapping[str, Any], apply: Apply, *, editable: bool
) -> tuple[Adw.PreferencesRow, ...]:
    """ADR-0008's Advanced colour group, collapsed: most displays need none of it.

    A header row that shows and hides the settings below it, rather than an expander
    nested in the display's expander: libadwaita draws a nested expander's arrow as open
    whenever its parent is open (seen in a widget probe), so a collapsed group would
    look expanded.
    """
    header = Adw.ActionRow(
        title="Advanced colour",
        subtitle="Colour management and HDR. Most displays need none of this.",
        activatable=True,
    )
    arrow = Gtk.Image(icon_name="pan-down-symbolic")
    header.add_suffix(arrow)
    eotf = fields.get("sdr_eotf")
    rows = [
        _choice_row(
            "Colour preset",
            "The colour space the display is driven in.",
            "cm",
            CM_PRESETS,
            fields.get("cm"),
            apply,
        ),
        _choice_row(
            "SDR transfer function",
            "How SDR content's brightness curve is read.",
            "sdr_eotf",
            SDR_EOTF_NAMES,
            None if eotf is None else sdr_eotf_name(eotf),
            apply,
        ),
        _number_row(
            "SDR brightness",
            "SDR content's brightness in HDR mode; 1 leaves it unchanged.",
            "sdrbrightness",
            fields.get("sdrbrightness"),
            apply,
        ),
        _number_row(
            "SDR saturation",
            "SDR content's saturation in HDR mode; 1 leaves it unchanged.",
            "sdrsaturation",
            fields.get("sdrsaturation"),
            apply,
        ),
        _choice_row(
            "Wide colour support",
            "Override whether the display reports wide colour.",
            "supports_wide_color",
            _ON_OFF,
            _tri_state(fields.get("supports_wide_color")),
            apply,
        ),
        _choice_row(
            "HDR support",
            "Override whether the display reports HDR.",
            "supports_hdr",
            _ON_OFF,
            _tri_state(fields.get("supports_hdr")),
            apply,
        ),
        *(_luminance_row(spec, fields.get(spec.key), apply) for spec in _LUMINANCE),
    ]
    for row in rows:
        row.set_sensitive(editable)
        row.set_visible(False)

    def toggle(_header: Adw.ActionRow) -> None:
        shown = not rows[0].get_visible()
        for row in rows:
            row.set_visible(shown)
        arrow.set_from_icon_name("pan-up-symbolic" if shown else "pan-down-symbolic")

    header.connect("activated", toggle)
    return (header, *rows)


def _choice_row(
    title: str,
    subtitle: str,
    key: str,
    choices: tuple[tuple[Any, str], ...],
    current: Any,
    apply: Apply,
) -> Adw.ComboRow:
    """A combo of `choices` after "Not set"; a value it does not know shows as itself."""
    values: list[Any] = [UNSET, *(value for value, _ in choices)]
    labels = [NOT_SET, *(label for _, label in choices)]
    if current is not None and current not in values:
        values.append(current)
        labels.append(str(current))
    row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels))
    row.set_selected(0 if current is None else values.index(current))
    row.connect(
        "notify::selected", lambda combo, _p: apply({key: values[combo.get_selected()]})
    )
    return row


def _tri_state(value: Any) -> Any:
    """`supports_*` is -1..1 and -1 is "use what the display reports": not set."""
    return None if value is None or value == -1 else value


def _number_row(
    title: str, subtitle: str, key: str, current: Any, apply: Apply
) -> Adw.ActionRow:
    committed = [round(_float_or(current, 1.0), 2)]
    spin = Gtk.SpinButton(
        adjustment=Gtk.Adjustment(
            lower=0.0, upper=5.0, step_increment=0.05, page_increment=0.25
        ),
        digits=2,
        numeric=True,
        valign=Gtk.Align.CENTER,
    )
    spin.set_value(committed[0])

    def commit() -> None:
        value = round(spin.get_value(), 2)
        if value != committed[0]:
            committed[0] = value
            apply({key: value})

    commit_on_settle(spin, commit)
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.add_suffix(spin)
    return row


def _luminance_row(spec: _Luminance, current: Any, apply: Apply) -> Adw.ActionRow:
    """A number in nits behind a "Not set" placeholder, with a Clear back to not set.

    Clicking "Not set" reveals the spin without writing: an HDR level the user has not
    chosen yet must not reach the file. Enter or focus leaving the spin writes it.
    """
    number = _float_or(current, -1.0)
    committed: list[float | None] = [None if number < 0 else number]
    spin = Gtk.SpinButton(
        adjustment=Gtk.Adjustment(
            lower=0.0,
            upper=spec.upper,
            step_increment=10**-spec.digits if spec.digits else 10.0,
            page_increment=0.1 if spec.digits else 100.0,
        ),
        digits=spec.digits,
        numeric=True,
        valign=Gtk.Align.CENTER,
    )
    spin.set_value(spec.seed if committed[0] is None else committed[0])

    placeholder = Gtk.Button(
        label=NOT_SET,
        css_classes=["flat", "dim-label"],
        valign=Gtk.Align.CENTER,
        tooltip_text=f"{NOT_SET} — click to set a value",
    )
    clear = Gtk.Button(
        icon_name="edit-clear-symbolic",
        css_classes=["flat"],
        valign=Gtk.Align.CENTER,
        tooltip_text="Clear, back to not set",
    )
    shown = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
    shown.append(spin)
    shown.append(clear)
    stack = Gtk.Stack(valign=Gtk.Align.CENTER, halign=Gtk.Align.END, hhomogeneous=False)
    stack.add_named(placeholder, "unset")
    stack.add_named(shown, "set")
    stack.set_visible_child_name("unset" if committed[0] is None else "set")

    def reveal(_button: Gtk.Button) -> None:
        stack.set_visible_child_name("set")
        spin.grab_focus()

    def unset(_button: Gtk.Button) -> None:
        stack.set_visible_child_name("unset")
        if committed[0] is not None:
            committed[0] = None
            apply({spec.key: UNSET})

    def commit() -> None:
        value = round(spin.get_value(), spec.digits)
        if stack.get_visible_child_name() == "set" and value != committed[0]:
            committed[0] = value
            apply({spec.key: int(value) if spec.digits == 0 else value})

    placeholder.connect("clicked", reveal)
    clear.connect("clicked", unset)
    commit_on_settle(spin, commit)

    row = Adw.ActionRow(title=spec.title, subtitle=spec.subtitle)
    row.add_suffix(stack)
    return row


# --- shared -----------------------------------------------------------------------------


def _float_or(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _whole_or(value: float) -> int | float:
    return int(value) if value.is_integer() else value
