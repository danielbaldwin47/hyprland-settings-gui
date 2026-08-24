"""The command socket, against scripted conversations captured from a live Hyprland.

Every test here is a round-trip: what the client puts on the wire, and what it makes of
what comes back. The replies are real captures (`_fake_hyprland`), so a reply shape that
changes in a future Hyprland breaks these tests instead of the app.

Async scenarios go through `_fake_hyprland.run_with_fake`, which owns the event loop and
the socket lifecycle.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import pytest
from _fake_hyprland import CONFIG_ERRORS, FakeHyprland, run_with_fake
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.ipc import (
    CommandClient,
    Instance,
    IpcTimeout,
    MalformedReply,
    NoSuchOption,
    SocketUnavailable,
)
from hyprtweaker.engine.model.values import CssGaps, parse_getoption
from hyprtweaker.engine.schema import load_schema

T = TypeVar("T")


def run(scenario: Callable[[CommandClient, FakeHyprland], Awaitable[T]]) -> T:
    """Run `scenario` with a client already pointed at a freshly bound fake."""
    return run_with_fake(lambda fake: scenario(CommandClient(fake.instance), fake))


# --- getoption ----------------------------------------------------------------------------


def test_getoption_sends_the_colon_form_with_the_json_flag() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        await client.getoption("general:gaps_in")
        assert fake.requests == ["j/getoption general:gaps_in"]

    run(scenario)


def test_getoption_reports_the_value_and_whether_the_config_set_it() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        gaps = await client.getoption("general:gaps_in")
        assert gaps.name == "general:gaps_in"
        assert gaps.payload["custom"] == "6 6 6 6"
        assert gaps.set_by_user is True

        # `set: false` is the whole point of the flag: the option exists and has a value,
        # but the value is Hyprland's default rather than anything the config asked for.
        untouched = await client.getoption("misc:disable_autoreload")
        assert untouched.payload["int"] == 0
        assert untouched.set_by_user is False

    run(scenario)


def test_getoption_payload_is_what_the_model_parses() -> None:
    """The seam that matters: the reply goes into `parse_getoption` unmodified.

    The client refuses to pick the value key itself precisely so this holds -- the key is
    engine-dependent and only the Option's schema knows which one to expect.
    """

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        schema = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
        reply = await client.getoption("general:gaps_in")
        assert parse_getoption(schema["general:gaps_in"], dict(reply.payload)) == CssGaps(
            6, 6, 6, 6
        )

    run(scenario)


def test_getoption_keeps_dots_inside_a_leaf_name() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        border = await client.getoption("general:col.active_border")
        assert fake.requests == ["j/getoption general:col.active_border"]
        assert border.payload["custom"] == "ee6a5740 ee7ba153 45deg"

    run(scenario)


def test_unknown_option_raises_rather_than_answering_with_a_value() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        with pytest.raises(NoSuchOption, match="general:nope"):
            await client.getoption("general:nope")

    run(scenario)


def test_the_dot_form_is_an_unknown_option_too() -> None:
    """Why the client documents colon form: the legacy engine rejects the dot form."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        with pytest.raises(NoSuchOption):
            await client.getoption("general.gaps_in")

    run(scenario)


def test_a_reply_that_is_not_json_is_a_malformed_reply() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        with pytest.raises(MalformedReply):
            await client.getoption("general:unscripted")

    run(scenario)


# --- configerrors -------------------------------------------------------------------------


def test_a_clean_config_reads_as_no_errors() -> None:
    """Hyprland answers `[""]`, not `[]`. Anything that trusts truthiness reads it as an
    error and reverts a perfectly good write."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        assert await client.configerrors() == ()
        assert fake.requests == ["j/configerrors"]

    run(scenario)


def test_config_errors_come_back_verbatim_with_their_file_line_prefix() -> None:
    """ADR-0016 attributes ownership by that prefix, so trimming it would cost the app the
    difference between its own bad write and a hand edit."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        fake.conversation["j/configerrors"] = CONFIG_ERRORS
        errors = await client.configerrors()
        assert len(errors) == 2
        assert errors[0].startswith("/home/user/.config/hypr/hyprtweaker/options/general.lua:3")
        assert errors[1].startswith('require("hyprtweaker/options/decoration")')

    run(scenario)


def test_a_configerrors_reply_of_the_wrong_shape_is_malformed() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        fake.conversation["j/configerrors"] = '{"errors": []}'
        with pytest.raises(MalformedReply):
            await client.configerrors()

    run(scenario)


# --- clients and layers (the pickers, #67) ------------------------------------------------


def test_clients_asks_with_the_json_flag_and_returns_the_windows() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        windows = await client.clients()
        assert fake.requests == ["j/clients"]
        assert [window["class"] for window in windows] == ["kitty", "helium"]
        # The camelCase spellings are the wire's, and the picker reads them as such.
        assert windows[1]["initialClass"] == "helium"

    run(scenario)


def test_a_clients_reply_of_the_wrong_shape_is_malformed() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        fake.conversation["j/clients"] = '{"clients": []}'
        with pytest.raises(MalformedReply):
            await client.clients()

    run(scenario)


def test_layers_flattens_outputs_and_levels_into_surfaces() -> None:
    """A layer rule matches `namespace` and nothing else, so surfaces are the answer."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        surfaces = await client.layers()
        assert fake.requests == ["j/layers"]
        assert [surface["namespace"] for surface in surfaces] == [
            "wallpaper",
            "forest-shell:keep-awake",
            "waybar",
        ]

    run(scenario)


def test_a_layers_reply_of_the_wrong_shape_is_malformed() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        fake.conversation["j/layers"] = "[]"
        with pytest.raises(MalformedReply):
            await client.layers()

    run(scenario)


# --- eval ---------------------------------------------------------------------------------


def test_eval_sends_raw_lua_without_the_json_flag() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        reply = await client.eval("hl.config{general={gaps_in=6}}")
        assert fake.requests == ["eval hl.config{general={gaps_in=6}}"]
        assert reply.ok

    run(scenario)


def test_a_rejected_eval_carries_the_parser_message() -> None:
    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        reply = await client.eval("hl.config{general={gaps_in='x'}}")
        assert not reply.ok
        assert not reply.unsupported
        assert "invalid value" in reply.text

    run(scenario)


def test_a_hyprlang_session_refusing_eval_is_not_a_rejected_value() -> None:
    """Nothing about the code would fix this one -- the session is not running Lua, and the
    answer is the Migration wizard rather than an error about a bad value."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        reply = await client.eval("hl.config{general={gaps_in=8}}")
        assert not reply.ok
        assert reply.unsupported

    run(scenario)


# --- reload -------------------------------------------------------------------------------


def test_reload_asks_for_a_reload_and_promises_nothing() -> None:
    """The reply is `ok` whatever happened, so the client returns nothing at all: there is
    no success value here for a caller to believe (research #5 §6)."""

    async def scenario(client: CommandClient, fake: FakeHyprland) -> None:
        assert await client.reload() is None
        assert fake.requests == ["reload"]

    run(scenario)


# --- failure modes ------------------------------------------------------------------------


def test_no_compositor_is_unavailable_not_a_crash() -> None:
    async def main() -> None:
        async with FakeHyprland() as fake:
            instance = fake.instance
        # The fake is stopped: the socket file is gone, exactly as after a Hyprland exit.
        with pytest.raises(SocketUnavailable):
            await CommandClient(instance).configerrors()

    asyncio.run(main())


def test_a_missing_instance_directory_is_unavailable() -> None:
    async def main() -> None:
        with pytest.raises(SocketUnavailable):
            await CommandClient(Instance(Path("/nonexistent/hypr"))).reload()

    asyncio.run(main())


def test_a_compositor_that_never_answers_times_out() -> None:
    """Separate from unavailable on purpose: the session may be alive and merely busy, and
    ADR-0010 keeps "unknown outcome" apart from "failed"."""

    async def scenario(fake: FakeHyprland) -> None:
        with pytest.raises(IpcTimeout):
            await CommandClient(fake.instance, timeout=0.05).configerrors()
        assert fake.requests == ["j/configerrors"], "the request did reach the compositor"

    run_with_fake(scenario, FakeHyprland(never_answer=True))
