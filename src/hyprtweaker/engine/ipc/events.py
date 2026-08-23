"""The event stream: one long-lived socket2 connection, fanned out to Engine callbacks.

Hyprland pushes `EVENT>>DATA\\n` lines and never reads (research #5 §7). Two properties of
that stream shape this module:

* **A slow client is dropped.** Hyprland disconnects a listener that lets 64 events queue
  up, so dispatch is synchronous and callbacks must return immediately -- anything slow
  belongs on a task the callback schedules, never inside it.
* **The stream cannot be re-armed after the fact.** A listener that connects *after*
  triggering a reload misses the `configreloaded` for it (research #5 §7 hit this with
  `socat`). `EventStream.arm()` exists for that: it starts listening for the next event of
  a name *before* the caller does the thing that causes it.

The naming here carries the one semantic ADR-0010 is emphatic about: the event is
`RELOAD_STARTED`, not "applied". `configreloaded` fires at the end of `postConfigReload`,
about 11 ms before the new values are readable, and fires just the same when the config was
rejected. Anything that wants to know whether an apply *worked* has to read `configerrors`
and the touched keys afterwards; nothing on this stream can tell it.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from types import TracebackType

from .errors import SocketUnavailable
from .instance import Instance

_log = logging.getLogger(__name__)

RELOAD_STARTED = "configreloaded"
"""A reload has finished running -- success or failure, no payload.

Named for what a caller may conclude from it. "The reload happened" is the whole message:
whether it applied cleanly is a `configerrors` question, and whether the app caused it is an
in-flight-transaction question (an uncorrelated one means somebody else -- a bridge tool, a
hand edit -- rewrote the config, and ADR-0010 answers that with a full state re-read)."""

MONITOR_ADDED = "monitoraddedv2"
"""Data is `ID,NAME,DESCRIPTION`. The v2 form carries the description, which is what
monitor rules prefer to key on (ADR-0015)."""

MONITOR_REMOVED = "monitorremovedv2"
"""Data is `ID,NAME,DESCRIPTION`, as for `MONITOR_ADDED`."""

_SEPARATOR = ">>"


@dataclass(frozen=True, slots=True)
class Event:
    """One line off socket2, split at the first separator.

    `data` is left raw. Its shape is per-event (comma-joined fields, a bare name, or
    nothing), Hyprland truncates it to 1024 bytes and replaces newlines with spaces, and
    every consumer of a given event knows its own shape -- so parsing it here would be a
    lossy guess made in the one place with the least context.
    """

    name: str
    data: str

    @classmethod
    def parse(cls, line: str) -> Event | None:
        """The event in `line`, or `None` if it is not one.

        A line without the separator is not an event this app understands, and a stream
        that dies on an unrecognised line would take the whole session's monitor hotplug
        and reload confirmation down with it -- so unparseable lines are dropped, not
        raised.
        """
        name, separator, data = line.partition(_SEPARATOR)
        if not separator:
            return None
        return cls(name=name, data=data)


Subscriber = Callable[[Event], None]
Unsubscribe = Callable[[], None]


class EventStream:
    """A connection to `.socket2.sock` that calls back on every event it receives.

    Started once for the app's lifetime and closed with it. The Engine half stops at plain
    Python callables: the UI adapts them to its own main loop, which is what keeps `gi` out
    of the engine (ADR-0011).
    """

    def __init__(
        self, instance: Instance, *, on_lost: Callable[[], None] | None = None
    ) -> None:
        """`on_lost` fires when the compositor closes the stream -- Hyprland has exited.

        Worth wiring even though there is nothing to retry: with the stream gone the app's
        view of the config is frozen at whatever it last read, and saying so beats
        pretending to be live.
        """
        self._instance = instance
        self._on_lost = on_lost
        self._subscriptions: dict[int, tuple[frozenset[str] | None, Subscriber]] = {}
        self._ids = itertools.count()
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lost_reported = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # --- lifecycle ------------------------------------------------------------------------

    async def start(self) -> None:
        """Connect and begin dispatching.

        Returns once the connection is established, not once the first event arrives: a
        caller that triggers a reload the moment this returns is guaranteed to be listening
        in time for it.
        """
        if self._task is not None:
            raise RuntimeError("this EventStream is already started")

        try:
            reader, writer = await asyncio.open_unix_connection(self._instance.event_socket)
        except OSError as error:
            raise SocketUnavailable(
                f"{self._instance.event_socket} is not answering: {error}"
            ) from error

        self._writer = writer
        self._task = asyncio.create_task(self._dispatch(reader), name="hyprtweaker-events")

    async def aclose(self) -> None:
        """Stop dispatching and drop the connection. Safe to call twice."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def __aenter__(self) -> EventStream:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    # --- subscription ---------------------------------------------------------------------

    def subscribe(self, callback: Subscriber, *names: str) -> Unsubscribe:
        """Call `callback` for the named events, or for every event when none are named.

        Returns the way to stop -- a handle rather than a `unsubscribe(callback)` lookup,
        because the same callback may legitimately be subscribed twice (two Rows watching
        the same event) and removing "it" would then be ambiguous.
        """
        key = next(self._ids)
        self._subscriptions[key] = (frozenset(names) if names else None, callback)

        def unsubscribe() -> None:
            self._subscriptions.pop(key, None)

        return unsubscribe

    def arm(self, name: str) -> EventWaiter:
        """Start waiting for the next `name` event, before whatever causes it happens."""
        return EventWaiter(self, name)

    # --- internals ------------------------------------------------------------------------

    async def _dispatch(self, reader: asyncio.StreamReader) -> None:
        try:
            while line := await reader.readline():
                event = Event.parse(line.decode("utf-8", errors="replace").rstrip("\r\n"))
                if event is not None:
                    self._deliver(event)
        except asyncio.CancelledError:
            raise
        except (OSError, ValueError) as error:
            # ValueError: a line past asyncio's buffer limit. Hyprland caps event data at
            # 1024 bytes, so this means the stream is not what we think it is either way.
            _log.warning("Hyprland event stream failed: %s", error)
        self._report_lost()

    def _deliver(self, event: Event) -> None:
        # Snapshot: a subscriber is entitled to unsubscribe itself (or another) from inside
        # its own callback, which would otherwise mutate the dict mid-iteration.
        for names, callback in list(self._subscriptions.values()):
            if names is not None and event.name not in names:
                continue
            try:
                callback(event)
            except Exception:
                # One bad subscriber must not cost the app its only event stream -- and a
                # stopped reader is worse than noisy: Hyprland drops a listener that stops
                # draining, so the app would silently go deaf to every later event.
                _log.exception("event subscriber failed on %s", event.name)

    def _report_lost(self) -> None:
        if self._lost_reported:
            return
        self._lost_reported = True
        if self._on_lost is not None:
            try:
                self._on_lost()
            except Exception:
                _log.exception("event stream on_lost callback failed")


class EventWaiter:
    """A one-shot wait for the next event of one name, armed before its cause.

    The Apply transaction's confirm step is the reason this exists: arm, write the Modules,
    ask for a reload, then wait. Arming first is what makes the wait immune to the reload
    finishing faster than the app can start listening.
    """

    def __init__(self, stream: EventStream, name: str) -> None:
        self._name = name
        self._received: Event | None = None
        self._arrived = asyncio.Event()
        self._cancel: Unsubscribe | None = stream.subscribe(self._record, name)

    def _record(self, event: Event) -> None:
        if self._received is None:
            self._received = event
            self._arrived.set()

    async def wait(self, timeout: float) -> Event | None:
        """The event, or `None` if `timeout` seconds pass without it.

        Timeout is a return value rather than an exception because it is an expected
        outcome with its own handling: ADR-0010 gives it a branch of ApplyResult, on the
        grounds that a reload that never reported back leaves the apply's fate *unknown*
        rather than failed.
        """
        try:
            async with asyncio.timeout(timeout):
                await self._arrived.wait()
        except TimeoutError:
            return None
        return self._received

    def close(self) -> None:
        """Stop listening. Idempotent."""
        if self._cancel is not None:
            self._cancel()
            self._cancel = None

    def __enter__(self) -> EventWaiter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
