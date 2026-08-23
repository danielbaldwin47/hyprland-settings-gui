"""One Apply transaction: render, gate, write, reload once, read back (ADR-0010 §Apply).

The five steps only mean anything in this order, and every one of them is there because a
simpler arrangement was measured and rejected:

1. **Render every dirty Module whole.** A patched Module is a Module whose unmentioned
   values silently revert on the next reload. `Writer.write` renders the lot.
2. **Syntax-gate before disk.** A broken `require`d module is *silently absent* while the
   rest of the config loads, so a bad byte costs the user a whole Section without saying
   so. `luac -p` turns that into `ABORTED` with nothing written.
3. **Atomic rename, every dirty Module.** An in-place write is 12 ms faster and can hand
   the compositor's watcher half a file; the rename is invisible to the watcher, which is
   also why step 4 is mandatory rather than optional.
4. **Exactly one explicit `reload`.** Reload is a full teardown and re-execute of the whole
   config; one per transaction is the entire reason the transaction exists.
5. **Confirm.** `configreloaded` is the *reload started* signal -- it fires ~11 ms before
   the new values are readable and fires just the same when the config was rejected. So
   the confirmation is `configerrors` plus a `getoption` of each touched key (**Read-back**),
   and nothing on the event stream is allowed to stand in for it.

No `eval` may run between the reload and the error read: `eval` clears `configerrors`, so a
slider preview racing a confirming transaction would erase the very errors it is waiting
for. `ApplyQueue` is what makes that ordering impossible.

Snapshotting the pre-write bytes into the Journal is also part of this transaction in
ADR-0010; the Journal itself arrives with #59, and this module grows the snapshot step then
rather than shipping a hook with nothing on the other end.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
from collections.abc import Sequence
from typing import Any

from ..ipc import (
    RELOAD_STARTED,
    CommandClient,
    EventStream,
    IpcError,
    IpcTimeout,
    MalformedReply,
    NoSuchOption,
    OptionReply,
)
from ..model import UNSET, ConfigModel, UnknownOption, parse_getoption
from ..schema import ResolvedOption
from ..writer import LuaSyntaxError, ProtectedFile, Writer, WriteResult
from .result import UNREADABLE, ApplyOutcome, ApplyResult, Mismatch

_log = logging.getLogger(__name__)

RELOAD_TIMEOUT_SECONDS = 2.0
"""ADR-0010: Hyprland's own config watchdog gives up at 1.5 s, plus margin."""

SETTLE_SECONDS = 0.25
"""How long Read-back keeps re-asking about a key that disagrees before calling it a
mismatch.

`configreloaded` fires about 11 ms *before* the new values are readable (measured, ADR-0010),
so the first `getoption` after it can legitimately still be answering with the old value. A
false mismatch is the expensive direction -- ADR-0016 wires it to auto-revert, which would
undo a change that had in fact applied -- so a disagreeing key is re-read for a short window
before it counts. An agreeing key is never re-read, which keeps the common path at one
round-trip per key."""

SETTLE_POLL_SECONDS = 0.01
"""Gap between Read-back re-reads. A round-trip is 0.4 ms, so this paces rather than costs."""

FLOAT_RELATIVE_TOLERANCE = 1e-6
"""Hyprland holds config floats as 32-bit `float`, so a `0.95` written from a Python double
reads back as the nearest float32. Comparing exactly would report a mismatch on every
fractional Option the app has ever written correctly."""

FLOAT_ABSOLUTE_TOLERANCE = 1e-9


class ApplyTransaction:
    """The whole render-write-reload-confirm cycle, reusable across applies.

    Stateful in exactly one respect: `in_flight`, which is how a `configreloaded` event gets
    told apart from somebody else's. ADR-0010 correlates by that flag rather than by content,
    because the event carries no payload to correlate on.
    """

    def __init__(
        self,
        *,
        model: ConfigModel,
        writer: Writer,
        client: CommandClient,
        events: EventStream,
        reload_timeout: float = RELOAD_TIMEOUT_SECONDS,
        settle_timeout: float = SETTLE_SECONDS,
    ) -> None:
        self._model = model
        self._writer = writer
        self._client = client
        self._events = events
        self._reload_timeout = reload_timeout
        self._settle_timeout = settle_timeout
        self._in_flight = False

    @property
    def in_flight(self) -> bool:
        """True from arming the reload wait until Read-back is done.

        Deliberately narrower than "a transaction is running": the window that matters is
        the one in which a `configreloaded` could be ours. Rendering and writing cannot
        produce one -- an atomic rename is invisible to Hyprland's watcher -- so counting
        them in would swallow a genuine foreign reload for no gain.
        """
        return self._in_flight

    async def run(
        self, keys: Sequence[str], *, overwrite_hand_edits: bool = False
    ) -> ApplyResult:
        """Apply the model, then confirm `keys` against the live compositor.

        `keys` are the Options this transaction is accountable for -- what the user touched
        since the last apply. The *write* is always the whole model (Modules are rendered
        whole); `keys` only scope the Read-back, because asking about 353 Options per
        transaction would spend 140 ms confirming values nobody changed.

        `overwrite_hand_edits` carries the user's answer to the ADR-0016 Banner back in. The
        default stands down and reports the skipped files instead.
        """
        names = tuple(keys)
        try:
            options = tuple(self._model.option(name) for name in names)
        except UnknownOption as error:
            return ApplyResult(ApplyOutcome.ABORTED, keys=names, detail=str(error))

        # Restart-flagged keys are pending the moment the bytes land, whatever the rest of
        # the transaction concludes -- the file is what "pending" is about.
        pending_restart = tuple(option.name for option in options if option.restart is not None)

        try:
            write = self._writer.write(self._model, overwrite_hand_edits=overwrite_hand_edits)
        except (LuaSyntaxError, ProtectedFile, ValueError) as error:
            # ADR-0010's guarantee: the gate runs over every rendered file before the first
            # one is replaced, so there is nothing on disk to undo here.
            _log.error("apply aborted before writing: %s", error)
            return ApplyResult(
                ApplyOutcome.ABORTED,
                keys=names,
                detail=str(error),
                pending_restart=pending_restart,
            )
        except OSError as error:
            _log.error("apply failed mid-write: %s", error)
            return ApplyResult(
                ApplyOutcome.WRITE_FAILED,
                keys=names,
                detail=str(error),
                pending_restart=pending_restart,
            )

        if not write.changed:
            # Nothing on disk moved, so the live config already says what the model says.
            # Spending a full teardown reload to reassert it is a visible stutter for nothing.
            return ApplyResult(
                ApplyOutcome.NOTHING_TO_DO,
                keys=names,
                write=write,
                pending_restart=pending_restart,
            )

        return await self._reload_and_confirm(names, options, write, pending_restart)

    # --- the compositor half ----------------------------------------------------------------

    async def _reload_and_confirm(
        self,
        names: tuple[str, ...],
        options: tuple[ResolvedOption, ...],
        write: WriteResult,
        pending_restart: tuple[str, ...],
    ) -> ApplyResult:
        def outcome(kind: ApplyOutcome, **extra: Any) -> ApplyResult:
            return ApplyResult(
                kind, keys=names, write=write, pending_restart=pending_restart, **extra
            )

        self._in_flight = True
        try:
            # Armed before the cause. A listener that subscribes after asking for the reload
            # misses the event for it, and Hyprland's stream cannot be re-armed after the
            # fact -- the wait would then always time out on a perfectly good apply.
            with self._events.arm(RELOAD_STARTED) as reloaded:
                try:
                    await self._client.reload()
                except IpcTimeout as error:
                    return outcome(ApplyOutcome.TIMEOUT, detail=str(error))
                except IpcError as error:
                    return outcome(ApplyOutcome.COMPOSITOR_GONE, detail=str(error))

                if await reloaded.wait(self._reload_timeout) is None:
                    return outcome(
                        ApplyOutcome.TIMEOUT,
                        detail=(
                            f"no {RELOAD_STARTED} within {self._reload_timeout}s; "
                            f"the Modules are on disk but may not have been applied"
                        ),
                    )

            try:
                # First, and before anything else touches the socket: the list is cleared by
                # the next reload and by any `eval`.
                errors = await self._client.configerrors()
                if errors:
                    return outcome(ApplyOutcome.CONFIG_ERRORS, errors=errors)

                mismatches = await self._read_back(options)
            except IpcTimeout as error:
                return outcome(ApplyOutcome.TIMEOUT, detail=str(error))
            except IpcError as error:
                return outcome(ApplyOutcome.COMPOSITOR_GONE, detail=str(error))
        finally:
            self._in_flight = False

        if mismatches:
            return outcome(ApplyOutcome.READ_BACK_MISMATCH, mismatches=mismatches)
        return outcome(ApplyOutcome.OK)

    async def _read_back(self, options: Sequence[ResolvedOption]) -> tuple[Mismatch, ...]:
        """Confirm each key against the live compositor, restart-flagged ones excepted.

        A restart-flagged Option cannot be confirmed at all: the value is on file and the
        running compositor will keep reporting the old one until it restarts, so reading it
        back would manufacture a mismatch on every correct write (ADR-0010 §Restart-flagged).
        """
        outstanding = [option for option in options if option.restart is None]
        if not outstanding:
            return ()

        deadline = asyncio.get_running_loop().time() + self._settle_timeout
        while True:
            found: list[Mismatch] = []
            for option in outstanding:
                mismatch = await self._compare(option)
                if mismatch is not None:
                    found.append(mismatch)

            if not found or asyncio.get_running_loop().time() >= deadline:
                return tuple(found)

            # Still settling: re-ask only about the keys that disagreed.
            outstanding = [
                option for option in outstanding if option.name in {m.name for m in found}
            ]
            await asyncio.sleep(SETTLE_POLL_SECONDS)

    async def _compare(self, option: ResolvedOption) -> Mismatch | None:
        """The model's value for one Option against the live one. `None` means they agree."""
        expected = self._model.get(option.name)
        try:
            reply = await self._client.getoption(option.name)
        except (NoSuchOption, MalformedReply) as error:
            # The running Hyprland does not have this key, or answers about it in a shape
            # this Option's parser cannot read. Both are version drift (ADR-0012) rather
            # than a value disagreement, and both leave the app with no live value to show
            # -- but neither is a reason to abandon the other keys in the transaction.
            _log.warning("read-back could not read %s: %s", option.name, error)
            return Mismatch(option.name, expected, UNREADABLE, live_set=False)

        if expected is UNSET:
            # Reset to Hyprland's default: the model emits nothing, so the live config must
            # not set it either. When it still does, something below the app in the require
            # order does -- `user.lua`, a Bridge, `legacy.lua` -- which is exactly the drift
            # the ADR-0005 badge exists to show.
            if not reply.set_by_user:
                return None
            live_override = self._live_value(option, reply)
            return Mismatch(option.name, expected, live_override, live_set=True)

        if expected is None:
            # Explicit null emits the curated `null_value` *verbatim* -- `-1` in
            # `general:float_gaps` is Hyprland's "same as the outer gaps" marker, not four
            # gaps of -1. What `getoption` reports for that marker is the compositor's own
            # interpretation and not something the app can predict, so the confirmation
            # stops at "the live config sets this key".
            if reply.set_by_user:
                return None
            return Mismatch(option.name, expected, UNREADABLE, live_set=False)

        live = self._live_value(option, reply)
        if reply.set_by_user and _matches(expected, live):
            return None
        return Mismatch(option.name, expected, live, live_set=reply.set_by_user)

    @staticmethod
    def _live_value(option: ResolvedOption, reply: OptionReply) -> Any:
        """The reply as a model value, or `UNREADABLE` if this Option's parser refused it.

        Refusing is a Hyprland-version surprise (ADR-0012), and it is reported as a
        mismatch with no live value rather than raised: the other keys in the same
        transaction are still worth confirming.
        """
        try:
            return parse_getoption(option, dict(reply.payload))
        except (KeyError, ValueError, TypeError) as error:
            _log.warning("unreadable getoption reply for %s: %s", option.name, error)
            return UNREADABLE


def _matches(expected: Any, actual: Any) -> bool:
    """Whether a live value is the value the model asked for, to float32 precision.

    Exact equality is wrong here for one boring reason and one structural one: Hyprland
    stores config floats as 32-bit, and the complex types (`Gradient`, `Vec2`, ...) carry
    floats inside frozen dataclasses where a top-level `==` would compare them exactly.
    """
    if isinstance(expected, bool) or isinstance(actual, bool):
        return bool(expected == actual)
    if isinstance(expected, int | float) and isinstance(actual, int | float):
        return math.isclose(
            expected,
            actual,
            rel_tol=FLOAT_RELATIVE_TOLERANCE,
            abs_tol=FLOAT_ABSOLUTE_TOLERANCE,
        )
    if dataclasses.is_dataclass(expected) and type(expected) is type(actual):
        return all(
            _matches(getattr(expected, item.name), getattr(actual, item.name))
            for item in dataclasses.fields(expected)
        )
    if (
        isinstance(expected, tuple | list)
        and isinstance(actual, tuple | list)
        and len(expected) == len(actual)
    ):
        return all(_matches(one, other) for one, other in zip(expected, actual, strict=True))
    return bool(expected == actual)
