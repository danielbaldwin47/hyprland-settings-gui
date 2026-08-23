"""The shape of a continuous gesture, without a widget to drag.

ADR-0010 splits a drag into a stream of previews and exactly one Apply transaction, and
`Gesture` is the boundary between the two. Three ways to get that wrong, and each one is a
user-visible failure rather than a tidiness issue:

* commit per tick and the drag is a write-storm -- one full compositor teardown per tick,
  which is the whole reason the preview tier exists;
* never commit and the value dies at the next reload, having looked applied the whole time;
* commit on somebody else's reload and the user's half-chosen value gets written by an event
  they had nothing to do with.

Toolkit-free, so all three are one list-append away from being asserted.
"""

from __future__ import annotations

from typing import Any

from hyprtweaker.ui.rows.gesture import Gesture


def make() -> tuple[Gesture, list[Any], list[Any]]:
    previewed: list[Any] = []
    committed: list[Any] = []
    gesture = Gesture(
        preview=previewed.append,
        commit=committed.append,
    )
    return gesture, previewed, committed


def test_every_tick_previews_and_none_of_them_commits() -> None:
    gesture, previewed, committed = make()

    for angle in (10, 20, 30):
        gesture.tick(angle)

    assert previewed == [10, 20, 30]
    assert committed == [], "a tick that wrote would be one compositor teardown per tick"


def test_the_release_commits_the_value_the_drag_stopped_on_exactly_once() -> None:
    gesture, _previewed, committed = make()

    gesture.tick(10)
    gesture.tick(45)
    gesture.end()

    assert committed == [45]


def test_ending_twice_writes_once() -> None:
    """A Row wires `end` to the pointer, the keyboard and focus, precisely so that no way of
    leaving the control can strand a preview. That overlap has to be free."""
    gesture, _previewed, committed = make()

    gesture.tick(45)
    gesture.end()
    gesture.end()

    assert committed == [45]


def test_ending_without_a_tick_writes_nothing() -> None:
    """Clicking a slider without moving it, or tabbing through the Row, must not apply a
    value the user never chose."""
    gesture, previewed, committed = make()

    gesture.end()

    assert previewed == []
    assert committed == []


def test_abandoning_drops_the_value_instead_of_writing_it() -> None:
    """What a foreign reload leaves behind: the preview is already gone from the compositor,
    and the model is about to be re-read. Committing here would turn somebody else's reload
    into a write the user never asked for."""
    gesture, _previewed, committed = make()

    gesture.tick(45)
    gesture.abandon()
    gesture.end()

    assert committed == []
    assert not gesture.active


def test_a_gesture_can_start_again_after_being_abandoned() -> None:
    """The pointer may well still be down: abandoning is about the value, not the pointer."""
    gesture, _previewed, committed = make()

    gesture.tick(45)
    gesture.abandon()
    gesture.tick(90)
    gesture.end()

    assert committed == [90]
