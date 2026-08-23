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

Snapshotting the pre-write bytes into the Journal is step 0, and it brackets the other five:
the Snapshot has to be taken while the previous bytes still exist (before step 3) and the
entry cannot be written until the transaction has an outcome to record (after step 5). What
it buys is stated in two ADRs: ADR-0010's auto-revert restores the *pre-write* bytes, and
ADR-0016's Last known good is the newest bytes whose transaction **confirmed** -- so the
entry carries both digests and the `confirmed` flag that separates "nothing went wrong" from
"everything was checked".

A transaction without a Journal still works, and every test that is not about history runs
that way. History is not config: an app that refused to apply an edit because it could not
write to the state dir would be the tail wagging the dog.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
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
from ..model import UNSET, ConfigModel, UnknownOption, values_match
from ..paths import ENTRYPOINT_NAME
from ..schema import ResolvedOption
from ..state import Draft, Journal
from ..writer import LuaSyntaxError, ProtectedFile, Writer, WriteResult, module_relpath
from .result import UNREADABLE, ApplyOutcome, ApplyResult, Mismatch, live_value

_log = logging.getLogger(__name__)

RELOAD_TIMEOUT_SECONDS = 2.0
"""ADR-0010: Hyprland's own config watchdog gives up at 1.5 s, plus margin."""

SETTLE_SECONDS = 0.25
"""How long Read-back keeps re-asking about a key that disagrees before calling it a
mismatch.

`configreloaded` fires about 11 ms *before* the new values are readable (measured, ADR-0010),
so the first `getoption` after it can legitimately still be answering with the old value.

A false mismatch is the expensive direction. ADR-0016 badges an unexplained one on the Row
as "didn't apply" and raises the Banner, so the user is told a correct change failed; and
the transaction never counts as `confirmed`, so its Snapshot never becomes the Module's
last-known-good. A disagreeing key is therefore re-read for a short window before it counts.
An agreeing key is never re-read, which keeps the common path at one round-trip per key."""

SETTLE_POLL_SECONDS = 0.01
"""Gap between Read-back re-reads. A round-trip is 0.4 ms, so this paces rather than costs."""


@dataclass(frozen=True, slots=True)
class ReloadReport:
    """What one explicit reload produced, before anyone decides what it means.

    Deliberately not an `ApplyResult`: a reload is a step, and the same step ends an Apply
    transaction and a Restore. Handing back the raw findings lets each caller reach its own
    verdict -- an apply goes on to Read-back its keys, a restore goes on to re-read the model
    -- without either having to unpick an outcome the other decided.
    """

    failed: ApplyOutcome | None = None
    """`TIMEOUT` or `COMPOSITOR_GONE` when the reload never happened; `None` when it did."""

    errors: tuple[str, ...] = ()
    binds: int | None = None
    """Bind count, probed only when `errors` is non-empty. `None` means never asked."""

    detail: str = ""

    @property
    def ok(self) -> bool:
        """The reload happened and Hyprland reported nothing wrong with the config."""
        return self.failed is None and not self.errors


class Reloader:
    """One explicit `reload`, plus the two questions that always follow it.

    Shared by every path that reloads -- the Apply transaction and ADR-0016's Restore -- for
    a reason that is correctness rather than tidiness. `in_flight` is how a `configreloaded`
    event is told apart from somebody else's (ADR-0010 correlates by the flag, because the
    event carries no payload). A restore that reloaded behind its own flag would have its own
    reload read as a *foreign* one, triggering the full re-read-and-drift-scan against a
    config the app was in the middle of putting back.

    So there is one flag, and it belongs to the thing that does the reloading.
    """

    def __init__(
        self,
        *,
        client: CommandClient,
        events: EventStream,
        timeout: float = RELOAD_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._events = events
        self._timeout = timeout
        self._in_flight = False

    @property
    def in_flight(self) -> bool:
        """True from arming the reload wait until the caller has finished confirming.

        Deliberately narrower than "a transaction is running": the window that matters is
        the one in which a `configreloaded` could be ours. Rendering and writing cannot
        produce one -- an atomic rename is invisible to Hyprland's watcher -- so counting
        them in would swallow a genuine foreign reload for no gain.
        """
        return self._in_flight

    def confirming(self) -> _Confirming:
        """Hold `in_flight` open across the caller's own confirmation step."""
        return _Confirming(self)

    def set_in_flight(self, value: bool) -> None:
        """Raise or drop the correlation flag. `confirming()` is the way to call this."""
        self._in_flight = value

    async def reload(self) -> ReloadReport:
        """Reload once and report what the config now says. Never raises.

        Assumes the caller is already inside `confirming()`: the flag has to stay up past
        the `configerrors` read, and only the caller knows when its own confirmation is done.
        """
        # Armed before the cause. A listener that subscribes after asking for the reload
        # misses the event for it, and Hyprland's stream cannot be re-armed after the fact --
        # the wait would then always time out on a perfectly good apply.
        with self._events.arm(RELOAD_STARTED) as reloaded:
            try:
                await self._client.reload()
            except IpcTimeout as error:
                return ReloadReport(failed=ApplyOutcome.TIMEOUT, detail=str(error))
            except IpcError as error:
                return ReloadReport(failed=ApplyOutcome.COMPOSITOR_GONE, detail=str(error))

            if await reloaded.wait(self._timeout) is None:
                return ReloadReport(
                    failed=ApplyOutcome.TIMEOUT,
                    detail=(
                        f"no {RELOAD_STARTED} within {self._timeout}s; "
                        f"the Modules are on disk but may not have been applied"
                    ),
                )

        try:
            # First, and before anything else touches the socket: the list is cleared by the
            # next reload and by any `eval`.
            errors = await self._client.configerrors()
        except IpcTimeout as error:
            return ReloadReport(failed=ApplyOutcome.TIMEOUT, detail=str(error))
        except IpcError as error:
            return ReloadReport(failed=ApplyOutcome.COMPOSITOR_GONE, detail=str(error))

        # Only when something is wrong, and never on the clean path: the probe is one more
        # round trip, and the only question it answers -- is the user stranded without
        # keybinds? -- cannot arise from a reload that parsed.
        return ReloadReport(errors=errors, binds=await self._bind_count() if errors else None)

    async def _bind_count(self) -> int | None:
        """How many binds the broken config still declares, or `None` if it would not say.

        Never allowed to change the outcome. The errors are already in hand and are the
        thing worth reporting; a probe that failed and took the whole result down with it
        would replace a precise "Hyprland rejected this, here are the lines" with a vague
        "the compositor stopped responding". `None` reads as "not asked", which is what keeps
        the emergency restore from firing on a probe that never answered.
        """
        try:
            return await self._client.bind_count()
        except IpcError as error:
            _log.warning("could not count binds after a failed reload: %s", error)
            return None


class _Confirming:
    """`in_flight` held up for the length of a `with` block."""

    def __init__(self, reloader: Reloader) -> None:
        self._reloader = reloader

    def __enter__(self) -> _Confirming:
        self._reloader.set_in_flight(True)
        return self

    def __exit__(self, *_: Any) -> None:
        self._reloader.set_in_flight(False)


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
        journal: Journal | None = None,
        reload_timeout: float = RELOAD_TIMEOUT_SECONDS,
        settle_timeout: float = SETTLE_SECONDS,
        reloader: Reloader | None = None,
    ) -> None:
        """`reloader` is shared with the Restore path when there is one (see `Reloader`)."""
        self._model = model
        self._writer = writer
        self._client = client
        self._events = events
        self._journal = journal
        self._settle_timeout = settle_timeout
        self._reloader = reloader or Reloader(
            client=client, events=events, timeout=reload_timeout
        )

    @property
    def reloader(self) -> Reloader:
        """The reload step, so a Restore can run behind the same in-flight flag."""
        return self._reloader

    @property
    def in_flight(self) -> bool:
        """True from arming the reload wait until Read-back is done."""
        return self._reloader.in_flight

    async def run(self, keys: Sequence[str]) -> ApplyResult:
        """Apply the model, then confirm `keys` against the live compositor.

        `keys` are the Options this transaction is accountable for -- what the user touched
        since the last apply. The *write* is always the whole model (Modules are rendered
        whole); `keys` only scope the Read-back, because asking about 353 Options per
        transaction would spend 140 ms confirming values nobody changed.

        Hand-edited Modules are left alone and reported on `result.skipped`, never
        overwritten. What the user may then do about it is the recovery matrix's
        (`recovery.py`), raised on the Banner.
        """
        names = tuple(keys)
        try:
            options = tuple(self._model.option(name) for name in names)
        except UnknownOption as error:
            return ApplyResult(ApplyOutcome.ABORTED, keys=names, detail=str(error))

        draft = self._open_draft()
        try:
            write = self._writer.write(self._model)
        except (LuaSyntaxError, ProtectedFile, ValueError) as error:
            # ADR-0010's guarantee: the gate runs over every rendered file before the first
            # one is replaced, so there is nothing on disk to undo -- and therefore nothing
            # to journal either.
            _log.error("apply aborted before writing: %s", error)
            self._discard(draft)
            return ApplyResult(ApplyOutcome.ABORTED, keys=names, detail=str(error))
        except OSError as error:
            # The one path with no `WriteResult` to read: the App dir may be half-updated, so
            # the Journal records what the disk says changed rather than what was intended.
            _log.error("apply failed mid-write: %s", error)
            result = ApplyResult(ApplyOutcome.WRITE_FAILED, keys=names, detail=str(error))
            self._record(draft, result, draft.dirty() if draft is not None else ())
            return result

        if not write.changed:
            # Nothing on disk moved, so the live config already says what the model says.
            # Spending a full teardown reload to reassert it is a visible stutter for nothing.
            # Nothing became newly pending either: a restart-flagged value that is already
            # on file was reported pending by the transaction that put it there.
            self._discard(draft)
            return ApplyResult(ApplyOutcome.NOTHING_TO_DO, keys=names, write=write)

        result = await self._reload_and_confirm(
            names, options, write, self._pending_restart(options, write)
        )
        self._record(draft, result, (*write.written, *write.removed))
        return result

    # --- the Journal half ---------------------------------------------------------------------

    def _open_draft(self) -> Draft | None:
        """Snapshot every file this write could touch, before it touches any of them."""
        if self._journal is None:
            return None
        return self._journal.begin(self._writer.candidate_files(self._model))

    def _record(self, draft: Draft | None, result: ApplyResult, changed: Sequence[str]) -> None:
        """Journal what happened, once the outcome -- and therefore `confirmed` -- is known.

        `changed` is the Modules whose bytes moved; the Entrypoint joins them when it was
        rewritten, because the Module set changing is a change to the config as much as a
        value is, and a rollback that restored Modules to a state the Entrypoint no longer
        requires would restore nothing the compositor reads.

        The Options each Module carries are recorded alongside its digest, from the same
        model walk that rendered it. That is what makes a Snapshot restorable rather than
        merely readable: Restore last good has to put the model back too, and the app cannot
        learn what a Module sets by reading it (#62).
        """
        if draft is None:
            return
        names = list(changed)
        if result.write is not None and result.write.entrypoint_written:
            names.append(ENTRYPOINT_NAME)
        draft.commit(
            keys=result.keys,
            outcome=str(result.outcome),
            confirmed=result.confirmed,
            changed=names,
            options=self._writer.module_options(self._model),
        )

    @staticmethod
    def _discard(draft: Draft | None) -> None:
        if draft is not None:
            draft.discard()

    @staticmethod
    def _pending_restart(
        options: Sequence[ResolvedOption], write: WriteResult
    ) -> tuple[str, ...]:
        """Restart-flagged keys whose bytes this transaction actually laid down.

        Pending restart is a statement about a *file* -- "applied to file, effective after
        Hyprland restart" (CONTEXT.md) -- so it is only ever claimed after the write landed.
        Badging a Row "takes effect after Hyprland restart" for a change that was aborted,
        or for one whose Module the Writer stood down from because an editor had touched it,
        promises the user a restart will produce a setting they never wrote.
        """
        return tuple(
            option.name
            for option in options
            if option.restart is not None and module_relpath(option) not in write.skipped
        )

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

        with self._reloader.confirming():
            report = await self._reloader.reload()
            if report.failed is not None:
                return outcome(report.failed, detail=report.detail)
            if report.errors:
                return outcome(
                    ApplyOutcome.CONFIG_ERRORS, errors=report.errors, binds=report.binds
                )

            try:
                mismatches, unconfirmed = await self._read_back(options)
            except IpcTimeout as error:
                return outcome(ApplyOutcome.TIMEOUT, detail=str(error))
            except IpcError as error:
                return outcome(ApplyOutcome.COMPOSITOR_GONE, detail=str(error))

        if mismatches:
            return outcome(
                ApplyOutcome.READ_BACK_MISMATCH,
                mismatches=mismatches,
                unconfirmed=unconfirmed,
            )
        # Unconfirmed keys leave the outcome OK on purpose: nothing is known to be wrong,
        # and ADR-0016 reverts on mismatch.
        return outcome(ApplyOutcome.OK, unconfirmed=unconfirmed)

    async def _read_back(
        self, options: Sequence[ResolvedOption]
    ) -> tuple[tuple[Mismatch, ...], tuple[str, ...]]:
        """What disagreed with the model, and what could not be asked about at all.

        A restart-flagged Option is not confirmed at all: the value is on file and the
        running compositor will keep reporting the old one until it restarts, so reading it
        back would manufacture a mismatch on every correct write (ADR-0010 §Restart-flagged).

        Keys the compositor will not answer usefully about come back as `unconfirmed` rather
        than as mismatches -- see `ApplyResult.unconfirmed` for why the difference matters.
        """
        outstanding = [option for option in options if option.restart is None]
        if not outstanding:
            return (), ()

        unconfirmed: dict[str, None] = {}
        deadline = asyncio.get_running_loop().time() + self._settle_timeout
        while True:
            found: list[Mismatch] = []
            again: list[ResolvedOption] = []
            for option in outstanding:
                try:
                    reply = await self._client.getoption(option.name)
                    mismatch = self._compare(option, reply)
                except MalformedReply as error:
                    # Not a reply at all: 0.56.2 answers `invalid type (internal error)` for
                    # both font-weight Options. No evidence either way about what the config
                    # holds -- and unlike a value still settling, it will not un-break, so
                    # this key is not asked again.
                    _log.warning("read-back could not read %s: %s", option.name, error)
                    unconfirmed[option.name] = None
                    continue
                except NoSuchOption as error:
                    # Retried, unlike the above: the key can be transiently absent while the
                    # reload is still re-registering options, which is the same skew the
                    # settle window exists for. Still absent at the deadline means the
                    # running Hyprland genuinely lacks it (ADR-0012 version drift).
                    _log.warning("read-back could not read %s: %s", option.name, error)
                    unconfirmed[option.name] = None
                    again.append(option)
                    continue

                # It answered, so whatever an earlier round could not read, it can now.
                unconfirmed.pop(option.name, None)
                if mismatch is not None:
                    found.append(mismatch)
                    again.append(option)

            if not again or asyncio.get_running_loop().time() >= deadline:
                return tuple(found), tuple(unconfirmed)

            # Still settling: re-ask only about the keys that did not settle.
            outstanding = again
            await asyncio.sleep(SETTLE_POLL_SECONDS)

    def _compare(self, option: ResolvedOption, reply: OptionReply) -> Mismatch | None:
        """The model's value for one Option against the live one. `None` means they agree.

        Raises `MalformedReply` when the live config sets the key and the reply about it
        cannot be read: that is the caller's "unconfirmed" branch, and routing it through the
        exception the command client already raises for the same condition keeps one path
        instead of two.
        """
        expected = self._model.get(option.name)

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
        if live is UNREADABLE and reply.set_by_user:
            # The live config sets the key and the reply about it is unreadable, so there is
            # nothing to disagree with. Calling that a mismatch would badge the Row "didn't
            # apply" for a write that did.
            raise MalformedReply(
                f"getoption {option.name} answered nothing readable as {option.type}"
            )
        if reply.set_by_user and values_match(expected, live):
            return None
        return Mismatch(option.name, expected, live, live_set=reply.set_by_user)

    @staticmethod
    def _live_value(option: ResolvedOption, reply: OptionReply) -> Any:
        """The reply as a model value, or `UNREADABLE` if this Option's parser refused it.

        What a caller does with `UNREADABLE` depends on what was being asked: for a key the
        model no longer sets, "the live config sets *something* here" is the whole finding
        and the unreadable value only costs the badge its detail; for a key the model does
        set, there is nothing left to compare and the key is unconfirmed.
        """
        return live_value(option, dict(reply.payload))
