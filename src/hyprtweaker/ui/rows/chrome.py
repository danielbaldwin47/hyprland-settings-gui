"""The suffix strip every generated Row wears (ADR-0013).

One convention, 353 Rows: whatever the typed control is, the widgets to its right are the
same five slots in the same order.

    [typed control] [state pills] [value summary] [dependency badge] [reset] [ⓘ help]

The Value summary is the collapsed preview on an `AdwExpanderRow` (ADR-0013 §4) and shows on
nothing else: an expander is the one Row shape that hides its own value, and a gradient with
two stops at 45° reads exactly like a gradient with one stop at 0° until it is opened.

Nothing here decides anything. `state.py` answers every question this asks ("is it
modified?", "is the dependency met?", "what does the default read as?") without a toolkit,
so what is left is assembling widgets and keeping them agreeing with that answer. The one
rule worth restating is ADR-0013 §3: **only the control is ever made insensitive.** A Row
whose dependency is unmet is a Row the user most needs to be able to read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gtk, Pango  # noqa: E402

from hyprtweaker.engine.schema import ResolvedOption  # noqa: E402
from hyprtweaker.ui.rows.state import (  # noqa: E402
    HelpContent,
    RowContext,
    RowState,
    help_content,
    row_state,
)

_SWATCH = 14.0
"""Side of one gradient swatch, in pixels. Big enough to read a colour off, small enough that
a four-stop gradient does not start competing with the Row's title for width."""

Navigate = Callable[[str], None]
"""Show the Row for an Option name. The window's job -- the factory only knows the name."""

_HELP_WIDTH_CHARS = 34
"""Wrap width for the popover's prose. A popover with no width request grows to the width of
its longest description, and a few of those are two hundred characters of wiki prose."""


class RowChrome:
    """One Row's suffix strip, and the handle that makes it agree with the model again.

    Built once per Row and refreshed in place. The Help popover is deliberately not part of
    that refresh: its four facts come from the Schema, which cannot change while the app
    runs, so rebuilding it per model change would be work with no possible result.
    """

    _state: RowState

    def __init__(
        self,
        row: Adw.ActionRow | Adw.ExpanderRow,
        control: Gtk.Widget,
        option: ResolvedOption,
        context: RowContext,
        *,
        on_reset: Callable[[str], None],
        navigate: Navigate | None = None,
    ) -> None:
        self._option = option
        self._context = context
        self._control = control
        self._on_reset = on_reset
        self._navigate = navigate

        self._pills = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._pills.set_valign(Gtk.Align.CENTER)

        self._swatches = SwatchStrip()
        self._summary_label = Gtk.Label(
            css_classes=["dim-label", "numeric"],
            valign=Gtk.Align.CENTER,
            hexpand=False,
        )
        self._summary = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
            css_classes=["value-summary"],
            visible=False,
        )
        self._summary.append(self._swatches.widget)
        self._summary.append(self._summary_label)

        self._dependency_label = _badge_label()
        self._dependency = Gtk.Button(
            child=self._dependency_label,
            css_classes=["pill", "flat", "caption"],
            valign=Gtk.Align.CENTER,
            hexpand=False,
            visible=False,
        )
        self._dependency.connect("clicked", self._on_dependency_clicked)

        self._reset = Gtk.Button(
            icon_name="edit-undo-symbolic",
            css_classes=["flat"],
            valign=Gtk.Align.CENTER,
            visible=False,
        )
        self._reset.connect("clicked", self._on_reset_clicked)

        self._help = _help_button(help_content(option))

        # One box holding all five slots, rather than five `add_suffix` calls. The order is
        # part of the convention (ADR-0013) and the two Row types disagree about what
        # `add_suffix` means: `AdwActionRow` appends, `AdwExpanderRow` *prepends*, so five
        # calls put the ⓘ at opposite ends of the strip depending on the widget type --
        # observed on the running app, where every expander wore its chrome backwards. A box
        # is ordered by its own appends and both Rows simply carry it.
        self._strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._strip.set_valign(Gtk.Align.CENTER)
        for slot in (self._pills, self._summary, self._dependency, self._reset, self._help):
            self._strip.append(slot)
        row.add_suffix(self._strip)

        # Built by `refresh`, which is also what puts the strip in its opening state --
        # there is no moment where a Row is on screen wearing chrome nobody decided.
        self.refresh()

    # --- what the Page and the tests read ---------------------------------------------------

    @property
    def state(self) -> RowState:
        """The state this strip is currently showing. The UI tier asserts against it."""
        return self._state

    @property
    def summary(self) -> Gtk.Box:
        """The Value summary slot. Invisible on every Row whose control is not an expander."""
        return self._summary

    @property
    def summary_text(self) -> str:
        """The dim collapsed-value label: an angle, a gap run, or the Option's null label."""
        return self._summary_label.get_text()

    @property
    def summary_swatches(self) -> tuple[str, ...]:
        """The gradient stops currently drawn, as the CSS strings they were given."""
        return self._swatches.colors

    @property
    def dependency_badge(self) -> Gtk.Button:
        return self._dependency

    @property
    def dependency_text(self) -> str:
        """The badge's visible text. Its own label widget, so it can ellipsize."""
        return self._dependency_label.get_text()

    @property
    def reset(self) -> Gtk.Button:
        return self._reset

    @property
    def help(self) -> Gtk.MenuButton:
        return self._help

    @property
    def pill_labels(self) -> tuple[str, ...]:
        return tuple(pill.label for pill in self._state.pills)

    # --- keeping up with the model -----------------------------------------------------------

    def refresh(self) -> None:
        """Recompute the whole strip. Cheap enough to run on every Row that could have moved.

        Control sensitivity is set here rather than by the Page, because two independent
        things decide it -- the session being live and the dependency being met -- and a
        second writer would race the first back to the wrong answer.
        """
        state = row_state(self._option, self._context)
        self._state = state

        self._set_pills(state)
        self._set_summary(state)

        badge = state.dependency
        self._dependency.set_visible(badge is not None)
        if badge is not None:
            self._dependency_label.set_label(badge.label)
            self._dependency.set_tooltip_text(badge.tooltip)

        self._reset.set_visible(state.modified)
        self._reset.set_sensitive(state.resettable)
        self._reset.set_tooltip_text(state.reset_tooltip)

        self._control.set_sensitive(state.editable)

    def _set_summary(self, state: RowState) -> None:
        """Redraw the collapsed preview. Refreshed with the rest of the strip, not on expand.

        Every tick of a gradient's angle slider comes back through here, which is what makes
        the collapsed Row track a drag it is not even showing -- and it costs one label set
        and one queued redraw, so it can afford to.
        """
        summary = state.summary
        self._summary.set_visible(summary is not None)
        if summary is None:
            return
        self._summary_label.set_label(summary.text)
        self._swatches.set_colors(summary.swatches)

    def _set_pills(self, state: RowState) -> None:
        while (child := self._pills.get_first_child()) is not None:
            self._pills.remove(child)
        for pill in state.pills:
            label = _pill_label(pill.label)
            label.add_css_class("pill")
            label.add_css_class("dim-label")
            label.set_tooltip_text(pill.tooltip)
            self._pills.append(label)
        self._pills.set_visible(bool(state.pills))

    # --- signals -----------------------------------------------------------------------------

    def _on_dependency_clicked(self, _button: Gtk.Button) -> None:
        badge = self._state.dependency
        if badge is not None and self._navigate is not None:
            self._navigate(badge.option)

    def _on_reset_clicked(self, _button: Gtk.Button) -> None:
        # Reset means Unset -- stop emitting the Option -- never write-the-default-value
        # (ADR-0013 §6). An explicitly-set default survives Hyprland changing its mind about
        # what the default is, which is not what the arrow promises.
        self._on_reset(self._option.name)
        # Its own feedback, immediately: the arrow that just unset the Option has no
        # business still being there while the write makes its way round to the Page.
        self.refresh()


class SwatchStrip:
    """The colour-swatch half of a gradient's Value summary (ADR-0013 §4).

    One `GtkDrawingArea` for the whole strip rather than one widget per stop: a gradient can
    carry any number of colours, and re-parenting a row of widgets on every tick of the
    angle slider is work with a visible cost for something that is four rectangles.

    Colours arrive as CSS strings from `state.value_summary`, which has no toolkit to parse
    them with. Anything `Gdk.RGBA` refuses is dropped rather than drawn as black -- a
    swatch is a claim about a colour, and a wrong one is worse than a missing one.
    """

    def __init__(self) -> None:
        self._colors: tuple[str, ...] = ()
        self._rgba: list[Gdk.RGBA] = []
        self.widget = Gtk.DrawingArea(valign=Gtk.Align.CENTER, visible=False)
        self.widget.set_content_height(int(_SWATCH))
        self.widget.set_draw_func(self._draw)

    @property
    def colors(self) -> tuple[str, ...]:
        return self._colors

    def set_colors(self, colors: tuple[str, ...]) -> None:
        accepted: list[str] = []
        self._rgba = []
        for css in colors:
            rgba = Gdk.RGBA()
            if rgba.parse(css):
                accepted.append(css)
                self._rgba.append(rgba)
        # Only the ones that will actually be drawn, so `colors` cannot report a swatch the
        # strip silently dropped -- the property is what the UI tier asserts against.
        self._colors = tuple(accepted)
        self.widget.set_content_width(int(_SWATCH * len(self._rgba)))
        self.widget.set_visible(bool(self._rgba))
        self.widget.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, context: Any, width: int, height: int) -> None:
        if not self._rgba:
            return
        span = width / len(self._rgba)
        for index, rgba in enumerate(self._rgba):
            context.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)
            context.rectangle(index * span, 0, span, height)
            context.fill()


_BADGE_CHARS = 20
"""How wide the dependency badge is, in characters. Fixed, and both bounds were measured on
the real toolkit rather than reasoned about.

`AdwActionRow` hands the title box every scrap of slack and its suffixes their *minimum*, so
the badge's minimum is the whole of its width. Un-ellipsized, that minimum is the full text
-- "Requires Resize windows by dragging their border" squeezed "Border grab area" down to
one character per line. Ellipsized with no floor, the minimum is about three characters --
the same badge collapsed to "Requires Re…" on a 1400px window. Twenty is the width that
does neither, and the full sentence is one hover away in the tooltip."""


def _pill_label(text: str = "") -> Gtk.Label:
    """A state pill. Never ellipsized: the vocabulary is three short fixed strings, and
    "Advanced" rendered as "A…" says less than nothing."""
    return Gtk.Label(
        label=text,
        css_classes=["caption"],
        valign=Gtk.Align.CENTER,
        hexpand=False,
    )


def _badge_label() -> Gtk.Label:
    return Gtk.Label(
        css_classes=["caption"],
        valign=Gtk.Align.CENTER,
        hexpand=False,
        ellipsize=Pango.EllipsizeMode.END,
        width_chars=_BADGE_CHARS,
        max_width_chars=_BADGE_CHARS,
    )


def _help_button(content: HelpContent) -> Gtk.MenuButton:
    """The ⓘ. Everything referential about an Option lives behind it and nowhere else.

    Help text, the dotted key (selectable *and* one click from the clipboard), the default,
    and the wiki link -- ADR-0013 §7 gathers them here rather than spending 353 identical
    link icons on the Rows themselves.
    """
    body = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=8,
        margin_top=12,
        margin_bottom=12,
        margin_start=12,
        margin_end=12,
    )

    body.append(
        Gtk.Label(
            label=content.text,
            wrap=True,
            xalign=0.0,
            max_width_chars=_HELP_WIDTH_CHARS,
        )
    )

    body.append(_key_row(content.dotted_key))

    body.append(
        Gtk.Label(
            label=f"Default: {content.default_label}",
            xalign=0.0,
            wrap=True,
            max_width_chars=_HELP_WIDTH_CHARS,
            css_classes=["dim-label"],
        )
    )

    if content.help_url:
        link = Gtk.LinkButton(uri=content.help_url, label="Learn more on the wiki")
        link.set_halign(Gtk.Align.START)
        body.append(link)

    return Gtk.MenuButton(
        icon_name="help-about-symbolic",
        css_classes=["flat"],
        valign=Gtk.Align.CENTER,
        tooltip_text="About this setting",
        popover=Gtk.Popover(child=body),
    )


def _key_row(dotted_key: str) -> Gtk.Widget:
    """The dotted key: monospace, selectable, and copyable in one click.

    Selectable alone would do for "copyable" on a desktop, but this key is the thing a user
    takes *out* of the app -- into `user.lua`, into a wiki search, into a bug report -- and
    dragging a selection across a popover that closes on the first stray click is a poor way
    to spend the one gesture that matters.
    """
    label = Gtk.Label(
        label=dotted_key,
        xalign=0.0,
        selectable=True,
        hexpand=True,
        css_classes=["monospace"],
    )

    copy = Gtk.Button(
        icon_name="edit-copy-symbolic",
        css_classes=["flat"],
        valign=Gtk.Align.CENTER,
        tooltip_text="Copy the config key",
    )

    def copied(_button: Gtk.Button) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(dotted_key)

    copy.connect("clicked", copied)

    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.append(label)
    box.append(copy)
    return box
