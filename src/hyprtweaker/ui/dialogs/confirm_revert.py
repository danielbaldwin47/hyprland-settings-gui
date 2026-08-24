"""The Confirm-or-revert countdown for display-breaking changes (ADR-0008, #7).

The apply pattern mode/scale/transform mistakes demand: the change is already applied
when this opens, and *silence means revert*. A black screen cannot click "Keep", so the
default response, the close response, and the countdown expiring all revert -- keeping
the new settings is the one outcome that requires a deliberate, visible click.

The countdown itself is a plain `tick()` a GLib timer calls once a second, so the UI
smoke tier drives it by hand: no main loop, no waiting fifteen real seconds to see the
revert fire.
"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib  # noqa: E402

COUNTDOWN_SECONDS = 15
"""Long enough to find the pointer on a rearranged desktop, short enough that a black
screen ends itself. The #7 prototype's number."""


class ConfirmRevertDialog(Adw.AlertDialog):
    """ "Keep these display settings?" with a clock running against Keep."""

    def __init__(
        self,
        *,
        on_keep: Callable[[], None],
        on_revert: Callable[[], None],
        seconds: int = COUNTDOWN_SECONDS,
    ) -> None:
        super().__init__(
            heading="Keep these display settings?",
            body=_body(seconds),
        )
        self._on_keep = on_keep
        self._on_revert = on_revert
        self._remaining = seconds
        self._decided = False
        self._source: int | None = None

        self.add_response("revert", "Revert")
        self.add_response("keep", "Keep changes")
        self.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
        self.set_default_response("keep")
        # Esc, the window closing, the countdown -- every way out that is not the Keep
        # button is a revert. On a broken display they are the only ways out there are.
        self.set_close_response("revert")
        self.connect("response", self._on_response)

    @property
    def remaining(self) -> int:
        """Seconds left before the revert fires. What the smoke tier asserts against."""
        return self._remaining

    def present(self, parent: object = None) -> None:  # type: ignore[override]
        self._source = GLib.timeout_add_seconds(1, self.tick)
        super().present(parent)

    def tick(self) -> bool:
        """One second passed; `True` while the countdown should keep running.

        The GLib timer's callback, public so tests can run the clock by hand.
        """
        if self._decided:
            return False
        self._remaining -= 1
        if self._remaining <= 0:
            # The timer is ending itself by returning False; forgetting the source id
            # first keeps `_on_response` from removing a source that is already gone.
            self._source = None
            # `close()` fires the close response, which is "revert" -- the timeout path
            # and the Esc path deliberately share one exit.
            self.close()
            return False
        self.set_body(_body(self._remaining))
        return True

    def _on_response(self, _dialog: Adw.AlertDialog, response: str) -> None:
        if self._decided:
            return
        self._decided = True
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        if response == "keep":
            self._on_keep()
        else:
            self._on_revert()


def _body(remaining: int) -> str:
    return (
        "The new settings are applied. If this display no longer looks right -- or no "
        f"longer shows anything -- the previous settings come back in {remaining} s."
    )
