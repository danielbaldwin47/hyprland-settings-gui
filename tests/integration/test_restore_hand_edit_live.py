"""Restore last good keeps the hand edit it replaces, on a real compositor (#266).

The unit tier proves the order (every copy before any write) and the settlement. This
journey proves the three views agree at each step on a nested Hyprland: the file's bytes,
the Session's model (what the Rows display) and the value the compositor reads back live.
A Restore whose copy cannot be kept changes none of them; one that can keeps the copy
where it says, and leaves all three on the restored value, which the next edit keeps.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest \\
        tests/integration/test_restore_hand_edit_live.py -m hyprland
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION, capture

from hyprtweaker.engine.migration.flow import fresh_start
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
SCHEMA = load_schema(SCHEMA_VERSION, SCHEMA_DIR)
BORDER_SIZE = "general:border_size"
GAPS_WORKSPACES = "general:gaps_workspaces"
GENERAL = "options/general.lua"


class Loop:
    """The Session's scheduler on a bare asyncio loop (`test_shell_session.py`)."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def settle(self) -> None:
        while self._tasks:
            batch, self._tasks = self._tasks, []
            await asyncio.gather(*batch)


async def settled(session: Session, loop: Loop) -> None:
    for _ in range(2):
        await session.drain()
        await loop.settle()


def live_border(nested: NestedHyprland, expected: int) -> Any:
    """The live border size once a reload has landed: polled until `expected`, 5 s."""
    deadline = time.monotonic() + 5.0
    while True:
        now = capture(nested, options=(BORDER_SIZE,)).option(BORDER_SIZE)
        if now == expected or time.monotonic() > deadline:
            return now
        time.sleep(0.2)


def test_restore_keeps_the_hand_edit_and_agrees_on_disk_in_the_model_and_live(
    harness_home: Path, artifacts: Path
) -> None:
    paths = ConfigPaths(
        hypr_dir=harness_home / ".config" / "hypr",
        state_dir=harness_home / ".local" / "state" / "hyprtweaker",
    )
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    model = fresh_start(paths, SCHEMA, app_version=APP_VERSION)
    model.set(BORDER_SIZE, 2)
    Writer(paths, app_version=APP_VERSION).write(model)

    async def journey(nested: NestedHyprland) -> None:
        loop = Loop()
        session = Session(
            spawn=loop.spawn,
            schema=SCHEMA,
            paths=paths,
            app_version=APP_VERSION,
            connect=lambda: nested.instance,
        )
        session.start()
        await loop.settle()
        assert session.live, session.offline_reason

        # The restore point: a confirmed write of 4.
        session.set_option(BORDER_SIZE, 4)
        await settled(session, loop)
        good = paths.file_for(GENERAL).read_bytes()
        assert b"border_size = 4," in good
        assert live_border(nested, 4) == 4

        # A hand edit to 7, loaded by somebody else's reload: file, model and live say 7.
        edited = good.replace(b"border_size = 4,", b"border_size = 7,")
        paths.file_for(GENERAL).write_bytes(edited)
        nested.hyprctl_text("reload")
        assert live_border(nested, 7) == 7
        for _ in range(25):
            await asyncio.sleep(0.2)
            await settled(session, loop)
            if session.model.get(BORDER_SIZE) == 7:
                break
        assert session.model.get(BORDER_SIZE) == 7
        assert session.edited_outside(GENERAL)

        # No copy can be kept: the Restore is refused, and nothing moves.
        paths.edited_copies_dir.parent.mkdir(parents=True, exist_ok=True)
        paths.edited_copies_dir.write_text("in the way")
        refused = session.restore_last_good(GENERAL)
        await settled(session, loop)
        assert not refused and refused.uncopied == (GENERAL,)
        assert paths.file_for(GENERAL).read_bytes() == edited
        assert session.model.get(BORDER_SIZE) == 7
        assert live_border(nested, 7) == 7
        paths.edited_copies_dir.unlink()

        # The copy can be kept: the hand edit is where the answer says, and all three
        # views are on the restored 4.
        outcomes: list[bool] = []
        start = session.restore_last_good(GENERAL, done=outcomes.append)
        await settled(session, loop)
        assert start and outcomes == [True]
        assert start.copies[GENERAL].read_bytes() == edited
        assert paths.file_for(GENERAL).read_bytes() == good
        assert session.model.get(BORDER_SIZE) == 4
        assert live_border(nested, 4) == 4
        assert not session.edited_outside(GENERAL)
        assert session.health.edited_files == ()

        # The next edit in the Module renders the model: the restored 4 stays.
        session.set_option(GAPS_WORKSPACES, 3)
        await settled(session, loop)
        text = paths.file_for(GENERAL).read_text()
        assert "border_size = 4," in text and "gaps_workspaces = 3," in text
        assert live_border(nested, 4) == 4
        capture(nested, options=(BORDER_SIZE, GAPS_WORKSPACES)).write(
            artifacts / "after-edit.json"
        )
        await session.aclose()

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        asyncio.run(journey(nested))
