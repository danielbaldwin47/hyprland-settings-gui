"""One run of the app: the Schema, the model, and the live connection behind them.

The window builds widgets; this holds everything they are a view of. Splitting it out is
what lets the whole edit-to-compositor path be tested without a display -- `tests/unit`
drives a `Session` against a scripted socket, and `tests/integration` drives the same object
against a nested Hyprland.

**Toolkit-free on purpose.** Nothing here imports `gi`. Coroutines are handed to a `spawn`
callable the caller supplies, and everything the UI must react to arrives as a plain Python
callback -- the same seam the engine already draws for `EventStream` (ADR-0011).

**Live or read-only, never a third thing.** Instant apply is the whole interaction model
(ADR-0003): there is no Apply button because a change *is* a write plus a reload. With no
compositor to reload there is nothing to make a change mean, and writing anyway would leave
values on disk the next launch cannot read back -- the app would open showing defaults over
a config that says otherwise. So a session with no Hyprland is read-only and says so.

**The transaction draws the gesture boundary, not the widget.** Undo steps are recorded when
a transaction *finishes*, from the values each Option held when its first edit since the last
transaction arrived. That is one decision doing three jobs. A slider drag -- fifty previews
and one commit -- becomes one step spanning press to release, because only the release ends
in a transaction. Four css-gaps spinners typed into in one breath become one step, because
the queue's debounce already coalesced them into one reload, and a gesture the *compositor*
saw as one change is one change. And a gesture that fails becomes no step at all: it is never
pushed, so ADR-0016's "drop the failed gesture from the undo stack (it never becomes a redo)"
holds by construction rather than by remembering to pop.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hyprtweaker.engine.apply import (
    Applier,
    ApplyOutcome,
    ApplyResult,
    Edit,
    ReRead,
    UndoStack,
    UndoStep,
    app_owned_options,
    own_write_modules,
    read_state,
)
from hyprtweaker.engine.ipc import (
    CommandClient,
    EventStream,
    Instance,
    IpcError,
    NoInstance,
)
from hyprtweaker.engine.model import UNSET, ConfigModel, OptionValue
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import ResolvedOption, Schema, load_schema
from hyprtweaker.engine.state import Journal, Manifest, content_hash
from hyprtweaker.engine.writer import Writer

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutoRevert:
    """One automatic recovery from the app's own rejected write (ADR-0016 §Auto-revert).

    Reported rather than merely logged, because instant apply has no cancel: the user watched
    a change land in the UI and it is being taken away again, so something has to say so. The
    error lines ride along verbatim so the toast's **Details** action has something to show
    without going back to the compositor -- `configerrors` is cleared by the next reload, and
    the restore transaction *is* the next reload.
    """

    keys: tuple[str, ...]
    """The Options whose values were put back."""

    modules: tuple[str, ...]
    """The App-dir Modules the errors blamed on this transaction's own write."""

    errors: tuple[str, ...]
    """The `configerrors` lines, `file:line` prefixes intact."""

    restored: bool = True
    """Whether the Modules came back byte-for-byte as their pre-write Snapshot.

    False is the escalation ADR-0016 names -- "if the restore transaction itself errors ...
    escalate to the Banner and stop auto-writing until the user acts". The toast still says
    what was attempted; what it must not do is claim a recovery that did not happen."""


Spawn = Callable[[Coroutine[Any, Any, None]], None]
"""How this session gets a coroutine running. The GTK app passes the main loop's own
scheduler, so engine callbacks land on the thread that owns the widgets."""

_NOT_CONNECTED_YET = "Connecting to Hyprland…"
"""The reason a session is read-only between construction and `start()` finishing.

A reason rather than a fourth state: the Rows are genuinely not editable yet, and a Banner
that says why is better than one that appears a moment later."""


class Session:
    """The app's state for one run, from schema load to a clean shutdown."""

    def __init__(
        self,
        *,
        spawn: Spawn,
        schema: Schema | None = None,
        paths: ConfigPaths | None = None,
        app_version: str,
        connect: Callable[[], Instance] = Instance.current,
    ) -> None:
        """`connect` names the compositor to talk to; by default, the one we run under.

        Injected for the same reason the Harness can drive the real `Applier`: an `Instance`
        is a frozen dataclass over a socket directory, so a nested Hyprland or a scripted
        pair of sockets is a first-class session and needs no monkeypatching.
        """
        self._spawn = spawn
        self._schema = schema if schema is not None else load_schema()
        self._paths = paths if paths is not None else ConfigPaths.default()
        self._app_version = app_version
        self._connect = connect

        self.on_state_changed: Callable[[], None] | None = None
        """Called when the model or the connection changed under the UI's feet -- the
        startup re-read, a foreign reload, a compositor that went away. The window's cue to
        make every control agree with the model again.

        Assigned after construction rather than injected, because the window it talks to
        needs this session to exist before *it* can."""

        self.on_applied: Callable[[ApplyResult], None] | None = None
        """Called once per finished Apply transaction, successful or not.

        Not called for a transaction that is being auto-reverted: that one is reported
        through `on_reverted` instead, so the user gets the one toast that says what happened
        rather than a failure toast chased by a recovery toast."""

        self.on_reverted: Callable[[AutoRevert], None] | None = None
        """Called when the app has just taken back its own rejected write (ADR-0016)."""

        self._model = ConfigModel(self._schema)
        self._writer = Writer(self._paths, app_version=app_version)
        self._journal = Journal(self._paths)

        self._events: EventStream | None = None
        self._client: CommandClient | None = None
        self._applier: Applier | None = None
        self._offline_reason: str | None = _NOT_CONNECTED_YET
        self._closing = False
        self._pending_restart: set[str] = set()

        self._undo = UndoStack()
        self._open: dict[str, OptionValue] = {}
        """Per Option, what it held when the current gesture's first edit arrived.

        Opened by any edit that finds no entry, closed by the transaction that carries the
        key. Between those two moments the Option is mid-gesture, however many model writes
        the widget makes -- which is what turns fifty slider ticks into one undo step."""

        self._reverting = False
        self._recovery_halted = False

    # --- what the UI reads ------------------------------------------------------------------

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def model(self) -> ConfigModel:
        return self._model

    @property
    def journal(self) -> Journal:
        """Snapshots and the change log for this install (ADR-0010 §Rollback, ADR-0016).

        Held by the session rather than built per transaction, so "what is this Module's Last
        known good?" has one answer whoever asks -- auto-revert now, the Banner's
        Restore-last-good in #60.
        """
        return self._journal

    @property
    def live(self) -> bool:
        """Whether edits reach a running compositor. False means the Rows are read-only."""
        return self._offline_reason is None

    @property
    def offline_reason(self) -> str | None:
        """Why this session is read-only, in one line fit for the Banner."""
        return self._offline_reason

    @property
    def pending_restart(self) -> frozenset[str]:
        """Options this session wrote that need a restart before they take effect.

        "Applied to file, effective after Hyprland restart" (`CONTEXT.md`), which is a claim
        about a *file*: only keys an Apply transaction actually laid down get in here, never
        keys that were merely edited and refused. The Row badges from this (ADR-0013).

        Accumulated for the life of the session and never cleared, because the event that
        would clear it -- Hyprland restarting -- takes the session with it: the event stream
        drops and the session goes read-only. A `hyprctl reload` is *not* that event, and
        forgetting on one would tell the user a pending change had landed when it had not.
        """
        return frozenset(self._pending_restart)

    def value_of(self, option: ResolvedOption) -> OptionValue:
        """The model's value: a value, `None` for explicit null, or `UNSET`."""
        return self._model.get(option.name)

    def effective_value(self, option: ResolvedOption) -> Any:
        """What Hyprland is currently doing: the set value, else its own default.

        An Unset Option is not blank -- the compositor applies its default, and a control
        that renders empty would state that the setting has no value (prototype #8's
        blank-row defect).

        Not the same question as "what should the Row display", which is
        `ui/rows/state.shown_value` and differs in one deliberate way: this answers `None`
        or a sentinel-shaped value verbatim, because a dependency asking "is the controlling
        Option set to `custom`?" wants the raw comparison. A control asking what to *show*
        wants "Device default", and folding those two into one function would make one of
        them lie.
        """
        value = self._model.get(option.name)
        return option.default if value is UNSET else value

    def is_modified(self, option: ResolvedOption) -> bool:
        """Whether the model emits this Option at all -- ADR-0005's tri-state, not `!=`."""
        return self._model.is_set(option.name)

    # --- what the UI writes -----------------------------------------------------------------

    def set_option(self, name: str, value: Any) -> None:
        """A decided edit: into the model, then applied as soon as the queue is free."""
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def unset_option(self, name: str) -> None:
        """Reset to Hyprland's default: stop emitting the Option (ADR-0013 §6)."""
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.unset(name)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def touch_option(self, name: str, value: Any) -> None:
        """A mid-gesture edit that still writes -- a spin button being typed into.

        Applied once the changes stop (~150 ms). The discrete half of "mid-gesture": a
        keystroke burst is a handful of edits, so coalescing them into one transaction is
        enough and the value is durable the moment the user stops. A *continuous* gesture is
        `preview_option`, because fifty ticks a second is fifty reloads however well they
        coalesce.
        """
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.touch(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def preview_option(self, name: str, value: Any) -> None:
        """One tick of a continuous gesture: into the model, echoed over the socket, unwritten.

        The Eval preview tier (ADR-0010). Nothing reaches disk and no reload is issued -- the
        value is shown by `eval`, which is sub-frame and transient. What makes it durable is
        the single Apply transaction the gesture's release commits, through `set_option`.

        Between the tick and the release the model is deliberately ahead of the file, and
        that window is honest rather than a gap: the model is what the app is *about* to
        write, the Row shows it, and a reload arriving mid-drag wipes the preview and drags
        the model back to the truth through the foreign-reload re-read. Nothing else in the
        app has to know a gesture is in progress.
        """
        if self._refuse(name):
            return
        self._begin_edit(name)
        self._model.set(name, value)
        self._applier.preview(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def _begin_edit(self, name: str) -> None:
        """Remember what this Option held before the current gesture started.

        Idempotent per gesture, which is the whole mechanism: the *first* edit records the
        value at press, every later one finds an entry and leaves it alone, and the
        transaction that carries the key takes it (`_close`). So the delta an undo step is
        built from is press-to-release rather than tick-to-tick, with nothing in the widget
        layer having to know when a gesture began.
        """
        if name not in self._open:
            self._open[name] = self._model.get(name)

    def _refuse(self, name: str) -> bool:
        """Whether this session must decline an edit -- and leave the model alone doing it.

        The model is the app's claim about what the config says. On a read-only session
        nothing is written, so accepting the edit would leave the model holding a value that
        exists nowhere else: the Row would show it, a later re-read would not clear it (the
        compositor never had it), and a session that regained a compositor would write it
        without the user asking again. Declining keeps the model and the config in step.

        Unreachable from the UI, which makes every control insensitive while read-only. It
        is an invariant rather than a guard for that reason -- worth stating so no later
        caller has to rediscover it.
        """
        if self.live and self._applier is not None:
            return False
        _log.debug("read-only session: refusing the edit to %s", name)
        return True

    # --- undo -------------------------------------------------------------------------------

    @property
    def can_undo(self) -> bool:
        """Whether there is a gesture to take back, and a live session to take it back in."""
        return self.live and self._undo.can_undo

    @property
    def last_gesture(self) -> UndoStep | None:
        """The gesture `undo` would reverse, without reversing it.

        What the undo toast names its button after, so the toast and Ctrl+Z cannot come to
        disagree about which gesture "the last one" is.
        """
        return self._undo.top

    def undo(self) -> bool:
        """Take back the last gesture through a normal Apply transaction. `False` if none.

        A *normal* transaction, not a special path: the values go into the model exactly as a
        user edit would put them there and the queue renders, gates, writes and confirms them
        the same way (ADR-0010 §Undo). An undo that wrote files directly would be a second
        way for bytes to reach the App dir, with its own bugs and its own failure modes, for
        a gesture that is by definition already expressible as a model delta.

        Undoing does not push a step of its own. There is no redo tier in v1, and a stack
        that recorded its own reversals would turn Ctrl+Z pressed twice into a value
        oscillating between two states rather than walking back through history.
        """
        if not self.live or self._applier is None:
            return False
        step = self._undo.pop()
        if step is None:
            return False

        self._restore({edit.name: edit.before for edit in step.edits})
        self._applier.commit(*step.names)
        self._changed()
        return True

    def _restore(self, values: Mapping[str, OptionValue]) -> None:
        """Put the model back to `values`, and forget any gesture open on those Options.

        The forgetting matters: an Option mid-gesture has an entry in `_open` holding a
        pre-gesture value that the restore has just made wrong, and leaving it there would
        have the *next* transaction record an undo step spanning both.
        """
        for name, value in values.items():
            self._open.pop(name, None)
            if value is UNSET:
                self._model.unset(name)
            else:
                self._model.set(name, value)

    # --- lifecycle --------------------------------------------------------------------------

    def start(self) -> None:
        """Connect, recover the model, and begin applying. Returns immediately."""
        self._spawn(self._go_live())

    def set_read_only(self, reason: str) -> None:
        """Declare the session read-only for a reason it could not discover itself.

        The app knows one such reason: without PyGObject's asyncio integration there is no
        loop to run a transaction on, so no amount of connecting would help. Saying so up
        front beats leaving the Banner on "Connecting to Hyprland…" forever.
        """
        self._go_offline(reason)
        self._changed()

    async def _go_live(self) -> None:
        try:
            instance = self._connect()
        except NoInstance as error:
            self.set_read_only(str(error))
            return

        events = EventStream(instance, on_lost=self._on_stream_lost)
        client = CommandClient(instance)
        try:
            await events.start()
            await self._recover(client)
        except IpcError as error:
            await events.aclose()
            self.set_read_only(f"{instance.command_socket} is not answering: {error}")
            return

        self._events = events
        self._client = client
        self._applier = Applier(
            model=self._model,
            writer=self._writer,
            client=client,
            events=events,
            journal=self._journal,
            on_foreign_reload=self._on_foreign_reload,
            on_result=self._applied,
        )
        self._applier.start()
        self._offline_reason = None
        self._changed()

    def _owned(self) -> tuple[ResolvedOption, ...]:
        """Exactly the Options the Manifest records this app as having written."""
        manifest = Manifest.load(
            self._paths.manifest,
            app_version=self._app_version,
            schema_version=self._schema.hyprland_version,
        )
        return app_owned_options(self._schema, manifest)

    async def _recover(self, client: CommandClient) -> ReRead:
        """Re-read the Options this app already wrote (ADR-0010, `reread.py`).

        The app cannot yet parse its own Lua back (that reader is #62), so what it wrote
        last session is recovered from the compositor that loaded it. An install that has
        written nothing owns nothing, adopts nothing, and opens Unset.
        """
        owned = self._owned()
        result = await read_state(self._model, client, owned)
        _log.info(
            "recovered %d option(s) from %d owned; %d unreadable, %d unknown",
            len(result.adopted),
            len(owned),
            len(result.unreadable),
            len(result.unknown),
        )
        return result

    async def drain(self) -> None:
        """Wait until every pending edit has been applied and confirmed.

        The seam `aclose` uses to keep an edit made in the last moment before the window
        closes, and the one anything driving a session without a user has to wait on -- the
        integration Harness, and every test that asserts about what a change *did*. Returns
        at once on a session that never connected, because there is then nothing in flight
        and never will be.
        """
        if self._applier is not None:
            await self._applier.drain()

    async def aclose(self) -> None:
        """Flush pending edits, then drop the connection. Safe to call twice.

        `drain` before `aclose` on purpose: an edit made in the last moments before the
        window closes is still inside the apply debounce, and dropping it would lose a
        change the user watched land in the UI.
        """
        self._closing = True
        self._client = None
        applier, self._applier = self._applier, None
        if applier is not None:
            try:
                await applier.drain()
            except IpcError as error:
                _log.warning("could not flush pending edits on close: %s", error)
            await applier.aclose()

        events, self._events = self._events, None
        if events is not None:
            await events.aclose()

    def close(self, done: Callable[[], None]) -> None:
        """Shut down on the main loop and call `done` when there is nothing left running.

        `done` is called even when there is nothing to shut down, and that is not a detail:
        the window holds itself open until it arrives. A session that never connected has no
        coroutine to run -- and on a machine with no asyncio integration at all, `spawn` is a
        no-op -- so routing that case through the loop would leave a window that cannot be
        closed.
        """
        if self._applier is None and self._events is None:
            self._closing = True
            done()
            return

        async def run() -> None:
            try:
                await self.aclose()
            finally:
                done()

        self._spawn(run())

    # --- what the compositor tells us -------------------------------------------------------

    def _on_foreign_reload(self) -> None:
        """Somebody else reloaded the config: everything the model holds may be stale.

        ADR-0010 requires a *full* re-read here rather than a merge: the model's values
        describe a config that has just been replaced, so re-reading only the keys that look
        wrong would keep whichever of them the new config silently dropped. What a re-read
        cannot revise is an Option the app deliberately set to null -- no reply spells "no
        value" -- so those keep their state and surface an override as a drift badge (#57).

        Attributing any `configerrors` the foreign reload produced, and raising the Banner
        for them (ADR-0016 §Surfacing), is #60's -- this re-read is the state half only.
        """
        if self._closing:
            return
        # A reload rebuilds the Lua VM, so every Eval preview this session ever sent is
        # already gone (ADR-0010). Dropping the un-sent tick with them is what keeps the
        # re-read below from being immediately contradicted by a preview of the value it
        # just replaced.
        if self._applier is not None:
            self._applier.forget_previews()
        # Every open gesture is void: the values it started from describe a config that has
        # just been replaced, and the re-read below is about to overwrite the model they
        # would have been measured against. The *stack* survives -- a recorded step is a
        # model delta and replays through a normal transaction whatever else has happened
        # since (ADR-0010 §Undo) -- but a half-open one would produce a delta spanning
        # somebody else's reload.
        self._open.clear()
        self._spawn(self._reread_after_foreign_reload())

    async def _reread_after_foreign_reload(self) -> None:
        client = self._client
        if client is None:
            return
        # Both halves, because they answer different failures: the model's own keys catch a
        # value the new config changed or dropped, and the owned set catches a key a hand
        # edit *added* to one of the app's Modules -- which the next Apply would otherwise
        # re-render without, silently undoing it (ADR-0016 ownership class 2).
        wanted = {option.name for option, _ in self._model.set_options()}
        wanted.update(option.name for option in self._owned())
        stale = tuple(option for option in self._schema.options if option.name in wanted)
        try:
            result = await read_state(self._model, client, stale)
        except IpcError as error:
            self.set_read_only(f"lost contact with Hyprland: {error}")
            return
        _log.info(
            "foreign reload: %d option(s) re-read, %d no longer set",
            len(result.adopted),
            len(result.cleared),
        )
        self._changed()

    def _on_stream_lost(self) -> None:
        """Hyprland closed the event stream -- it has exited."""
        self.set_read_only("Hyprland is no longer running")

    def _applied(self, result: ApplyResult) -> None:
        """One transaction finished: close its gestures, then recover or record.

        The order is the whole design. Closing first turns "which Options were mid-gesture?"
        into a concrete delta; that delta is then either the thing to *undo automatically*
        (Hyprland rejected our own write) or the thing to *remember* (it stands, so Ctrl+Z
        should be able to take it back). A gesture can never be both, which is why the failed
        one is never pushed rather than pushed and popped.
        """
        if self._reverting:
            # The restore transaction's own result. It carries no gesture of the user's, and
            # a second auto-revert on top of a failed one is the loop ADR-0016 forbids.
            #
            # An edit made in the ~25 ms the restore takes would coalesce into it, and its
            # gesture is deliberately *not* closed here: the entry stays open in `_open`, so
            # the next transaction records it with the value it really started from rather
            # than with the one the revert put back.
            self._recovery_result(result)
            self._report(result)
            return

        delta = self._close(result.keys)
        blamed = self._own_write_errors(result)
        if blamed and delta:
            self._auto_revert(result, delta, blamed)
            return
        if blamed:
            # Our own write, rejected, with nothing recorded to put back -- which the app
            # cannot reach by editing, only by writing without an edit. Reverting blind would
            # mean re-rendering the same model into the same bad bytes, so this reports and
            # stops. Raising the Banner for it is #60's.
            _log.error("own write rejected with no model delta to revert: %s", result.errors)
            self._recovery_halted = True

        self._undo.record(self._step(delta))
        self._report(result)

    def _report(self, result: ApplyResult) -> None:
        self._pending_restart.update(result.pending_restart)
        if self.on_applied is not None:
            self.on_applied(result)

    def _close(self, keys: Sequence[str]) -> dict[str, OptionValue]:
        """Take the pre-gesture values of every Option this transaction carried.

        Scoped to `keys` rather than draining `_open`, because coalescing is per key: a
        transaction confirms exactly the Options it was handed, and an edit that arrived
        while it was in flight belongs to the *next* one and is still mid-gesture.
        """
        return {name: self._open.pop(name) for name in keys if name in self._open}

    def _step(self, delta: Mapping[str, OptionValue]) -> UndoStep | None:
        return UndoStep.of(
            [Edit(name, before, self._model.get(name)) for name, before in delta.items()]
        )

    def _own_write_errors(self, result: ApplyResult) -> tuple[str, ...]:
        """The Modules this transaction wrote that Hyprland then complained about.

        Narrow on purpose (ADR-0016 §Attribution): an error in `user.lua`, in a Bridge
        module, or in an app Module this write did not touch is somebody else's to fix, and
        answering it with an automatic write would be the app overwriting a file on the
        strength of an error it did not cause.
        """
        if result.outcome is not ApplyOutcome.CONFIG_ERRORS:
            return ()
        return own_write_modules(result.errors, written=result.written)

    # --- auto-revert (ADR-0016) ---------------------------------------------------------------

    def _auto_revert(
        self,
        result: ApplyResult,
        delta: Mapping[str, OptionValue],
        modules: Sequence[str],
    ) -> None:
        """Put the model back and re-apply it, so the file goes back with it.

        The model *is* the restore. Modules are rendered whole and deterministically
        (ADR-0010), so a model returned to its pre-transaction values renders byte-for-byte
        the Snapshot this transaction replaced -- which is why step 1 and step 2 of ADR-0016's
        auto-revert are one act here rather than two writes racing each other. The Snapshot
        is still what makes it *checkable*, and `_verify` checks it.

        No confirmation, per the ADR: instant apply has no cancel, and the bytes being
        restored were live and confirmed moments ago.
        """
        _log.warning("Hyprland rejected our own write to %s; reverting", ", ".join(modules))
        # Read before re-applying: right now the newest Journal entry for each Module is the
        # failed write, so its `before` digest is the Snapshot the revert has to reproduce.
        # A moment later the revert's own entry is newest, and its `before` is the bad bytes.
        expected = self._snapshot_digests(modules)
        self._restore(delta)
        self._changed()
        self._spawn(self._revert_transaction(result, tuple(delta), expected))

    def _snapshot_digests(self, modules: Sequence[str]) -> dict[str, str | None]:
        """Each Module's pre-write Snapshot digest, from the newest entry that touched it."""
        entries = self._journal.entries()
        digests: dict[str, str | None] = {}
        for module in modules:
            digests[module] = None
            for entry in reversed(entries):
                change = entry.change(module)
                if change is not None:
                    digests[module] = change.before
                    break
        return digests

    async def _revert_transaction(
        self, result: ApplyResult, keys: tuple[str, ...], expected: Mapping[str, str | None]
    ) -> None:
        applier = self._applier
        if applier is None:
            return

        self._reverting = True
        try:
            await applier.apply(*keys)
        except (IpcError, RuntimeError) as error:
            _log.error("could not re-apply after reverting: %s", error)
            self._recovery_halted = True
        finally:
            self._reverting = False

        restored = self._verify(expected)
        if self.on_reverted is not None:
            self.on_reverted(
                AutoRevert(
                    keys=keys,
                    modules=tuple(expected),
                    errors=result.errors,
                    restored=restored and not self._recovery_halted,
                )
            )
        self._changed()

    def _verify(self, expected: Mapping[str, str | None]) -> bool:
        """Whether each reverted Module is now byte-for-byte its pre-write Snapshot.

        Checked rather than assumed, because the assumption is load-bearing: auto-revert
        restores the file *by re-rendering the model*, which is only the same thing as
        restoring the Snapshot for as long as rendering is deterministic and the model was
        put back completely. A disagreement means it was not -- schema drift after a Hyprland
        upgrade is the case ADR-0016 names -- and the honest answer is to say the recovery
        did not complete and stop auto-writing, rather than to toast "reverted" over a config
        that is still broken.

        A Module whose Snapshot digest is `None` did not exist before the write; the revert
        should have deleted it again, and a file still there is a failure like any other.
        """
        for module, digest in expected.items():
            current = self._journal.read_module(module)
            if digest is None:
                if current is None:
                    continue
            elif current is not None and content_hash(current) == digest:
                continue
            _log.error("auto-revert did not restore %s to its Snapshot", module)
            self._recovery_halted = True
            return False
        return True

    def _recovery_result(self, result: ApplyResult) -> None:
        """Whether the restore transaction itself was refused -- ADR-0016's escalation."""
        if not result.ok:
            _log.error("the restore transaction failed: %s", result.outcome)
            self._recovery_halted = True

    @property
    def recovery_halted(self) -> bool:
        """Whether the app has stopped recovering automatically and needs the user.

        Set when a restore fails to land the Snapshot it promised. ADR-0016 puts a Banner
        behind this; the Banner and its error dialog are #60, so for now it is a fact the
        session states and the log records rather than one the window draws.
        """
        return self._recovery_halted

    # --- notification -----------------------------------------------------------------------

    def _go_offline(self, reason: str) -> None:
        """Stop applying, and record the one line the Banner will show.

        The `Applier` is deliberately *not* torn down here: this can fire from inside its
        own event stream's callback, and cleanup belongs to `aclose`. `live` is what gates
        every write, so a session that has gone read-only is read-only whatever is still
        holding a socket.
        """
        if self._offline_reason != reason:
            _log.info("session is read-only: %s", reason)
        self._offline_reason = reason

    def _changed(self) -> None:
        if self.on_state_changed is not None:
            self.on_state_changed()
