"""The serializer: one transaction in flight, everything else coalesces into the next.

Two rules from ADR-0010, and one worker task that enforces both.

**Serialized.** A reload is O(whole config) and `configerrors` is one global slot, so two
transactions in flight could not attribute an error to either of them. There is exactly one
worker; a transaction never starts while another is running.

**Coalesced.** Hyprland has no debounce of its own and each reload is a full teardown, so
the app is the coalescer. Edits arriving during a transaction pile into the next one, and a
burst of edits arriving between transactions waits ~150 ms for the burst to end.

Debounce and commit are different gestures, not different delays. Dragging a slider is a
stream of `touch` calls that must not each buy a reload; releasing it, toggling a switch or
picking from a combo is a `commit`, and a user who has finished deciding should not then
watch a progress-shaped pause (ADR-0003). So `commit` skips the wait outright rather than
shortening it.

This module knows nothing about sockets or files. Its collaborator is anything with an
`async run(keys)`, which is what lets the sequencing rules -- the ones with the races in
them -- be tested without a compositor at all.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from contextlib import suppress
from types import TracebackType
from typing import Protocol

from .result import ApplyResult

_log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.15
"""ADR-0010: ~150 ms after the last change. Long enough to swallow a keystroke burst, short
enough that a user who stops typing does not notice waiting for it."""


class Transaction(Protocol):
    """Whatever the queue serializes. `ApplyTransaction` is the one implementation.

    Named for the thing rather than for the verb: `Applies` next to `Applier` was two
    near-identical words for a protocol and the object that composes it.
    """

    async def run(self, keys: Sequence[str]) -> ApplyResult: ...


class ApplyQueue:
    """Marks Options dirty, and turns bursts of that into single Apply transactions."""

    def __init__(
        self,
        transaction: Transaction,
        *,
        debounce: float = DEBOUNCE_SECONDS,
        on_result: Callable[[ApplyResult], None] | None = None,
    ) -> None:
        """`on_result` sees every transaction, including ones nobody is awaiting.

        A debounced `touch` has no caller left to hand a result to by the time it applies,
        so the callback is the only way error surfacing (#60) hears about the apply that a
        slider drag ended in.
        """
        self._transaction = transaction
        self._debounce = debounce
        self._on_result = on_result

        self._dirty: set[str] = set()
        self._waiters: list[asyncio.Future[ApplyResult]] = []
        self._immediate = False
        self._deadline = 0.0
        self._busy = False
        self._closed = False

        self._work_available = asyncio.Event()
        self._commit_now = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        """Begin serving. Requires a running event loop; safe to call twice.

        A closed queue stays closed. `touch` starts the worker on demand, and a widget
        emitting one last focus-out during teardown would otherwise resurrect a queue the
        app has already shut down.
        """
        if self._worker is None and not self._closed:
            self._worker = asyncio.create_task(self._serve(), name="hyprtweaker-apply")

    async def aclose(self) -> None:
        """Stop serving and fail anything still waiting. Safe to call twice.

        Deliberately does *not* flush: a queue closing means the app is going away, and
        starting a reload the user will never see the result of is worse than dropping the
        edit. Callers that want the pending work applied `await drain()` first.
        """
        self._closed = True
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker

        for waiter in self._waiters:
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()
        self._idle.set()

    async def __aenter__(self) -> ApplyQueue:
        self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    # --- the two gestures -------------------------------------------------------------------

    def touch(self, *names: str) -> None:
        """Mark Options dirty and apply once the edits stop -- the continuous gesture."""
        self._mark(names, immediate=False)

    def commit(self, *names: str) -> None:
        """Mark Options dirty and apply as soon as the queue is free -- the decided gesture.

        Still coalesces: a commit landing while a transaction runs joins the next one rather
        than starting a second. "Immediate" is about the debounce, never about the lock.
        """
        self._mark(names, immediate=True)

    async def apply(self, *names: str) -> ApplyResult:
        """Commit `names` and return the result of the transaction that carries them.

        The result belongs to a *batch*, not to this call: coalescing means the returned
        `ApplyResult` may well cover other Options too. That is the honest answer -- those
        Options were written by the same reload, and their errors are indistinguishable in
        the one `configerrors` slot.
        """
        if self._closed:
            # Raised rather than dropped: a caller awaiting a result would otherwise wait
            # for a transaction that is never going to run.
            raise RuntimeError("this ApplyQueue is closed")
        waiter: asyncio.Future[ApplyResult] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        self.commit(*names)
        return await waiter

    async def drain(self) -> None:
        """Wait until nothing is pending and nothing is in flight."""
        await self._idle.wait()

    @property
    def busy(self) -> bool:
        """Whether a transaction is running right now."""
        return self._busy

    @property
    def pending(self) -> frozenset[str]:
        """Options marked dirty and not yet carried by a transaction."""
        return frozenset(self._dirty)

    # --- internals --------------------------------------------------------------------------

    def _mark(self, names: Sequence[str], *, immediate: bool) -> None:
        if self._closed:
            _log.debug("dropping edits to %s: the apply queue is closed", sorted(names))
            return
        self._dirty.update(names)
        self._deadline = self._now() + self._debounce
        if immediate:
            self._immediate = True
            # Wakes a worker already part-way through a debounce wait. Setting the flag
            # alone would not: releasing a slider after dragging it would then still wait
            # out the drag's own 150 ms, which is exactly the pause a commit gesture exists
            # to avoid.
            self._commit_now.set()
        self._idle.clear()
        self._work_available.set()
        self.start()

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()

    async def _serve(self) -> None:
        while True:
            await self._work_available.wait()

            await self._debounced()

            keys = tuple(sorted(self._dirty))
            waiters = self._waiters
            self._dirty.clear()
            self._waiters = []
            self._immediate = False
            self._commit_now.clear()
            self._work_available.clear()

            if not keys:
                self._settle()
                continue

            await self._run_once(keys, waiters)

    async def _debounced(self) -> None:
        """Wait out the quiet period, or return at once if a commit gesture arrived.

        The deadline moves: every `_mark` pushes it out, so a burst of edits is one wait
        rather than one wait per edit. The wait is on an event rather than a plain sleep so
        that a commit landing mid-burst ends it immediately.
        """
        while not self._immediate and (delay := self._deadline - self._now()) > 0:
            with suppress(TimeoutError):
                async with asyncio.timeout(delay):
                    await self._commit_now.wait()

    async def _run_once(
        self, keys: tuple[str, ...], waiters: list[asyncio.Future[ApplyResult]]
    ) -> None:
        self._busy = True
        try:
            result = await self._transaction.run(keys)
        except asyncio.CancelledError:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
            raise
        except Exception as error:
            # The worker is the app's only apply path; letting it die would silently retire
            # instant apply for the rest of the session. The callers who were waiting hear
            # the exception; everyone else hears it in the log.
            _log.exception("apply transaction raised")
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(error)
            self._settle()
            return
        finally:
            self._busy = False

        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(result)
        self._notify(result)
        # Last, so a `drain()` that returns has already seen every subscriber run.
        self._settle()

    def _settle(self) -> None:
        """Report idle unless an edit arrived while the last transaction was running."""
        if not self._dirty:
            self._idle.set()

    def _notify(self, result: ApplyResult) -> None:
        if self._on_result is None:
            return
        try:
            self._on_result(result)
        except Exception:
            # Same reasoning as the exception guard above: a subscriber's bug must not cost
            # the session its apply path.
            _log.exception("apply result subscriber failed")
