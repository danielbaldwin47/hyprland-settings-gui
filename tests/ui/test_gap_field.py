"""UI tier: `GapField`, the one number-or-four-sides control entity rows share (#193).

The Monitors page's Reserved area uses it first and the Workspace rule editor's gaps
(#160) after. Committed values are what both consumers write, so every test reads what
`on_commit` received after a user-shaped gesture: a spin activated, focus leaving it, the
"Same on all sides" toggle.
"""

from __future__ import annotations

from typing import Any


def build(value: Any) -> tuple[Any, list[Any]]:
    from gi.repository import Adw

    from hyprtweaker.ui.gap_field import GapField

    Adw.init()
    committed: list[Any] = []
    return GapField(value, on_commit=committed.append), committed


def type_into(spin: Any, value: int) -> None:
    """What a user does: change the number, then press Enter."""
    spin.set_value(value)
    spin.emit("activate")


def leave(spin: Any) -> None:
    """Focus leaves the spin button -- the other commit gesture."""
    from gi.repository import Gtk

    for controller in spin.observe_controllers():
        if isinstance(controller, Gtk.EventControllerFocus):
            controller.emit("leave")


def test_an_int_opens_uniform_and_commits_an_int() -> None:
    field, committed = build(8)

    assert field.uniform
    type_into(field.all_sides, 12)

    assert committed == [12]
    assert field.value == 12


def test_a_mapping_opens_per_side_and_commits_all_four_sides() -> None:
    field, committed = build({"top": 30, "right": 0, "bottom": 0, "left": 0})

    assert not field.uniform
    type_into(field.sides["left"], 4)

    assert committed == [{"top": 30, "right": 0, "bottom": 0, "left": 4}]


def test_untouched_value_is_the_original_object() -> None:
    original = {"top": 30, "right": 0, "bottom": 0, "left": 0}
    field, _committed = build(original)
    nothing, _ = build(None)

    assert field.value is original
    assert nothing.value is None
    assert nothing.uniform


def test_focus_leaving_commits_and_a_repeat_commits_nothing_new() -> None:
    field, committed = build(None)

    field.all_sides.set_value(5)
    leave(field.all_sides)
    leave(field.all_sides)  # focus came back and left again with no change

    assert committed == [5]


def test_editing_without_committing_emits_nothing() -> None:
    field, committed = build(3)

    field.all_sides.set_value(9)  # a keystroke or an arrow click, not yet committed

    assert committed == []


def test_switching_to_uniform_flattens_to_the_top_side() -> None:
    field, committed = build({"top": 30, "right": 5, "bottom": 0, "left": 0})

    field.uniform_toggle.set_active(True)

    assert committed == [30]
    assert field.all_sides.get_value() == 30


def test_switching_to_per_side_spreads_the_number_to_every_side() -> None:
    field, committed = build(6)

    field.uniform_toggle.set_active(False)

    assert committed == [{"top": 6, "right": 6, "bottom": 6, "left": 6}]


def test_bounds_clamp_what_the_user_types() -> None:
    from gi.repository import Adw

    from hyprtweaker.ui.gap_field import GapField

    Adw.init()
    committed: list[Any] = []
    field = GapField(0, on_commit=committed.append, lower=0, upper=50)

    type_into(field.all_sides, 80)

    assert committed == [50]
