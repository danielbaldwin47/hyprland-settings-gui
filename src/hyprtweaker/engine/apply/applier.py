"""The live-apply pipeline as one object: model edits in, `ApplyResult`s out.

Three collaborators that only work in one arrangement, so the arrangement lives here rather
than in every caller:

* the **transaction** owns the in-flight flag, because only it knows the window in which a
  `configreloaded` could be the app's own;
* the **queue** owns serialization and coalescing, and must be the only thing that starts a
  transaction;
* the **watch** reads the transaction's flag to tell a foreign reload from ours.

Wire them the other way round -- correlate on the queue's "a transaction is running"
instead -- and the app stops noticing a Bridge tool that reloads while it is rendering.
"""

from __future__ import annotations

from collections.abc import Callable
from types import TracebackType

from ..ipc import CommandClient, EventStream
from ..model import ConfigModel
from ..writer import Writer
from .foreign import ForeignReloadWatch
from .queue import DEBOUNCE_SECONDS, ApplyQueue
from .result import ApplyResult
from .transaction import RELOAD_TIMEOUT_SECONDS, ApplyTransaction


class Applier:
    """What the app holds for the lifetime of a session.

    Usage is two verbs. `touch` for a gesture still in progress -- a slider being dragged, a
    text entry being typed into -- and `commit` for a decided one. Everything else (one
    reload per burst, never two at once, what came back) is this object's problem::

        applier = Applier(model=model, writer=writer, client=client, events=events,
                          on_result=window.show_apply_result)
        model.set("decoration:rounding", 10)
        applier.commit("decoration:rounding")
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        writer: Writer,
        client: CommandClient,
        events: EventStream,
        on_result: Callable[[ApplyResult], None] | None = None,
        on_foreign_reload: Callable[[], None] | None = None,
        debounce: float = DEBOUNCE_SECONDS,
        reload_timeout: float = RELOAD_TIMEOUT_SECONDS,
    ) -> None:
        """`on_foreign_reload` is optional and worth passing.

        Without it the app never learns that a Bridge tool or a hand edit changed the config
        under it, and every value it shows stays whatever it last read (ADR-0010 §Foreign
        reloads). It is optional only because the re-read is the model layer's to perform,
        not because skipping it is free.
        """
        self._transaction = ApplyTransaction(
            model=model,
            writer=writer,
            client=client,
            events=events,
            reload_timeout=reload_timeout,
        )
        self._queue = ApplyQueue(self._transaction, debounce=debounce, on_result=on_result)
        self._watch = (
            ForeignReloadWatch(
                events,
                is_ours=lambda: self._transaction.in_flight,
                on_foreign_reload=on_foreign_reload,
            )
            if on_foreign_reload is not None
            else None
        )

    # --- edits ------------------------------------------------------------------------------

    def touch(self, *names: str) -> None:
        """The model changed mid-gesture: apply once the changes stop (~150 ms)."""
        self._queue.touch(*names)

    def commit(self, *names: str) -> None:
        """The model changed and the user is done deciding: apply as soon as the queue is free.

        Still coalesces -- "immediate" means skipping the debounce, never jumping the queue.
        """
        self._queue.commit(*names)

    async def apply(self, *names: str) -> ApplyResult:
        """Commit and await the transaction that carries `names`."""
        return await self._queue.apply(*names)

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        self._queue.start()

    async def drain(self) -> None:
        """Wait until every pending edit has been applied and confirmed."""
        await self._queue.drain()

    async def aclose(self) -> None:
        """Stop applying and stop watching. Safe to call twice.

        Pending edits are dropped rather than flushed -- `await drain()` first if the session
        is closing cleanly and the last edit should still land.
        """
        if self._watch is not None:
            self._watch.close()
        await self._queue.aclose()

    async def __aenter__(self) -> Applier:
        self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    @property
    def busy(self) -> bool:
        return self._queue.busy
