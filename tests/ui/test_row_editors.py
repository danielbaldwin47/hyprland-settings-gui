"""The complex-value editors in a real toolkit (#58).

What each part *decides* is settled without a display -- the collapsed summary in
`tests/unit/test_ui_row_state.py`, the drag's shape in `tests/unit/test_ui_row_gesture.py`,
the Lua a value renders to in `tests/unit/test_writer_modules.py`. What is left for this
tier is the half only GTK can answer: that turning the widgets produces exactly those
values, and that a drag on the one continuous control in the app previews per tick and
writes once.

That last claim is what joins the two tiers. The unit golden asserts that a `Gradient` of
`Color`s renders as a Lua table; these tests assert that dragging the angle slider is what
puts one in the model. Neither is worth much without the other.

Toolkit imports sit inside the tests: importing ``gi`` at module scope would error during
collection on a machine without PyGObject instead of skipping (see ``conftest.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from test_row_chrome import SCHEMA, FakeSession, build_row, children

from hyprtweaker.engine.model import Color, CssGaps, Gradient, Vec2

ACTIVE_BORDER = "general:col.active_border"
GAPS_IN = "general:gaps_in"
FLOAT_GAPS = "general:float_gaps"
SHADOW_OFFSET = "decoration:shadow:offset"
BACKGROUND = "misc:background_color"
INACTIVE_TEXT = "group:groupbar:text_color_inactive"
SHADOW_INACTIVE = "decoration:shadow:color_inactive"


class PreviewSession(FakeSession):
    """A `FakeSession` that records the Eval preview tier's half of a gesture.

    `FakeSession` has no `preview_option` because nothing before #58 previewed. Keeping the
    two lists apart is the whole point: `previewed` must fill during a drag and `applied`
    must not, which is the difference between a preview and a write-storm.
    """

    def __init__(self, *, live: bool = True) -> None:
        super().__init__(live=live)
        self.previewed: list[Any] = []

    def preview_option(self, name: str, value: Any) -> None:
        self.model.set(name, value)
        self.previewed.append(value)


def walk(widget: Any) -> list[Any]:
    found = [widget]
    for child in children(widget):
        found.extend(walk(child))
    return found


def controls(row: Any, kind: type) -> list[Any]:
    return [widget for widget in walk(row.control) if isinstance(widget, kind)]


# --- colours ----------------------------------------------------------------------------------


def test_a_colour_row_gets_a_colour_button_with_alpha(tmp_path: Path) -> None:
    """Every Hyprland colour is a 32-bit ARGB word; an editor without alpha would silently
    make half the config opaque."""
    from gi.repository import Gtk

    row = build_row(BACKGROUND, PreviewSession())

    assert isinstance(row.control, Gtk.ColorDialogButton)
    assert row.control.get_dialog().get_with_alpha()


def test_picking_a_colour_writes_a_model_colour_not_a_string() -> None:
    from gi.repository import Gdk

    session = PreviewSession()
    row = build_row(BACKGROUND, session)

    chosen = Gdk.RGBA()
    assert chosen.parse("#ff8800cc")
    row.control.set_rgba(chosen)

    assert session.model.get(BACKGROUND) == Color(0xCCFF8800)
    assert session.applied == [BACKGROUND], "a dialog is a decided gesture: one transaction"


def test_a_nullable_colour_shows_its_label_instead_of_a_black_swatch() -> None:
    """Four of the six colours mean "same as the title colour" when unset. A button showing
    *some* colour for that would be reporting a choice nobody made."""
    from gi.repository import Gtk

    row = build_row(INACTIVE_TEXT, PreviewSession())

    assert isinstance(row.control, Gtk.Stack)
    assert row.control.get_visible_child_name() == "none"
    assert row.control.get_visible_child().get_label() == "Same as the title colour"


def test_clicking_a_nullable_colours_placeholder_gives_it_a_value() -> None:
    session = PreviewSession()
    row = build_row(INACTIVE_TEXT, session)

    row.control.get_visible_child().emit("clicked")
    row.refresh()

    assert row.control.get_visible_child_name() == "value"
    assert isinstance(session.model.get(INACTIVE_TEXT), Color)


# --- gradients --------------------------------------------------------------------------------


def test_a_gradient_row_is_an_expander_with_a_collapsed_summary() -> None:
    from gi.repository import Adw

    row = build_row(ACTIVE_BORDER, PreviewSession())

    assert isinstance(row.widget, Adw.ExpanderRow)
    assert row.chrome.summary.get_visible()
    assert row.chrome.summary_text == "0°"
    assert row.chrome.summary_swatches == ("#ffffffff",), "the schema default, one stop"


def test_adding_and_removing_a_stop_changes_the_gradients_shape() -> None:
    from gi.repository import Gtk

    session = PreviewSession()
    row = build_row(ACTIVE_BORDER, session)

    add = [button for button in controls(row, Gtk.Button) if button.get_icon_name()][-1]
    add.emit("clicked")

    gradient = session.model.get(ACTIVE_BORDER)
    assert isinstance(gradient, Gradient)
    assert len(gradient.colors) == 2

    remove = next(
        button
        for button in controls(row, Gtk.Button)
        if button.get_icon_name() == "list-remove-symbolic"
    )
    remove.emit("clicked")

    assert len(session.model.get(ACTIVE_BORDER).colors) == 1


def test_a_single_stop_gradient_offers_no_way_to_remove_its_last_colour() -> None:
    """`Gradient` refuses to hold none and so does Hyprland's parser."""
    from gi.repository import Gtk

    row = build_row(ACTIVE_BORDER, PreviewSession())

    icons = {button.get_icon_name() for button in controls(row, Gtk.Button)}
    assert "list-remove-symbolic" not in icons


def test_editing_a_stop_writes_a_gradient_of_model_colours() -> None:
    from gi.repository import Gdk, Gtk

    session = PreviewSession()
    row = build_row(ACTIVE_BORDER, session)

    chosen = Gdk.RGBA()
    assert chosen.parse("#33ccffee")
    controls(row, Gtk.ColorDialogButton)[0].set_rgba(chosen)

    assert session.model.get(ACTIVE_BORDER) == Gradient((Color(0xEE33CCFF),), 0.0)


def test_a_nullable_gradient_shows_its_label_rather_than_a_white_stop_at_zero() -> None:
    """The two `color_inactive` gradients fall back to their related colour when unset.

    An editor opening on one opaque white stop at 0° would state a gradient the config does
    not contain -- and the collapsed Row would contradict the expanded one, since the
    summary reads "Same as shadow colour" either way.
    """
    from gi.repository import Gtk

    row = build_row(SHADOW_INACTIVE, PreviewSession())

    assert isinstance(row.control, Gtk.Stack)
    assert row.control.get_visible_child_name() == "none"
    assert row.control.get_visible_child().get_label() == "Same as shadow colour"
    assert row.chrome.summary_text == "Same as shadow colour"


def test_clicking_a_nullable_gradients_placeholder_gives_it_one_stop() -> None:
    session = PreviewSession()
    row = build_row(SHADOW_INACTIVE, session)

    row.control.get_visible_child().emit("clicked")
    row.refresh()
    row.chrome.refresh()

    gradient = session.model.get(SHADOW_INACTIVE)
    assert isinstance(gradient, Gradient)
    assert len(gradient.colors) == 1
    assert row.control.get_visible_child_name() == "value"
    assert row.chrome.summary_text == "0°"


def test_resetting_a_nullable_colour_puts_its_button_back_to_the_starting_colour() -> None:
    """The placeholder writes whatever the button is holding, so a button left on the colour
    the Row was just reset *from* would make reset-then-set silently reinstate it."""
    from gi.repository import Gdk

    session = PreviewSession()
    row = build_row(INACTIVE_TEXT, session)

    session.model.set(INACTIVE_TEXT, Color(0xFF112233))
    row.refresh()
    chosen = Gdk.RGBA()
    assert chosen.parse("#112233ff")
    assert row.control.get_visible_child().get_rgba().equal(chosen)

    session.model.unset(INACTIVE_TEXT)
    row.refresh()
    row.control.get_visible_child().emit("clicked")

    assert session.model.get(INACTIVE_TEXT) == Color(0xFFFFFFFF), (
        "a fresh start, not the old one"
    )


# --- the one continuous gesture in the app ----------------------------------------------------


def angle_slider(row: Any) -> Any:
    from gi.repository import Gtk

    return controls(row, Gtk.Scale)[0]


def test_dragging_the_angle_previews_per_tick_and_writes_nothing() -> None:
    """ADR-0010: a continuous gesture is `eval` per tick. Ten writes here would be ten full
    teardown reloads of the compositor's config state."""
    session = PreviewSession()
    row = build_row(ACTIVE_BORDER, session)
    slider = angle_slider(row)

    for angle in (15, 30, 45):
        slider.set_value(angle)

    assert [gradient.angle for gradient in session.previewed] == [15.0, 30.0, 45.0]
    assert session.applied == [], "not one file write during the drag"


def test_releasing_the_angle_lands_exactly_one_transaction() -> None:
    session = PreviewSession()
    row = build_row(ACTIVE_BORDER, session)
    slider = angle_slider(row)

    for angle in (15, 30, 45):
        slider.set_value(angle)
    row.gesture.end()

    assert session.applied == [ACTIVE_BORDER]
    assert session.model.get(ACTIVE_BORDER).angle == 45.0


def test_a_reload_mid_drag_leaves_the_row_showing_the_truth() -> None:
    """The last acceptance criterion. A reload wipes the Eval preview and the session
    re-reads the model; the Page abandons the gesture on the way through, so the value the
    user was dragging towards is not then written by somebody else's reload."""
    from hyprtweaker.engine.model import UNSET

    session = PreviewSession()
    row = build_row(ACTIVE_BORDER, session)
    slider = angle_slider(row)

    slider.set_value(45)
    assert row.gesture.active

    # What `ConfigPage.refresh` does after a foreign reload's re-read put the truth back.
    session.model.unset(ACTIVE_BORDER)
    row.abandon_gesture()
    row.refresh()
    row.chrome.refresh()

    row.gesture.end()

    assert session.applied == [], "the reload must not commit the abandoned drag"
    assert session.model.get(ACTIVE_BORDER) is UNSET
    assert row.chrome.summary_text == "0°", "back to the schema default the Row now holds"


def test_a_row_with_no_gesture_survives_being_abandoned() -> None:
    """`ConfigPage.refresh` calls this on all 353 Rows without asking which have one."""
    row = build_row("decoration:rounding", PreviewSession())

    row.abandon_gesture()

    assert row.gesture is None


# --- css-gaps ---------------------------------------------------------------------------------


def gap_parts(row: Any) -> tuple[Any, list[Any]]:
    from gi.repository import Gtk

    uniform = controls(row, Gtk.CheckButton)[0]
    return uniform, controls(row, Gtk.SpinButton)


def test_gaps_open_uniform_and_write_all_four_sides() -> None:
    session = PreviewSession()
    row = build_row(GAPS_IN, session)
    uniform, spins = gap_parts(row)

    assert uniform.get_active(), "the schema default is 5 on every side"
    spins[0].set_value(12)

    assert session.model.get(GAPS_IN) == CssGaps.uniform(12)


def test_turning_uniform_off_reveals_four_sides_that_edit_independently() -> None:
    session = PreviewSession()
    row = build_row(GAPS_IN, session)
    uniform, spins = gap_parts(row)

    uniform.set_active(False)
    # The all-sides spinner is built first; the four side spinners follow it in TRBL order.
    spins[2].set_value(20)

    assert session.model.get(GAPS_IN) == CssGaps(5, 20, 5, 5)
    row.chrome.refresh()
    assert row.chrome.summary_text == "5 · 20 · 5 · 5"


def test_turning_uniform_back_on_flattens_to_the_top_side() -> None:
    """The user asked for one number, and the number they were reading is the top one."""
    session = PreviewSession()
    row = build_row(GAPS_IN, session)
    uniform, spins = gap_parts(row)

    uniform.set_active(False)
    spins[1].set_value(9)
    spins[2].set_value(20)
    uniform.set_active(True)

    assert session.model.get(GAPS_IN) == CssGaps.uniform(9)


def test_an_uneven_gaps_value_opens_on_the_four_side_form() -> None:
    session = PreviewSession()
    session.model.set(GAPS_IN, "4 8 4 8")
    row = build_row(GAPS_IN, session)
    uniform, _spins = gap_parts(row)

    assert not uniform.get_active()
    assert row.chrome.summary_text == "4 · 8 · 4 · 8"


def test_a_nullable_gaps_row_shows_its_label_rather_than_four_minus_ones() -> None:
    """`general:float_gaps` spells "same as the outer gaps" as `-1`."""
    from gi.repository import Gtk

    session = PreviewSession()
    session.model.set(FLOAT_GAPS, None)
    row = build_row(FLOAT_GAPS, session)

    assert isinstance(row.control, Gtk.Stack)
    assert row.control.get_visible_child_name() == "none"
    assert row.chrome.summary_text == "Same as outer gaps"


# --- vec2 -------------------------------------------------------------------------------------


def test_a_vec2_row_edits_two_axes_within_their_curated_bounds() -> None:
    from gi.repository import Gtk

    session = PreviewSession()
    row = build_row(SHADOW_OFFSET, session)
    spins = controls(row, Gtk.SpinButton)

    bounds = SCHEMA[SHADOW_OFFSET].vec2_range
    assert bounds is not None
    assert spins[0].get_adjustment().get_lower() == bounds.min_x
    assert spins[1].get_adjustment().get_upper() == bounds.max_y

    spins[0].set_value(3)
    spins[1].set_value(-2)

    assert session.model.get(SHADOW_OFFSET) == Vec2(3.0, -2.0)
    row.chrome.refresh()
    assert row.chrome.summary_text == "3.0, -2.0"


# --- the Row contract still holds for all of them ---------------------------------------------


def test_every_complex_editor_dims_only_its_control_on_a_read_only_session() -> None:
    """ADR-0013 §3, over the Rows that grew a whole editor rather than one widget."""
    for name in (BACKGROUND, ACTIVE_BORDER, GAPS_IN, SHADOW_OFFSET):
        row = build_row(name, PreviewSession(live=False))

        assert not row.control.get_sensitive(), name
        assert row.widget.get_sensitive(), name
        assert row.widget.get_title() and row.widget.get_subtitle(), name


def test_a_colour_row_shows_no_value_summary() -> None:
    """A colour button *is* its own preview; a dim label repeating it would be noise."""
    row = build_row(BACKGROUND, PreviewSession())

    assert not row.chrome.summary.get_visible()


def test_the_suffix_strip_reads_in_the_same_order_on_both_row_types() -> None:
    """`AdwExpanderRow.add_suffix` prepends where `AdwActionRow.add_suffix` appends.

    Found by running the app: every expander wore its chrome backwards -- ⓘ first, then the
    Value summary -- while every ActionRow had it the right way round. ADR-0013 fixes the
    order, so it cannot depend on which Row type the Option happened to resolve to.
    """
    for name in (GAPS_IN, "decoration:rounding"):
        row = build_row(name, PreviewSession())
        strip = children(row.chrome.help.get_parent())

        assert strip.index(row.chrome.summary) < strip.index(row.chrome.help), name
        assert strip.index(row.chrome.dependency_badge) < strip.index(row.chrome.reset), name
        assert strip[-1] is row.chrome.help, name
