"""The undo stack's own rules, with no model, no queue and no compositor near it.

`UndoStack` is deliberately dumb -- push, pop, bound -- because the interesting decision is
*when* a step is pushed, and that belongs to `Session` (see `test_session_undo.py`). What is
worth pinning down here is the one piece of judgement the stack does carry: an edit that did
not move the model is not a gesture, and spending a Ctrl+Z on it would take the user back to
a state indistinguishable from the one they are in.
"""

from __future__ import annotations

from hyprtweaker.engine.apply import Edit, EntityEdit, EntityStep, UndoStack, UndoStep
from hyprtweaker.engine.model import UNSET


def test_an_edit_that_changed_nothing_is_not_a_gesture() -> None:
    assert not Edit("decoration:rounding", 10, 10).changed
    assert Edit("decoration:rounding", 10, 12).changed


def test_unset_is_distinguishable_from_every_value_including_none() -> None:
    """The tri-state is the reason a delta is model-level rather than byte-level: Unset and
    explicit null both render as the absence of a line, and only one of them is a value."""
    assert Edit("input:kb_variant", UNSET, None).changed
    assert Edit("input:kb_variant", None, UNSET).changed
    assert not Edit("input:kb_variant", UNSET, UNSET).changed
    assert not Edit("input:kb_variant", None, None).changed


def test_a_step_drops_the_edits_that_did_not_move() -> None:
    """A gesture over four gap sides that only changed two is a two-edit step: undoing the
    other two would rewrite values the user never touched in this gesture."""
    step = UndoStep.of(
        [
            Edit("general:gaps_in", 5, 8),
            Edit("general:gaps_out", 10, 10),
            Edit("general:border_size", 1, 2),
        ]
    )

    assert step is not None
    assert step.names == ("general:gaps_in", "general:border_size")


def test_a_step_of_nothing_is_no_step_at_all() -> None:
    assert UndoStep.of([]) is None
    assert UndoStep.of([Edit("decoration:rounding", 10, 10)]) is None


def test_recording_nothing_leaves_the_stack_alone() -> None:
    """`record(None)` is accepted so the caller does not have to branch: "this transaction
    changed nothing" and "there was no transaction" are the same thing to a stack."""
    stack = UndoStack()

    stack.record(None)

    assert not stack.can_undo
    assert stack.pop() is None


def test_the_stack_is_linear_and_newest_first() -> None:
    stack = UndoStack()
    first = UndoStep.of([Edit("general:gaps_in", 5, 8)])
    second = UndoStep.of([Edit("decoration:rounding", 10, 12)])

    stack.record(first)
    stack.record(second)

    assert stack.top is second
    assert stack.pop() is second
    assert stack.pop() is first
    assert stack.pop() is None


def test_the_stack_is_bounded_and_drops_its_oldest() -> None:
    """A session left open for a week must not grow without limit, and the step furthest
    from anything the user still means to take back is the one to lose."""
    stack = UndoStack(max_depth=3)

    for value in range(6):
        stack.record(UndoStep.of([Edit("decoration:rounding", value, value + 1)]))

    assert len(stack) == 3
    assert [step.edits[0].after for step in [stack.pop(), stack.pop(), stack.pop()]] == [  # type: ignore[union-attr]
        6,
        5,
        4,
    ]


def test_clearing_empties_the_stack() -> None:
    stack = UndoStack()
    stack.record(UndoStep.of([Edit("general:gaps_in", 5, 8)]))

    stack.clear()

    assert not stack.can_undo
    assert stack.top is None


# --- entity steps -----------------------------------------------------------------------------


def test_an_entity_step_keeps_only_the_lists_that_moved() -> None:
    """A whole-list snapshot of every kind is taken around an edit; the step is the kinds
    whose list actually changed, so undoing it never rewrites a list the user left alone."""
    step = EntityStep.of(
        [
            EntityEdit("binds", ("a", "b"), ("a",)),
            EntityEdit("submaps", ("resize",), ("resize",)),
        ],
        "Bind removed",
    )

    assert step == EntityStep((EntityEdit("binds", ("a", "b"), ("a",)),), "Bind removed")
    assert step.kinds == frozenset({"binds"})


def test_an_entity_edit_that_moved_nothing_is_no_step() -> None:
    """An out-of-range delete commits and changes nothing; it must not spend a Ctrl+Z."""
    assert EntityStep.of([EntityEdit("binds", ("a",), ("a",))], "Bind removed") is None


def test_merging_entity_steps_spans_first_before_to_last_after_per_kind() -> None:
    """A kept Confirm-or-revert batch is one step, from what the user saw before the
    countdown to what they kept -- and a reverted one cancels to nothing."""
    first = EntityStep((EntityEdit("monitors", ("60Hz",), ("144Hz",)),), "Monitor rule changed")
    second = EntityStep(
        (
            EntityEdit("monitors", ("144Hz",), ("144Hz", "HDMI")),
            EntityEdit("workspace_rules", (), ("1",)),
        ),
        "Monitor rule changed",
    )
    revert = EntityStep((EntityEdit("monitors", ("144Hz", "HDMI"), ("60Hz",)),), "x")

    assert EntityStep.merge([first, second], "Display settings changed") == EntityStep(
        (
            EntityEdit("monitors", ("60Hz",), ("144Hz", "HDMI")),
            EntityEdit("workspace_rules", (), ("1",)),
        ),
        "Display settings changed",
    )
    assert EntityStep.merge([first, revert], "Monitor rule changed") is None


def test_forget_drops_every_entity_step_touching_a_kind_and_keeps_the_rest() -> None:
    """A list that changed off the stack (a foreign reload, a profile) makes every step over
    it unreplayable; Option steps and steps over other lists are untouched."""
    stack = UndoStack()
    option = UndoStep.of([Edit("general:gaps_in", 5, 8)])
    monitors = EntityStep.of([EntityEdit("monitors", (), ("m",))], "Monitor rule added")
    binds = EntityStep.of([EntityEdit("binds", (), ("b",))], "Bind added")
    both = EntityStep.of(
        [EntityEdit("binds", ("b",), ()), EntityEdit("workspace_rules", (), ("w",))], "x"
    )
    for step in (option, monitors, binds, both):
        stack.record(step)

    stack.forget({"monitors", "workspace_rules"})

    assert [stack.pop(), stack.pop(), stack.pop()] == [binds, option, None]
