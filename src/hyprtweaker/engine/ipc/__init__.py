"""IPC: direct clients for Hyprland's command socket and event stream (ADR-0010).

Two objects, one per socket, over one `Instance`::

    instance = Instance.current()
    client = CommandClient(instance)

    async with EventStream(instance) as events:
        with events.arm(RELOAD_STARTED) as reloaded:   # arm before the cause
            await client.reload()
            started = await reloaded.wait(timeout=2.0)
        errors = await client.configerrors()           # only this says whether it worked

**Never spawns `hyprctl`.** A process spawn is ~20 ms against a 0.4 ms socket round-trip,
which is the difference between instant apply and a visibly laggy slider (ADR-0010) --
enforced by `tests/unit/test_no_hyprctl_spawn.py` rather than left as an intention.

The clients are async because the app has to do two things at once here: a transaction
waiting on a reload must not stop the event stream from draining, and a stream that stops
draining gets dropped by Hyprland. Nothing in this package touches GTK -- callbacks are
plain Python callables, and the UI adapts them to its own main loop (ADR-0011).
"""

from __future__ import annotations

from .commands import DEFAULT_TIMEOUT_SECONDS, CommandClient, EvalReply, OptionReply
from .errors import (
    IpcError,
    IpcTimeout,
    MalformedReply,
    NoInstance,
    SocketUnavailable,
    UnknownOption,
)
from .events import (
    MONITOR_ADDED,
    MONITOR_REMOVED,
    RELOAD_STARTED,
    Event,
    EventStream,
    EventWaiter,
    Subscriber,
    Unsubscribe,
)
from .instance import Instance

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MONITOR_ADDED",
    "MONITOR_REMOVED",
    "RELOAD_STARTED",
    "CommandClient",
    "EvalReply",
    "Event",
    "EventStream",
    "EventWaiter",
    "Instance",
    "IpcError",
    "IpcTimeout",
    "MalformedReply",
    "NoInstance",
    "OptionReply",
    "SocketUnavailable",
    "Subscriber",
    "UnknownOption",
    "Unsubscribe",
]
