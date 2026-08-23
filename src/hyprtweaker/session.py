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
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from hyprtweaker.engine.apply import (
    Applier,
    ApplyResult,
    ReRead,
    app_owned_options,
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
from hyprtweaker.engine.state import Manifest
from hyprtweaker.engine.writer import Writer

_log = logging.getLogger(__name__)

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
        """Called once per finished Apply transaction, successful or not."""

        self._model = ConfigModel(self._schema)
        self._writer = Writer(self._paths, app_version=app_version)

        self._events: EventStream | None = None
        self._client: CommandClient | None = None
        self._applier: Applier | None = None
        self._offline_reason: str | None = _NOT_CONNECTED_YET
        self._closing = False
        self._pending_restart: set[str] = set()

    # --- what the UI reads ------------------------------------------------------------------

    @property
    def schema(self) -> Schema:
        return self._schema

    @property
    def model(self) -> ConfigModel:
        return self._model

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
        """What the Row should show: the set value, else Hyprland's own default.

        An Unset Option is not blank -- the compositor applies its default, and a control
        that renders empty would state that the setting has no value (prototype #8's
        blank-row defect). `None` still means "no value", which is a state the Row renders
        as the curated `null_label` rather than as emptiness.
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
        self._model.set(name, value)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def unset_option(self, name: str) -> None:
        """Reset to Hyprland's default: stop emitting the Option (ADR-0013 §6)."""
        if self._refuse(name):
            return
        self._model.unset(name)
        self._applier.commit(name)  # type: ignore[union-attr]  # _refuse proved it is here

    def touch_option(self, name: str, value: Any) -> None:
        """A mid-gesture edit -- a slider being dragged. Applied once the changes stop."""
        if self._refuse(name):
            return
        self._model.set(name, value)
        self._applier.touch(name)  # type: ignore[union-attr]  # _refuse proved it is here

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
        self._pending_restart.update(result.pending_restart)
        if self.on_applied is not None:
            self.on_applied(result)

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
