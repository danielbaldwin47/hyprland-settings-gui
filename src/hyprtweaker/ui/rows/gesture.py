"""One continuous gesture over one Option: preview per tick, one transaction on release.

ADR-0010 splits a drag in two. Every tick is an Eval preview -- Hyprland's own parser, no
file touched, sub-frame -- and the release is a single normal Apply transaction. What this
class owns is the boundary between them, which is the part that is easy to get wrong in
three separate ways:

* a gesture that commits per tick is the write-storm the preview tier exists to prevent, so
  `end` commits **once** and only if a tick actually happened;
* a gesture that never ends leaves a value previewed and never written -- transient state
  that dies at the next reload -- so `end` is wired to every way a drag can finish and is
  idempotent, which is what lets a Row connect it to four different signals without
  counting them;
* a gesture the compositor pulled the rug from under must not then write the half-chosen
  value: a foreign reload wipes eval state *and* triggers a full re-read, so `abandon` drops
  the gesture and lets the re-read put the truth back in the model.

Toolkit-free on purpose. The sequencing is where the bugs are, and none of it needs a
widget: `tests/unit/test_ui_row_gesture.py` drives it with two lists.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class Gesture:
    """The open/tick/end life of one drag, over one Option.

    Not per widget: a Row rebuilds its editor as the value changes shape (a gradient gains a
    stop) and the gesture has to outlive that, because the pointer is still down.
    """

    def __init__(
        self,
        name: str,
        *,
        preview: Callable[[str, Any], None],
        commit: Callable[[str, Any], None],
    ) -> None:
        self._name = name
        self._preview = preview
        self._commit = commit
        self._active = False
        self._value: Any = None

    @property
    def active(self) -> bool:
        """Whether a tick has happened that no `end` or `abandon` has answered yet."""
        return self._active

    def tick(self, value: Any) -> None:
        """The control moved. Preview the new value; do not write it."""
        self._active = True
        self._value = value
        self._preview(self._name, value)

    def end(self) -> None:
        """The gesture finished: write the value it stopped on, exactly once.

        A no-op when no tick has happened, which is the common case -- the pointer-release
        and focus-leave handlers a Row wires this to fire on plenty of interactions that
        never moved the control at all, and committing on those would apply a value the user
        did not choose to a Row they merely clicked on.
        """
        if not self._active:
            return
        self._active = False
        self._commit(self._name, self._value)

    def abandon(self) -> None:
        """Drop the gesture without writing. The model is about to be told the truth.

        What a reload leaves behind: the preview is gone from the compositor either way, and
        the value the user was dragging towards was never on disk. Committing it here would
        turn somebody else's reload into a write the user never asked for.
        """
        self._active = False
