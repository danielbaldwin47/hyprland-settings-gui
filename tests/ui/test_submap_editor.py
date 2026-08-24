"""UI smoke tier: the Submap editor validates and hands back its two strings (#66).

Driven directly rather than through a window: the dialog never touches the session, so
what there is to assert is the validation gate and the `on_done` contract.

Toolkit imports sit inside the test functions, as the tier's conftest requires.
"""

from __future__ import annotations

from typing import Any


def build(**kwargs: Any) -> tuple[Any, list[tuple[str, str]]]:
    from hyprtweaker.ui.dialogs.submap_editor import SubmapEditor

    done: list[tuple[str, str]] = []
    dialog = SubmapEditor(
        on_done=lambda name, reset_target: done.append((name, reset_target)), **kwargs
    )
    return dialog, done


def test_saving_hands_back_name_and_reset_target() -> None:
    dialog, done = build()
    dialog._name.set_text("resize")
    dialog._reset_target.set_text("landing")
    dialog._save()
    assert done == [("resize", "landing")]


def test_an_empty_name_is_blocked() -> None:
    dialog, done = build()
    dialog._save()
    assert done == []
    assert dialog._error.get_visible()


def test_the_name_reset_is_blocked() -> None:
    """`submap("reset")` exits -- a submap named "reset" could never be entered."""
    dialog, done = build()
    dialog._name.set_text("reset")
    dialog._save()
    assert done == []


def test_a_taken_name_is_blocked() -> None:
    dialog, done = build(taken=("resize", "move"))
    dialog._name.set_text("resize")
    dialog._save()
    assert done == []


def test_editing_keeps_its_own_name_available() -> None:
    """A rename back to the current name is an ordinary save, not a collision."""
    dialog, done = build(taken=("resize", "move"), name="resize", reset_target="")
    dialog._save()
    assert done == [("resize", "")]


def test_a_submap_cannot_reset_to_itself() -> None:
    dialog, done = build()
    dialog._name.set_text("resize")
    dialog._reset_target.set_text("resize")
    dialog._save()
    assert done == []
