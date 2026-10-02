"""Live smoke: the IPC clients against a real running Hyprland.

ADR-0011 tier 3 -- marked `-m hyprland`, outside `testpaths`, skipped by the tier's gate on
a machine that cannot nest a compositor. The unit tier proves the clients handle Hyprland's
replies; only this proves those are Hyprland's replies, which is the half that goes stale
on a compositor release.

The compositor is a nested one from `guarded_hyprland`, never the session's own (#201):
this module once read `Instance.current()`, which from any shell naming the desktop's
signature was the owner's daily session. It stays read-only; the mutating half (`eval`,
`reload`) is driven through the Harness by the end-to-end tests.

Run it explicitly::

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_ipc_live.py -m hyprland
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
from harness import GuardedInstance

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from hyprtweaker.engine.ipc import (  # noqa: E402
    RELOAD_STARTED,
    CommandClient,
    EventStream,
    NoSuchOption,
    read_live_hyprland,
)

pytestmark = pytest.mark.hyprland


def test_getoption_answers_from_the_real_socket(guarded_hyprland: GuardedInstance) -> None:
    async def main() -> None:
        client = CommandClient(guarded_hyprland.instance)

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


def test_an_option_this_hyprland_does_not_have_raises(
    guarded_hyprland: GuardedInstance,
) -> None:
    async def main() -> None:
        with pytest.raises(NoSuchOption):
            await CommandClient(guarded_hyprland.instance).getoption(
                "general:definitely_not_an_option"
            )

    asyncio.run(main())


def test_configerrors_reads_as_a_tuple_of_lines(guarded_hyprland: GuardedInstance) -> None:
    """A healthy session answers with no errors -- and the point is that the `[""]` reply
    reads as empty rather than as one blank error."""

    async def main() -> None:
        errors = await CommandClient(guarded_hyprland.instance).configerrors()
        assert isinstance(errors, tuple)
        assert all(line.strip() for line in errors)

    asyncio.run(main())


def test_the_event_stream_connects_and_arms(guarded_hyprland: GuardedInstance) -> None:
    """No event is provoked: nothing here may touch the session. Connecting and arming is
    what would fail against a wrong socket path or protocol assumption."""

    async def main() -> None:
        async with EventStream(guarded_hyprland.instance) as stream:
            assert stream.running
            with stream.arm(RELOAD_STARTED) as reloaded:
                assert await reloaded.wait(timeout=0.1) is None

    asyncio.run(main())


def test_the_live_read_parses_the_real_version_and_descriptions(
    guarded_hyprland: GuardedInstance,
) -> None:
    """The blocking startup read (#176) against Hyprland's own `j/version` and
    `j/descriptions` replies, whose shapes the unit tier can only script."""
    live = read_live_hyprland(lambda: guarded_hyprland.instance)

    assert live is not None
    assert re.fullmatch(r"\d+\.\d+\.\d+", live.version)
    assert "general:gaps_in" in live.names
    assert len(live.names) == len(live.descriptions)
