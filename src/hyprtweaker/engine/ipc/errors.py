"""What can go wrong on the wire, as four distinguishable failures.

The Apply transaction (#54) and error surfacing (ADR-0016) both branch on *which* of these
happened -- a compositor that went away is a different situation from a reply the app could
not read, and neither is a config error. Config errors are not exceptions at all: they come
back from `configerrors` as data, because a rejected value is the user's business and a
broken socket is the app's.
"""

from __future__ import annotations


class IpcError(Exception):
    """Base for every failure talking to Hyprland. Catch this to mean "IPC did not work"."""


class NoInstance(IpcError):
    """No running Hyprland session to talk to -- nothing was even attempted."""


class SocketUnavailable(IpcError):
    """The socket refused a connection or died mid-request: the compositor is gone."""


class IpcTimeout(IpcError):
    """A request was sent and no reply arrived in time.

    Distinct from `SocketUnavailable` because the compositor may well be alive and merely
    busy -- a `reload` reply waits for the whole reload, which Hyprland's own watchdog only
    caps at 1.5 s -- and a transaction that times out has an unknown outcome rather than a
    failed one (ADR-0010's ApplyResult keeps them apart for exactly that reason).
    """


class MalformedReply(IpcError):
    """A reply arrived but was not the shape this command's reply is supposed to be.

    Always a Hyprland-version surprise rather than a user error: the app asked for JSON and
    got something else, so the version degradation path (ADR-0012) is what should hear
    about it.
    """


class UnknownOption(IpcError):
    """`getoption` for a name this Hyprland does not have."""
