"""Running the engine's asyncio work on the GTK main loop.

The engine's IPC is async because a transaction waiting on a reload must not stop the event
stream from draining (ADR-0010). GTK's loop is GLib's. PyGObject ≥ 3.50 reconciles the two
directly: `gi.events.GLibEventLoopPolicy` hands out an `asyncio` event loop that *is* the
GLib main context, so `await` works inside a running `Gtk.Application` with no second thread
anywhere.

That is the whole reason this module is eleven lines of work rather than a threaded bridge.
One thread means the model has exactly one owner: `Applier` renders the same `ConfigModel`
object a Row is editing, and on a worker thread that is a `dict changed size during
iteration` waiting to happen -- plus every engine callback would need marshalling back to
the widgets. Here they simply arrive on the thread that owns them.

Older PyGObject has no `gi.events`. Rather than grow a second, differently-behaved code
path for it, the app degrades honestly: no asyncio integration means no live apply, which
is exactly the read-only session a machine with no compositor gets, and the Banner says so.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

_log = logging.getLogger(__name__)

MINIMUM_PYGOBJECT = "3.50"
"""The release that added `gi.events`. Only live apply depends on it; the Config view
builds and reads correctly without it."""


class MainLoopRunner:
    """Schedules coroutines on the GTK main loop, or explains why it cannot."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unavailable_reason: str | None = None
        self._pending: set[asyncio.Task[None]] = set()

        try:
            import gi.events
        except ImportError as error:  # pragma: no cover - depends on the installed PyGObject
            self._unavailable_reason = (
                f"PyGObject {MINIMUM_PYGOBJECT} or newer is needed to apply changes ({error})"
            )
            return

        # The policy caches one loop per GLib main context, so constructing it here as many
        # times as there are windows still yields the one loop the app runs on.
        loop = gi.events.GLibEventLoopPolicy().get_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

    @property
    def available(self) -> bool:
        return self._loop is not None

    @property
    def unavailable_reason(self) -> str | None:
        return self._unavailable_reason

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        """Start `coro` on the main loop. Fire-and-forget; failures are logged, not raised.

        A reference is kept until the task finishes: `asyncio` holds only a weak one, and a
        garbage-collected task is a re-read or an apply that silently never happened.
        """
        if self._loop is None:
            coro.close()
            return

        task = self._loop.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._pending.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            _log.exception("background task failed", exc_info=error)
