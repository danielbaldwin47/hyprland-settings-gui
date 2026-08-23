"""The in-session undo stack: one gesture, one model delta, replayed through Apply.

ADR-0010 §Undo settles three things this module is the shape of.

**A step is a model delta, not a file diff.** "Byte-level file undo rejected -- it fights the
tri-state model." Unset, explicit null and set-to-a-value are three different states, and two
of them render as the *absence* of a line: a byte-level undo of "reset to default" cannot
tell the value it removed from a value that was never there, while `Edit(name, before=10,
after=UNSET)` says exactly what happened and reverses without ambiguity.

**A step is a gesture, not an edit.** "A whole slider drag is one step: value-at-press ->
value-at-release." Fifty ticks of a drag are fifty model writes and one thing the user did,
and an undo stack that recorded the fifty would need fifty Ctrl+Z to get back to where the
pointer went down. What draws the boundary is not this module -- it is the Apply transaction,
because coalescing has *already* decided which edits were one burst (see `Session`).

**It dies with the session.** "The Journal remains the durable history but is not walkable as
undo." A cross-session stack would have to survive a compositor the user reconfigured by hand
in between, and the value it would restore might no longer mean anything.

There is no redo tier in v1, which is what makes ADR-0016's "the failed gesture never becomes
a redo" true by construction rather than by a rule somebody has to remember. The stack is
bounded for the same reason every in-memory history is: a session left open for a week is a
session whose undo depth nobody is ever going to walk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..model import UNSET, OptionValue

MAX_DEPTH = 200
"""How many gestures the stack remembers. Deep enough to walk back a whole sitting; bounded
so a long-lived session cannot grow without limit. Overflow drops the *oldest* step, which is
the one furthest from anything the user still means to take back."""


@dataclass(frozen=True, slots=True)
class Edit:
    """One Option's value before and after a gesture.

    Both sides are full model values -- a value, `None` for explicit null, or `UNSET` -- so
    reversing is `apply(before)` rather than a rule about which of the three states a
    particular reversal happens to land in.
    """

    name: str
    before: OptionValue
    after: OptionValue

    @property
    def changed(self) -> bool:
        """Whether this edit moved the model at all.

        `UNSET` is a singleton and every model value compares by value, so identity is not
        the question -- an edit that set 10 over 10 is nothing to undo, and keeping it would
        spend a Ctrl+Z on a step the user cannot see happen.
        """
        if self.before is UNSET or self.after is UNSET:
            return self.before is not self.after
        return bool(self.before != self.after)


@dataclass(frozen=True, slots=True)
class UndoStep:
    """One user gesture, as everything it changed.

    Plural because one gesture is not always one Option: the css-gaps editor's four spinners
    coalesce into one Apply transaction, and so will applying a Preset (#69). Undoing half of
    a gesture would leave a state the user never chose.
    """

    edits: tuple[Edit, ...]

    @classmethod
    def of(cls, edits: Sequence[Edit]) -> UndoStep | None:
        """A step from `edits`, or `None` when none of them moved the model."""
        moved = tuple(edit for edit in edits if edit.changed)
        return cls(moved) if moved else None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(edit.name for edit in self.edits)


class UndoStack:
    """A linear in-memory stack of `UndoStep`s. One per session, global across Pages."""

    def __init__(self, *, max_depth: int = MAX_DEPTH) -> None:
        self._steps: list[UndoStep] = []
        self._max_depth = max(1, max_depth)

    def __len__(self) -> int:
        return len(self._steps)

    @property
    def can_undo(self) -> bool:
        return bool(self._steps)

    @property
    def top(self) -> UndoStep | None:
        """The step a `pop` would return, without taking it.

        What the undo toast names, so the toast and the keystroke cannot come to disagree
        about which gesture "the last one" is.
        """
        return self._steps[-1] if self._steps else None

    def record(self, step: UndoStep | None) -> None:
        """Push a gesture. `None` is accepted and ignored -- see `UndoStep.of`."""
        if step is None:
            return
        self._steps.append(step)
        if len(self._steps) > self._max_depth:
            del self._steps[0 : len(self._steps) - self._max_depth]

    def pop(self) -> UndoStep | None:
        """Take the newest gesture off the stack, or `None` when there is nothing to undo."""
        return self._steps.pop() if self._steps else None

    def clear(self) -> None:
        self._steps.clear()
