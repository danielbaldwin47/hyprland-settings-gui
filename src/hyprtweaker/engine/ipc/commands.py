"""The command socket: the four things the app asks Hyprland to do or tell it.

`hyprctl` is a 20 ms process spawn around a 0.4 ms socket round-trip (measured, ADR-0010),
and instant apply cannot afford it on every slider tick -- so this speaks the wire protocol
itself and the app never spawns the CLI at all.

The protocol is small enough to state in full. Connect to `.socket.sock`, write one request,
read until Hyprland closes the connection. A request is `<flags>/<command>` where the only
flag this module uses is `j` for JSON; without a flag the reply is human-readable text::

    j/getoption general:gaps_in  ->  {"option": "general:gaps_in", "custom": "6 6 6 6",
                                      "set": true }
    j/configerrors               ->  [\\n\\t""\\n]\\n
    reload                       ->  ok

Two shapes of that are worth stating up front, because both have bitten:

* **`reload`'s reply is "ok" unconditionally**, whatever happened -- errors live in
  `configerrors` and nowhere else (research #5 §6). This module returns `None` from
  `reload()` so there is no "ok" for a caller to mistake for success.
* **`configerrors` is a JSON array holding one empty string when the config is clean**, not
  an empty array. Filtering blanks is what makes "no errors" spell as an empty tuple here.

Not implemented on purpose: `[[BATCH]]a;b` multiplexing (Hyprland joins the replies with
`\\n\\n\\n`, which is only unambiguous while no reply contains a blank line pair). Read-back
touches a handful of keys per transaction at 0.4 ms each; the parsing risk buys nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .errors import IpcTimeout, MalformedReply, NoSuchOption, SocketUnavailable
from .instance import Instance

DEFAULT_TIMEOUT_SECONDS = 5.0
"""Long enough for the slowest reply: `reload` answers only once the reload has finished,
and Hyprland's own config watchdog runs to 1.5 s before it gives up (research #5 §1)."""

_NO_SUCH_OPTION = "no such option"

UNSUPPORTED_EVAL = "eval is only supported with the lua config manager"
"""What a hyprlang session answers to `eval` -- captured off one. Not an error the app can
fix by sending different code: that session is not running a Lua config at all."""


@dataclass(frozen=True, slots=True)
class OptionReply:
    """One `getoption` answer, kept in the shape the model's reader expects.

    The value key is engine-dependent -- `custom` under hyprlang, the type's own name under
    the Lua engine -- so this deliberately does *not* pick the value out. That decision
    needs the Option's schema, and `model.values.parse_getoption` already owns it; handing
    the payload over whole is what keeps one parser instead of two.
    """

    name: str
    payload: Mapping[str, Any]

    @property
    def set_by_user(self) -> bool:
        """Whether the running config set this Option, as opposed to leaving the default.

        Reset by every reload (Hyprland resets `setByUser` on all values before re-running
        the config), so it answers "does the live config still set this", which is exactly
        the Read-back and drift-badge question (ADR-0005, ADR-0010).
        """
        return bool(self.payload.get("set", False))


@dataclass(frozen=True, slots=True)
class EvalReply:
    """The result of one `eval`: Hyprland's own parser, without touching a file."""

    text: str

    @property
    def ok(self) -> bool:
        """Exactly `ok` came back: no error, and the evaluated Lua printed nothing.

        Not quite "it worked". Hyprland answers with the error text *or* with whatever the
        code printed, so a preview whose Lua prints reads as not-ok here. The Eval preview
        tier only ever sends `hl.config{...}`, which prints nothing -- anything richer
        needs to read `text` rather than trust this.
        """
        return self.text.strip() == "ok"

    @property
    def unsupported(self) -> bool:
        """This session refuses `eval` at all: it is running the hyprlang manager.

        Worth telling apart from a rejected value, because nothing the app can do to the
        code will help -- the config is not Lua, and the answer is the Migration wizard
        (ADR-0009), not an error toast about a bad value.
        """
        return self.text.strip() == UNSUPPORTED_EVAL


class CommandClient:
    """Request/reply against `.socket.sock`. One connection per call, as the protocol wants.

    Stateless by design: Hyprland closes the connection after every reply, so a "persistent"
    client would be a reconnect loop wearing a different name. Cheap enough that the Apply
    transaction can afford one call per touched key.
    """

    def __init__(self, instance: Instance, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._instance = instance
        self._timeout = timeout

    # --- the four commands ----------------------------------------------------------------

    async def getoption(self, name: str) -> OptionReply:
        """Read one Option's live value. `name` is the colon form: `input:touchpad:tap`.

        The dot form the Lua manager also accepts is not used: the legacy manager rejects it
        (`no such option`), and a name that works on one engine and not the other is a
        migration-shaped bug waiting for the one user still on hyprlang. Note that a dot
        inside a *leaf* name is normal -- `general:col.active_border` is a colon-form name.
        """
        reply = await self._request(f"getoption {name}", json_output=True)
        if reply.strip() == _NO_SUCH_OPTION:
            raise NoSuchOption(
                f"Hyprland has no option {name!r} "
                f"(names are colon-separated, e.g. general:gaps_in)"
            )

        payload = _parse_json(reply, f"getoption {name}")
        if not isinstance(payload, dict) or "option" not in payload:
            raise MalformedReply(f"getoption {name} answered {reply!r}")
        return OptionReply(name=str(payload["option"]), payload=payload)

    async def configerrors(self) -> tuple[str, ...]:
        """Every error from the **last** parse, oldest first; empty means the config is clean.

        Read it after a reload and before anything else: the list is cleared by the next
        reload *and by any `eval`* (research #5 §3), so an Eval preview racing a confirming
        transaction would erase the very errors that transaction is waiting to read.

        Lines are returned verbatim, `file:line`-prefixed as Hyprland wrote them, because
        that prefix is what ADR-0016 attributes ownership by.
        """
        reply = await self._request("configerrors", json_output=True)
        payload = _parse_json(reply, "configerrors")
        if not isinstance(payload, list):
            raise MalformedReply(f"configerrors answered {payload!r}")
        # Clean answers as `[""]`, not `[]` -- the joined-errors string is what gets
        # serialised, and an empty one still occupies an element.
        return tuple(str(line) for line in payload if str(line).strip())

    async def eval(self, code: str) -> EvalReply:
        """Run Lua in the live config state -- the Eval preview tier (ADR-0010).

        Transient: the next reload resets every value and rebuilds the Lua VM, so an eval is
        only ever a preview of something a file write has to make durable.

        **`eval` clears `configerrors` on entry.** Never run one between a reload and its
        Read-back; the Apply queue exists partly to make that ordering impossible.
        """
        return EvalReply(await self._request(f"eval {code}", json_output=False))

    async def reload(self) -> None:
        """Re-read the config now. Returns when Hyprland has finished reloading.

        Deliberately returns nothing. The reply is `"ok"` even when the config failed to
        parse, so the only honest confirmation is the `configreloaded` event plus a
        `configerrors` read (ADR-0010 step 5).

        The `reload full-reset` variant -- which rebuilds the config manager and re-resolves
        the config path, the one way to switch a live session from hyprlang to Lua -- is not
        here. It is migration mechanics with session-wide side effects (`hyprland.start`
        never re-fires), so it belongs with the wizard that needs it (ADR-0009) rather than
        sitting next to the apply path as a flag.
        """
        await self._request("reload", json_output=False)

    # --- wire -----------------------------------------------------------------------------

    async def _request(self, command: str, *, json_output: bool) -> str:
        """Send one request, return the whole reply text."""
        request = f"j/{command}" if json_output else command
        try:
            async with asyncio.timeout(self._timeout):
                return await self._round_trip(request)
        except TimeoutError as error:
            raise IpcTimeout(f"no reply to {command!r} within {self._timeout}s") from error
        except OSError as error:
            raise SocketUnavailable(
                f"{self._instance.command_socket} is not answering: {error}"
            ) from error

    async def _round_trip(self, request: str) -> str:
        reader, writer = await asyncio.open_unix_connection(self._instance.command_socket)
        try:
            writer.write(request.encode("utf-8"))
            await writer.drain()
            # Hyprland closes the connection once it has written the reply, so EOF is the
            # message boundary -- there is no length prefix and no terminator to look for.
            return (await reader.read()).decode("utf-8", errors="replace")
        finally:
            writer.close()
            # A close that fails has nothing left to affect: the reply is already in hand.
            with suppress(OSError):
                await writer.wait_closed()


def _parse_json(reply: str, command: str) -> Any:
    try:
        return json.loads(reply)
    except ValueError as error:
        raise MalformedReply(f"{command} answered non-JSON {reply!r}") from error
