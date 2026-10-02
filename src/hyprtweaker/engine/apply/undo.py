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

**An Entity step holds whole lists** (#189, deciding #104 coarse). Binds and rules have no key
to address them by -- position *is* identity (ADR-0007, ADR-0008) -- so the delta of an entity
edit is the edited kind's whole list before and after, as tuples of frozen entities. Those are
pointer arrays over objects every snapshot shares, so a step costs a few hundred pointers, not
copies of the rules. A list that changed off the stack (a foreign reload adopted a hand edit, a
profile was activated) makes every step over it unreplayable, and `UndoStack.forget` drops them.

There is no redo tier in v1, which is what makes ADR-0016's "the failed gesture never becomes
a redo" true by construction rather than by a rule somebody has to remember. The stack is
bounded for the same reason every in-memory history is: a session left open for a week is a
session whose undo depth nobody is ever going to walk.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..model import UNSET, OptionValue

UNDO_MAX_DEPTH = 200
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
    coalesce into one Apply transaction, and so does applying a Preset (`PresetStep`).
    Undoing half of a gesture would leave a state the user never chose.
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


@dataclass(frozen=True, slots=True)
class EntityEdit:
    """One Entity list before and after a gesture, whole.

    `kind` is the `EntitySet` list name (`binds`, `monitors`, `window_rules`, ...). Whole
    lists rather than an index-level diff, because a replay of "insert at 3" is only right
    while nothing else has moved -- a whole list is right or visibly stale.
    """

    kind: str
    before: tuple[Any, ...]
    after: tuple[Any, ...]

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class EntityStep:
    """One entity gesture: every list it moved, and the words the undo toast uses for it.

    The title travels with the step because an entity has no Schema title to look up: the
    session knows whether the gesture was an add, a remove or a reorder, and the window,
    which only sees the lists, could not tell a removal from a reorder without diffing them.
    """

    edits: tuple[EntityEdit, ...]
    title: str

    @classmethod
    def of(cls, edits: Iterable[EntityEdit], title: str) -> EntityStep | None:
        """A step from `edits`, or `None` when none of them moved a list."""
        moved = tuple(edit for edit in edits if edit.changed)
        return cls(moved, title) if moved else None

    @classmethod
    def merge(cls, steps: Iterable[EntityStep], title: str) -> EntityStep | None:
        """Several steps as one: per kind, the first `before` to the last `after`.

        What an undo group closes into. A batch that was kept is one gesture; a batch that
        was reverted ends where it began, and `of` turns that into no step at all.
        """
        before: dict[str, tuple[Any, ...]] = {}
        after: dict[str, tuple[Any, ...]] = {}
        for step in steps:
            for edit in step.edits:
                before.setdefault(edit.kind, edit.before)
                after[edit.kind] = edit.after
        return cls.of((EntityEdit(kind, before[kind], after[kind]) for kind in before), title)

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(edit.kind for edit in self.edits)


@dataclass(frozen=True, slots=True)
class PresetStep:
    """Applying a Preset: every Option it changed, under the Preset's name (ADR-0014).

    Its own variant rather than a title on `UndoStep`, because the undo toast names the
    Preset ("Applied Nord") where an Option step names a Row, and because undoing a Preset
    is meant to put back everything the apply changed -- Options here; the Color source and
    the wallpaper join it with #170.
    """

    name: str
    options: UndoStep

    @classmethod
    def of(cls, name: str, options: UndoStep | None) -> PresetStep | None:
        """A step, or `None` when the Preset changed nothing (it matched the current look)."""
        return cls(name, options) if options is not None else None


Step = UndoStep | EntityStep | PresetStep
"""Anything on the one stack. Option and entity gestures interleave in the order they landed."""


@dataclass(eq=False, slots=True)
class UndoGroup:
    """Entity steps held back to become one step, or none (`Session.begin_undo_group`).

    A handle with state, not a stack operation: the session fills `held` as the group's
    commits come back, and merges it once the group is ended and nothing it holds is still
    in flight. Confirm-or-revert is the caller: a kept countdown is one gesture, and a
    reverted one ends where it started and records nothing.
    """

    kinds: frozenset[str]
    held: list[EntityStep] = field(default_factory=list)
    failed: bool = False
    """A held commit's transaction failed: the batch never stood whole, so nothing is pushed."""
    title: str | None = None
    """Set by `end_undo_group`; `None` while the group is still open."""
    finished: bool = False


class UndoStack:
    """A linear in-memory stack of `Step`s. One per session, global across Pages."""

    def __init__(self, *, max_depth: int = UNDO_MAX_DEPTH) -> None:
        self._steps: list[Step] = []
        self._max_depth = max(1, max_depth)

    def __len__(self) -> int:
        return len(self._steps)

    @property
    def can_undo(self) -> bool:
        return bool(self._steps)

    @property
    def top(self) -> Step | None:
        """The step a `pop` would return, without taking it.

        What the undo toast names, so the toast and the keystroke cannot come to disagree
        about which gesture "the last one" is.
        """
        return self._steps[-1] if self._steps else None

    def record(self, step: Step | None) -> None:
        """Push a gesture. `None` is accepted and ignored -- see `UndoStep.of`."""
        if step is None:
            return
        self._steps.append(step)
        if len(self._steps) > self._max_depth:
            del self._steps[0 : len(self._steps) - self._max_depth]

    def pop(self) -> Step | None:
        """Take the newest gesture off the stack, or `None` when there is nothing to undo."""
        return self._steps.pop() if self._steps else None

    def forget(self, kinds: Collection[str]) -> None:
        """Drop every entity step touching any of `kinds`; Option steps stay.

        For a list that changed off the stack: replaying a step over it would overwrite
        the change with a list from before it, which is a hand edit lost to Ctrl+Z.
        """
        gone = frozenset(kinds)
        self._steps = [
            step
            for step in self._steps
            if not (isinstance(step, EntityStep) and step.kinds & gone)
        ]

    def clear(self) -> None:
        self._steps.clear()
