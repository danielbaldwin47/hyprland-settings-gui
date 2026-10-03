"""Run the GTK main loop's turns, with a deadline: until none is ready, or until a condition.

A bare `while context.pending(): context.iteration(False)` returns only when no source is
ready at that instant, so a source that re-arms itself (a mapped window's frame clock running
behind under xdist load, an idle that queues another) pins a worker at 100% CPU with no
traceback. This wait fails instead, and says what it was waiting for.
"""

from __future__ import annotations

import time
from collections.abc import Callable

SETTLE_SECONDS = 10.0


def settle(waiting_for: str, timeout: float = SETTLE_SECONDS) -> None:
    """Run main-loop turns until none is ready; fail naming `waiting_for` after `timeout` s."""
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while context.pending():
        if time.monotonic() > deadline:
            raise AssertionError(
                f"the main loop still had work ready after {timeout:g} s "
                f"while waiting for {waiting_for}"
            )
        context.iteration(False)


def wait_until(
    predicate: Callable[[], object], waiting_for: str, timeout: float = SETTLE_SECONDS
) -> None:
    """Run turns until `predicate()` holds; fail naming `waiting_for` after `timeout` s.

    For a result another thread delivers (a worker's `GLib.idle_add`), which `settle` cannot
    wait for: `settle` returns as soon as no source is ready, while the worker is still
    running. Between turns with nothing ready it sleeps briefly, so it does not spin.
    """
    from gi.repository import GLib

    context = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"{waiting_for} did not happen within {timeout:g} s")
        if not context.iteration(False):
            time.sleep(0.005)
