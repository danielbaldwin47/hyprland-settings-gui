"""The Eval preview tier: a continuous gesture's per-tick echo (ADR-0010 §Eval preview).

A slider drag is dozens of edits a second. Each one as an Apply transaction would be dozens
of full teardown reloads, so a gesture *in progress* goes over the socket instead: one
`eval hl.config{...}` per tick -- 0.4 ms, Hyprland's own parser, no file touched. The
durable write is the single Apply transaction the release commits, and this tier never
stands in for it.

Three rules, all ADR-0010's, and each one is a way to get this wrong:

* **Never while a transaction is confirming.** `eval` clears `configerrors`, so a preview
  landing between a reload and its error read erases the very errors that transaction is
  waiting for -- and the transaction then reports a clean apply of a config Hyprland
  rejected. The guard is a predicate the caller supplies rather than a flag kept here,
  because the apply queue is the only thing that knows.
* **Latest wins.** Ticks arrive faster than round trips. Queueing them would replay a drag
  the user has already finished; sending only the newest pending Option keeps the preview
  at most one round trip behind the pointer, which is what "sub-frame" means in practice.
* **Transient, and never authoritative.** Eval state dies at the next reload and the model
  is what survives, so a preview that fails is logged and dropped rather than surfaced: the
  value the user actually chose still reaches the compositor through the Apply transaction
  the gesture ends in, and *that* result is the one the user hears about.

Deliberately not here: any notion of "the preview is showing X". A reload -- ours, a Bridge
tool's, a hand edit's -- wipes eval state without telling anyone, so a cache of what the
compositor is previewing would be a claim this module cannot keep true. `forget()` is the
whole of the state management, and it only drops work that has not been sent yet.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress

from ..ipc import CommandClient, IpcError
from ..model import UNSET, ConfigModel, UnknownOption, lua_literal_for
from ..schema import ResolvedOption
from ..writer import LuaTree, insert, render_table_inline

_log = logging.getLogger(__name__)


def preview_code(option: ResolvedOption, value: object) -> str:
    """The `eval` body that makes one Option read as `value`, without writing anything.

    The same tree the Writer builds for a Module, one Option wide and printed on one line:
    `hl.config` merges per leaf, so setting a single key changes that key and nothing else
    (research `lua-api-surface.md` §1). The leaf literal comes from `lua_literal_for`, which
    is the same call the Module render makes -- a preview that spelled a gradient
    differently from the file that follows it would be previewing a different value.
    """
    tree: LuaTree = {}
    insert(tree, option.path, lua_literal_for(option, value))
    return f"hl.config{render_table_inline(tree)}"


class EvalPreview:
    """Echoes the model's value for one Option to the live compositor, best-effort.

    One worker, one pending Option, no queue: the point of the tier is to be *behind* the
    gesture by at most a round trip, and everything older than the newest tick is a value
    the user has already moved past.
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        client: CommandClient,
        is_blocked: Callable[[], bool],
    ) -> None:
        """`is_blocked` answers "would an eval right now step on an Apply transaction?".

        Required, and a callable rather than a value, because the answer changes between the
        moment a tick is recorded and the moment the worker gets to it -- which is exactly
        the window a drag against a running transaction lives in.
        """
        self._model = model
        self._client = client
        self._is_blocked = is_blocked

        self._pending: str | None = None
        self._last_code: str | None = None
        self._supported = True
        self._closed = False

        self._work = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._worker: asyncio.Task[None] | None = None

    # --- what the Applier calls ---------------------------------------------------------

    def preview(self, name: str) -> None:
        """One tick: show whatever the model now holds for `name`. Returns immediately.

        Fire-and-forget on purpose. A tick that cannot be sent -- closed, a transaction in
        the way, a session that refuses `eval` at all -- is dropped rather than deferred:
        the user is still dragging, and the value that matters is the one they stop on.
        """
        if self._closed or not self._supported:
            return
        self._pending = name
        self._idle.clear()
        self._work.set()
        self.start()

    def forget(self) -> None:
        """Drop any tick not yet sent -- the compositor reloaded and wiped eval state.

        Not a cancellation of anything in flight: a request already on the socket will be
        answered whatever happens here. It is the un-sent tick that has gone stale, because
        the model behind it is about to be re-read from the compositor (`Session`).
        """
        self._pending = None
        self._last_code = None
        self._settle()

    @property
    def supported(self) -> bool:
        """Whether this session evaluates Lua at all.

        False once a session has answered `UNSUPPORTED_EVAL`: it is running the hyprlang
        config manager, so no code the app sends will ever work and every later tick is a
        wasted round trip. The Options still apply -- through the file write on release --
        they just stop previewing (ADR-0009's Migration wizard is the real answer).
        """
        return self._supported

    @property
    def last_code(self) -> str | None:
        """The most recent `eval` body actually sent. The tier's one testable trace."""
        return self._last_code

    # --- lifecycle ------------------------------------------------------------------------

    def start(self) -> None:
        """Begin serving. Requires a running event loop; safe to call twice."""
        if self._worker is None and not self._closed:
            self._worker = asyncio.create_task(self._serve(), name="hyprtweaker-preview")

    async def flush(self) -> None:
        """Wait until nothing is pending and nothing is in flight."""
        await self._idle.wait()

    async def aclose(self) -> None:
        """Stop previewing. Safe to call twice.

        Pending ticks are dropped rather than sent: a preview whose window is closing has
        nobody left to show anything to, and the Apply queue's `drain` is what makes sure
        the *durable* half of the last gesture still lands.
        """
        self._closed = True
        self._pending = None
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
        self._idle.set()

    # --- internals ------------------------------------------------------------------------

    async def _serve(self) -> None:
        while True:
            await self._work.wait()
            self._work.clear()
            name, self._pending = self._pending, None

            if name is not None:
                await self._send(name)
            self._settle()

    async def _send(self, name: str) -> None:
        if self._is_blocked():
            # ADR-0010: no eval between a reload and its Read-back. Dropped rather than
            # deferred -- by the time the transaction is done this tick is stale, and the
            # next one is already on its way.
            _log.debug("skipping preview of %s: an apply transaction is running", name)
            return

        code = self._code(name)
        if code is None:
            return

        try:
            reply = await self._client.eval(code)
        except IpcError as error:
            # Best-effort by design: the release's Apply transaction is what the user is
            # promised, and it reports its own failures through `ApplyResult`.
            _log.debug("preview of %s did not reach Hyprland: %s", name, error)
            return

        self._last_code = code
        if reply.unsupported:
            _log.info("previews are off: this session runs the hyprlang config manager")
            self._supported = False
        elif not reply.ok:
            _log.debug("Hyprland refused the preview of %s: %s", name, reply.text.strip())

    def _code(self, name: str) -> str | None:
        """The `eval` body for one Option's *current* model value, or `None` for no preview.

        Read at send time rather than captured at tick time: between the two the user has
        very likely moved the slider again, and the newer value is the one worth a round
        trip.

        An Unset Option previews nothing. `eval` can set a value but has no way to say
        "stop setting this" -- only a reload re-reads the config from scratch -- so the
        honest answer is to leave it to the Apply transaction, which does exactly that.
        """
        try:
            option = self._model.option(name)
        except UnknownOption:
            return None

        value = self._model.get(name)
        if value is UNSET:
            return None
        return preview_code(option, value)

    def _settle(self) -> None:
        if self._pending is None:
            self._idle.set()
