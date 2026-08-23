"""Live smoke: the IPC clients against a real running Hyprland.

ADR-0011 tier 3 -- marked `-m hyprland`, outside `testpaths`, auto-skipped without a
session. The unit tier proves the clients handle Hyprland's replies; only this proves those
are Hyprland's replies, which is the half that goes stale on a compositor release.

**Read-only, deliberately.** `eval` and `reload` are not exercised here: the only Hyprland
in reach is the developer's own session, `eval` wipes its `configerrors` and `reload`
re-executes their whole config. Exercising the mutating half needs a compositor nobody is
sitting in -- the nested-headless Harness (#55), which is where it belongs.

Run it explicitly::

    pytest tests/integration -m hyprland
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.ipc import (  # noqa: E402
    RELOAD_STARTED,
    CommandClient,
    EventStream,
    Instance,
    NoInstance,
    UnknownOption,
)

pytestmark = pytest.mark.hyprland


def live_instance() -> Instance | None:
    try:
        return Instance.current()
    except NoInstance:
        return None


INSTANCE = live_instance()

skip_without_hyprland = pytest.mark.skipif(
    INSTANCE is None, reason="no running Hyprland session on this machine"
)


@skip_without_hyprland
def test_getoption_answers_from_the_real_socket() -> None:
    async def main() -> None:
        assert INSTANCE is not None
        client = CommandClient(INSTANCE)

        gaps = await client.getoption("general:gaps_in")
        assert gaps.name == "general:gaps_in"
        assert isinstance(gaps.set_by_user, bool)
        # Whichever key this engine uses, there is exactly one besides the two envelope
        # fields -- that is the shape `parse_getoption` relies on.
        value_keys = set(gaps.payload) - {"option", "set"}
        assert len(value_keys) == 1, gaps.payload

        nested = await client.getoption("input:touchpad:natural_scroll")
        assert nested.name == "input:touchpad:natural_scroll"

    asyncio.run(main())


@skip_without_hyprland
def test_an_option_this_hyprland_does_not_have_raises() -> None:
    async def main() -> None:
        assert INSTANCE is not None
        with pytest.raises(UnknownOption):
            await CommandClient(INSTANCE).getoption("general:definitely_not_an_option")

    asyncio.run(main())


@skip_without_hyprland
def test_configerrors_reads_as_a_tuple_of_lines() -> None:
    """A healthy session answers with no errors -- and the point is that the `[""]` reply
    reads as empty rather than as one blank error."""

    async def main() -> None:
        assert INSTANCE is not None
        errors = await CommandClient(INSTANCE).configerrors()
        assert isinstance(errors, tuple)
        assert all(line.strip() for line in errors)

    asyncio.run(main())


@skip_without_hyprland
def test_the_event_stream_connects_and_arms() -> None:
    """No event is provoked: nothing here may touch the session. Connecting and arming is
    what would fail against a wrong socket path or protocol assumption."""

    async def main() -> None:
        assert INSTANCE is not None
        async with EventStream(INSTANCE) as stream:
            assert stream.running
            with stream.arm(RELOAD_STARTED) as reloaded:
                assert await reloaded.wait(timeout=0.1) is None

    asyncio.run(main())
