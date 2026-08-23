"""A scripted stand-in for Hyprland's two sockets.

The IPC clients have exactly one dependency -- a directory with two unix sockets in it --
so the fake is the real thing minus the compositor: real `AF_UNIX` sockets, real framing,
real EOF-terminated replies. Nothing is monkeypatched, which means these tests exercise the
same code path a live session does, down to the connection handling.

The conversations below are **captures from a live Hyprland 0.56.2**, byte for byte,
including the two shapes that would be easy to get wrong from prose: `configerrors` answers
`[""]` rather than `[]` when the config is clean, and an unknown option answers with the
bare text `no such option` rather than JSON. Replies marked *from source* were not captured
live: exercising them means mutating the developer's own session, so they come from
research #5's reading of `HyprCtl.cpp` instead.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hyprtweaker.engine.ipc import Instance

# --- golden conversations ----------------------------------------------------------------

GAPS_IN = '{"option": "general:gaps_in", "custom": "6 6 6 6", "set": true }'
"""Captured. A css-gaps Option under the hyprlang engine, where every complex type answers
under `custom` -- the reason `parse_getoption` keeps `custom` as a fallback key."""

LAYOUT = '{"option": "general:layout", "str": "scrolling", "set": true }'
"""Captured."""

AUTORELOAD = '{"option": "misc:disable_autoreload", "int": 0, "set": false }'
"""Captured. A bool answers under `int`, and `set: false` means the config never set it."""

ACTIVE_BORDER = (
    '{"option": "general:col.active_border", "custom": "ee6a5740 ee7ba153 45deg", "set": true }'
)
"""Captured. The dots in `col.active_border` are part of the leaf name, not separators."""

NO_SUCH_OPTION = "no such option"
"""Captured, for both a misspelled name and the dot form `general.gaps_in`."""

NO_CONFIG_ERRORS = '[\n\t""\n]\n'
"""Captured on a clean config: one empty string, not an empty array."""

CONFIG_ERRORS = (
    "[\n"
    '\t"/home/user/.config/hypr/hyprtweaker/options/general.lua:3: '
    "unknown config key 'general.nope'\",\n"
    '\t"require(\\"hyprtweaker/options/decoration\\"): bad argument"\n'
    "]\n"
)
"""Same array shape as the captured clean reply, carrying the two error spellings research
 #5 §6 documents: a `file:line`-prefixed `hl.config` complaint and a failed `require`."""

OK = "ok"
"""From source: `reload` answers this unconditionally, and `eval` answers it on success."""

EVAL_ERROR = "error setting 'general.gaps_in': invalid value"
"""From source: a failed `eval` answers with the parser's own message, not with `ok`."""

CONVERSATION: Mapping[str, str] = {
    "j/getoption general:gaps_in": GAPS_IN,
    "j/getoption general:layout": LAYOUT,
    "j/getoption misc:disable_autoreload": AUTORELOAD,
    "j/getoption general:col.active_border": ACTIVE_BORDER,
    "j/getoption general:nope": NO_SUCH_OPTION,
    "j/getoption general.gaps_in": NO_SUCH_OPTION,
    "j/configerrors": NO_CONFIG_ERRORS,
    "reload": OK,
    "reload full-reset": OK,
    "eval hl.config{general={gaps_in=6}}": OK,
    "eval hl.config{general={gaps_in='x'}}": EVAL_ERROR,
}

UNSCRIPTED = "unscripted request"
"""What the fake answers to anything the script does not cover. A distinctive reply beats a
silent default: the test that provoked it fails on the reply *and* can name the request."""


# --- the fake ----------------------------------------------------------------------------


class FakeHyprland:
    """Two unix sockets in a temp dir, answering from a script and pushing events on cue."""

    def __init__(self, conversation: Mapping[str, str] | None = None) -> None:
        self.conversation = dict(CONVERSATION if conversation is None else conversation)
        self.requests: list[str] = []
        """Every request received on the command socket, in order -- the wire-level record a
        test asserts against (that `getoption` really did send the colon form, and so on)."""

        self._directory: Path | None = None
        self._servers: list[asyncio.Server] = []
        self._listeners: list[asyncio.StreamWriter] = []

    @property
    def instance(self) -> Instance:
        if self._directory is None:
            raise RuntimeError("FakeHyprland is not started")
        return Instance(self._directory)

    async def start(
        self, *, command_socket: bool = True, event_socket: bool = True
    ) -> Instance:
        """Bind the sockets. Either can be left unbound to test an absent compositor.

        The directory is a short `mkdtemp` path rather than pytest's `tmp_path`: a unix
        socket path is capped at ~108 bytes and a nested test-name-derived path can reach
        it, which shows up as an unrelated-looking `OSError`.
        """
        self._directory = Path(tempfile.mkdtemp(prefix="hyprfake-"))
        instance = self.instance
        if command_socket:
            self._servers.append(
                await asyncio.start_unix_server(self._serve_command, instance.command_socket)
            )
        if event_socket:
            self._servers.append(
                await asyncio.start_unix_server(self._serve_events, instance.event_socket)
            )
        return instance

    async def stop(self) -> None:
        for writer in self._listeners:
            writer.close()
        self._listeners.clear()

        for server in self._servers:
            server.close()
            await server.wait_closed()
        self._servers.clear()

        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    async def __aenter__(self) -> FakeHyprland:
        await self.start()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.stop()

    async def emit(self, name: str, data: str = "") -> None:
        """Push one `EVENT>>DATA` line to every connected listener."""
        await self.emit_raw(f"{name}>>{data}\n")

    async def emit_raw(self, line: str) -> None:
        """Push arbitrary bytes, for the lines a well-behaved compositor would not send."""
        # A client's `connect` returns before this server has accepted it, so emitting
        # straight after `EventStream.start()` would otherwise push into an empty room and
        # read as "the event never arrived".
        await self.wait_for_listeners(1)
        for writer in list(self._listeners):
            writer.write(line.encode("utf-8"))
            await writer.drain()

    async def wait_for_listeners(self, count: int, *, timeout: float = 2.0) -> None:
        """Block until exactly `count` event connections are registered on this side.

        Connection bookkeeping is asynchronous on both ends, so every assertion about how
        many listeners there are has to wait for it rather than assume it.
        """
        async with asyncio.timeout(timeout):
            while len(self._listeners) != count:
                await asyncio.sleep(0.005)

    async def drop_listeners(self) -> None:
        """Close the event connections from the compositor's side: Hyprland exited."""
        for writer in list(self._listeners):
            writer.close()
        self._listeners.clear()
        # Let the client's reader see the EOF before the test asserts on it.
        await asyncio.sleep(0)

    @property
    def listener_count(self) -> int:
        return len(self._listeners)

    # --- handlers -------------------------------------------------------------------------

    async def _serve_command(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request = (await reader.read(4096)).decode("utf-8")
        self.requests.append(request)
        writer.write(self.conversation.get(request, f"{UNSCRIPTED}: {request}").encode("utf-8"))
        await writer.drain()
        # Closing is the message boundary: Hyprland answers and hangs up.
        writer.close()

    async def _serve_events(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._listeners.append(writer)
        try:
            # socket2 is push-only; the read is just how the handler waits for the client to
            # go away without spinning.
            await reader.read()
        finally:
            if writer in self._listeners:
                self._listeners.remove(writer)
            # A handler that returns without closing leaves the transport attached to the
            # server, and `Server.wait_closed()` then waits for it forever.
            writer.close()


class HangingHyprland:
    """A command socket that accepts connections and never answers -- the timeout case."""

    def __init__(self) -> None:
        self._directory: Path | None = None
        self._server: asyncio.Server | None = None
        self._held: list[asyncio.StreamWriter] = []

    async def start(self) -> Instance:
        self._directory = Path(tempfile.mkdtemp(prefix="hyprhang-"))
        instance = Instance(self._directory)
        self._server = await asyncio.start_unix_server(self._hold, instance.command_socket)
        return instance

    async def stop(self) -> None:
        for writer in self._held:
            writer.close()
        self._held.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)
            self._directory = None

    async def _hold(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._held.append(writer)
        await reader.read()
