"""The Row contract in a real toolkit: suffix strip, popover, honest placeholders.

What each part *decides* is settled in `tests/unit/test_ui_row_state.py`, where it needs no
display. What is left for this tier is the half only GTK can answer: that the widgets are
actually built and hung off the Row, that clicking them does what the ADR says, and that a
control with no value shows words instead of a number.

The session is a stand-in rather than the real one, for the same reason the unit tier uses a
fake context: a `Session` with no compositor is read-only and refuses every edit, so a reset
button tested against one would pass without ever having reset anything.

Toolkit imports sit inside the tests: importing ``gi`` at module scope would error during
collection on a machine without PyGObject instead of skipping (see ``conftest.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hyprtweaker.engine.model import UNSET, ConfigModel, OptionValue
from hyprtweaker.engine.schema import ResolvedOption, Schema, Visibility, load_schema

SCHEMA = load_schema()

GAPS_IN = "general:gaps_in"
"""A css-gaps Option, so its editor is still #58's -- and its chrome is this ticket's."""

ROUNDING = "decoration:rounding"
GRAB_AREA = "general:extend_border_grab_area"
RESIZE_ON_BORDER = "general:resize_on_border"
PRESSURE_MIN = "input:tablettool:pressure_range_min"


class FakeSession:
    """Everything `RowFactory` asks of a `Session`, over a real model and no sockets."""

    def __init__(self, *, live: bool = True) -> None:
        self.schema: Schema = SCHEMA
        self.live = live
        self.pending_restart: frozenset[str] = frozenset()
        self.unapplied: frozenset[str] = frozenset()
        self.overridden: frozenset[str] = frozenset()
        self.model = ConfigModel(SCHEMA)
        self.applied: list[str] = []

    def value_of(self, option: ResolvedOption) -> OptionValue:
        return self.model.get(option.name)

    def effective_value(self, option: ResolvedOption) -> Any:
        value = self.model.get(option.name)
        return option.default if value is UNSET else value

    def is_modified(self, option: ResolvedOption) -> bool:
        return self.model.is_set(option.name)

    def set_option(self, name: str, value: Any) -> None:
        self.model.set(name, value)
        self.applied.append(name)

    def touch_option(self, name: str, value: Any) -> None:
        self.set_option(name, value)

    def unset_option(self, name: str) -> None:
        self.model.unset(name)
        self.applied.append(name)


def build_row(name: str, session: FakeSession, **kwargs: Any) -> Any:
    from gi.repository import Adw

    from hyprtweaker.ui.rows.factory import RowFactory

    Adw.init()
    return RowFactory(session, **kwargs).build(SCHEMA[name])  # type: ignore[arg-type]


def children(widget: Any) -> list[Any]:
    found = []
    child = widget.get_first_child()
    while child is not None:
        found.append(child)
        child = child.get_next_sibling()
    return found


def labels_under(widget: Any) -> list[str]:
    """Every `GtkLabel` text in a widget tree, in document order."""
    from gi.repository import Gtk

    texts = []
    if isinstance(widget, Gtk.Label):
        texts.append(widget.get_text())
    for child in children(widget):
        texts.extend(labels_under(child))
    return texts


# --- the suffix strip is really there ---------------------------------------------------------


def test_every_row_carries_a_help_button_and_a_hidden_reset() -> None:
    session = FakeSession()
    row = build_row(GAPS_IN, session)

    assert row.chrome.help.get_popover() is not None
    assert not row.chrome.reset.get_visible(), "an untouched Option has nothing to reset"
    assert not row.chrome.dependency_badge.get_visible()


def test_the_reset_arrow_appears_when_the_option_is_set_and_unsets_it() -> None:
    """Reset means Unset -- stop emitting -- never write-the-default-value (ADR-0013 §6)."""
    session = FakeSession()
    row = build_row(GAPS_IN, session)

    session.model.set(GAPS_IN, 12)
    row.chrome.refresh()
    assert row.chrome.reset.get_visible()
    assert "Reset to default" in row.chrome.reset.get_tooltip_text()

    row.chrome.reset.emit("clicked")

    assert session.model.get(GAPS_IN) is UNSET
    assert not row.chrome.reset.get_visible()


def test_an_option_set_to_its_own_default_still_shows_reset() -> None:
    """The tri-state, in the UI: choosing today's default is a decision the user can undo,
    and it survives Hyprland changing its mind about the default (ADR-0005)."""
    session = FakeSession()
    row = build_row(GAPS_IN, session)

    session.model.set(GAPS_IN, SCHEMA[GAPS_IN].default)
    row.chrome.refresh()

    assert row.chrome.reset.get_visible()


def test_editing_a_row_raises_its_reset_arrow_without_the_page_being_told() -> None:
    """The arrow has to arrive with the gesture, not with the apply that follows it."""
    edited: list[str] = []
    session = FakeSession()
    row = build_row(ROUNDING, session, on_edited=edited.append)

    row.control.set_value(9)

    assert edited == [ROUNDING]
    assert session.model.get(ROUNDING) == 9

    row.chrome.refresh()
    assert row.chrome.reset.get_visible()


# --- dependency badges ------------------------------------------------------------------------


def test_an_unmet_dependency_dims_the_control_and_leaves_the_row_readable() -> None:
    """ADR-0013 §3: never hide the Row, never dim its text."""
    session = FakeSession()
    row = build_row(GRAB_AREA, session)

    assert row.chrome.dependency_badge.get_visible()
    assert row.chrome.dependency_text == f"Requires {SCHEMA[RESIZE_ON_BORDER].title}"
    assert not row.control.get_sensitive()
    assert row.widget.get_sensitive()
    assert row.widget.get_title()
    assert row.widget.get_subtitle()


def test_satisfying_the_dependency_re_enables_the_control() -> None:
    session = FakeSession()
    row = build_row(GRAB_AREA, session)

    session.model.set(RESIZE_ON_BORDER, True)
    row.chrome.refresh()

    assert not row.chrome.dependency_badge.get_visible()
    assert row.control.get_sensitive()


def test_the_badge_navigates_to_the_option_that_gates_the_row() -> None:
    went_to: list[str] = []
    row = build_row(GRAB_AREA, FakeSession(), navigate=went_to.append)

    row.chrome.dependency_badge.emit("clicked")

    assert went_to == [RESIZE_ON_BORDER]


def test_a_long_badge_ellipsizes_rather_than_squeezing_the_rows_title() -> None:
    """`AdwActionRow` takes a suffix's width off the title, and "Requires Resize windows by
    dragging their border" took all of it -- the title rendered one character per line."""
    from gi.repository import Pango

    row = build_row(GRAB_AREA, FakeSession())
    label = row.chrome.dependency_badge.get_child()

    assert label.get_ellipsize() == Pango.EllipsizeMode.END
    assert not label.get_hexpand()
    assert row.chrome.dependency_badge.get_tooltip_text()


def test_a_read_only_session_dims_the_control_even_with_the_dependency_met() -> None:
    """Two reasons to dim, and they compose -- neither may overwrite the other's answer."""
    session = FakeSession(live=False)
    session.model.set(RESIZE_ON_BORDER, True)
    row = build_row(GRAB_AREA, session)

    assert not row.control.get_sensitive()


def test_a_read_only_session_dims_the_reset_arrow_too() -> None:
    """It would otherwise be a visible, clickable button whose write the session drops --
    the arrow stays up afterwards, looking like it failed rather than like it was refused."""
    session = FakeSession(live=False)
    session.model.set(GAPS_IN, 12)
    row = build_row(GAPS_IN, session)

    assert row.chrome.reset.get_visible()
    assert not row.chrome.reset.get_sensitive()


def test_a_dependency_disabled_row_keeps_a_usable_reset_arrow() -> None:
    session = FakeSession()
    session.model.set(GRAB_AREA, 30)
    row = build_row(GRAB_AREA, session)

    assert not row.control.get_sensitive()
    assert row.chrome.reset.get_sensitive()

    row.chrome.reset.emit("clicked")
    assert session.model.get(GRAB_AREA) is UNSET


# --- honest placeholders ----------------------------------------------------------------------


def test_a_sentinel_valued_number_shows_its_label_instead_of_minus_one() -> None:
    """The first acceptance criterion, in the widget that could most easily lie about it."""
    from gi.repository import Gtk

    session = FakeSession()
    row = build_row(PRESSURE_MIN, session)

    assert isinstance(row.control, Gtk.Stack)
    assert row.control.get_visible_child_name() == "none"
    assert row.control.get_visible_child().get_label() == "Device default"


def test_giving_a_sentinel_valued_number_a_value_reveals_the_spinner() -> None:
    session = FakeSession()
    row = build_row(PRESSURE_MIN, session)

    row.control.get_visible_child().emit("clicked")
    row.refresh()

    assert row.control.get_visible_child_name() == "value"
    assert session.model.get(PRESSURE_MIN) == 0.0


def test_a_nullable_string_row_keeps_its_label_in_the_placeholder() -> None:
    row = build_row("input:kb_variant", FakeSession())

    assert row.control.get_text() == ""
    assert row.control.get_placeholder_text() == "None"


def test_a_nullable_combo_offers_no_value_as_a_choice_rather_than_as_a_blank() -> None:
    from gi.repository import Gtk

    row = build_row("input:accel_profile", FakeSession())

    assert isinstance(row.control, Gtk.DropDown)
    assert row.control.get_selected() == 0
    assert row.control.get_model().get_string(0) == "Device default"


# --- state pills ------------------------------------------------------------------------------


def test_an_advanced_row_wears_a_pill_saying_so() -> None:
    option = next(o for o in SCHEMA if o.visibility is Visibility.ADVANCED)
    row = build_row(option.name, FakeSession())

    assert row.chrome.pill_labels == ("Advanced",)


def test_a_restart_flagged_row_swaps_its_pill_once_the_write_lands() -> None:
    session = FakeSession()
    row = build_row("xwayland:enabled", session)

    assert row.chrome.pill_labels == ("Restart",)

    session.pending_restart = frozenset({"xwayland:enabled"})
    row.chrome.refresh()

    assert row.chrome.pill_labels == ("Pending restart",)


# --- the help popover -------------------------------------------------------------------------


def test_the_popover_holds_the_key_the_default_and_the_wiki_link() -> None:
    from gi.repository import Gtk

    option = SCHEMA["input:kb_layout"]
    row = build_row(option.name, FakeSession())

    popover = row.chrome.help.get_popover()
    texts = labels_under(popover.get_child())

    assert option.description in texts
    assert option.dotted_key in texts
    assert f"Default: {option.default}" in texts

    links = [w for w in _walk(popover.get_child()) if isinstance(w, Gtk.LinkButton)]
    assert [link.get_uri() for link in links] == [option.help_url]


def test_the_dotted_key_is_selectable_so_it_can_be_taken_out_of_the_app() -> None:
    from gi.repository import Gtk

    option = SCHEMA["input:kb_layout"]
    row = build_row(option.name, FakeSession())

    key_label = next(
        widget
        for widget in _walk(row.chrome.help.get_popover().get_child())
        if isinstance(widget, Gtk.Label) and widget.get_text() == option.dotted_key
    )

    assert key_label.get_selectable()


def test_no_rows_subtitle_is_its_dotted_key(tmp_path: Path) -> None:
    """ADR-0013 §1, over every Row the app actually builds: the subtitle is the
    description and the key lives in the ⓘ."""
    from test_config_view import build_window

    _session, window = build_window(tmp_path)

    for page in window.pages:
        for row in page.rows:
            assert row.widget.get_subtitle() != row.option.dotted_key
            assert row.widget.get_subtitle() != row.option.name


def _walk(widget: Any) -> list[Any]:
    found = [widget]
    for child in children(widget):
        found.extend(_walk(child))
    return found
