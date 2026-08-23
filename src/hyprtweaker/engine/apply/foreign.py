"""Noticing that somebody else reloaded the config (ADR-0010 §Foreign reloads).

`configreloaded` carries no payload, so there is nothing in the event to say who caused it.
ADR-0010 therefore correlates by **in-flight flag**: a reload arriving while a transaction is
confirming is that transaction's; any other is somebody else's -- a Bridge tool rewriting
colours, a hand edit saved in an editor, a `hyprctl reload` from a script -- and the app's
whole view of the config is now a guess. The answer is a full state re-read plus a drift
scan, which is the caller's job; this object only says *when*.

The known cost of correlating by flag rather than by content: a foreign reload that lands
inside the app's own confirm window is attributed to the app and no re-read happens. That is
the ADR's call, and the alternative -- treating our own reload as foreign -- would re-read
the whole config after every single apply.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from types import TracebackType

from ..ipc import RELOAD_STARTED, Event, EventStream, Unsubscribe

_log = logging.getLogger(__name__)


class ForeignReloadWatch:
    """Calls back on every `configreloaded` the app did not cause."""

    def __init__(
        self,
        events: EventStream,
        *,
        is_ours: Callable[[], bool],
        on_foreign_reload: Callable[[], None],
    ) -> None:
        """`on_foreign_reload` must return immediately.

        It runs on the event stream's dispatch path, and Hyprland drops a listener that lets
        64 events queue up -- so a re-read done *inside* the callback would cost the session
        its event stream. Schedule the work; do not do it here.
        """
        self._is_ours = is_ours
        self._on_foreign_reload = on_foreign_reload
        self._unsubscribe: Unsubscribe | None = events.subscribe(self._seen, RELOAD_STARTED)

    def _seen(self, _event: Event) -> None:
        if self._is_ours():
            return
        _log.info("config reloaded by something other than this app; re-reading state")
        self._on_foreign_reload()

    def close(self) -> None:
        """Stop watching. Idempotent."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def __enter__(self) -> ForeignReloadWatch:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
