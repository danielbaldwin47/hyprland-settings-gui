"""A kept menu Import is what Restore last good puts back, on a real compositor (#259).

The unit tier proves which bytes the Journal offers. This journey proves the three views
agree after the restore: the file holds the imported bytes, the Session's model holds the
imported value, and the nested Hyprland reads it back live -- with a confirmed write from
before the import still in the Journal, which is what Restore used to offer.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest \\
        tests/integration/test_kept_import_restore_live.py -m hyprland
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION, CompositorState, capture, diff

from hyprtweaker.engine.ipc import CommandClient
from hyprtweaker.engine.migration.flow import MigrationFlow, fresh_start
from hyprtweaker.engine.model.values import CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
SCHEMA = load_schema(SCHEMA_VERSION, SCHEMA_DIR)
GAPS_IN = "general:gaps_in"
GENERAL = "options/general.lua"
IMPORTED = "general {\n    gaps_in = 9\n}\n"
OPTIONS = (GAPS_IN,)


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


def live_once_landed(nested: NestedHyprland, target: CompositorState) -> CompositorState:
    """The live state once a reload has landed: polled until it matches `target`, 5 s."""
    deadline = time.monotonic() + 5.0
    while True:
        now = capture(nested, options=OPTIONS)
        if diff(target, now).empty or time.monotonic() > deadline:
            return now
        time.sleep(0.2)


def test_restore_after_a_kept_import_agrees_on_disk_in_the_model_and_live(
    harness_home: Path, artifacts: Path
) -> None:
    paths = ConfigPaths(
        hypr_dir=harness_home / ".config" / "hypr",
        state_dir=harness_home / ".local" / "state" / "hyprtweaker",
    )
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    model = fresh_start(paths, SCHEMA, app_version=APP_VERSION)
    model.set(GAPS_IN, CssGaps(3, 3, 3, 3))
    Writer(paths, app_version=APP_VERSION).write(model)
    source = harness_home / "other.conf"
    source.write_text(IMPORTED, encoding="utf-8")

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

        # A confirmed write before the import: the restore point Restore used to offer.
        session.set_option(GAPS_IN, CssGaps(4, 4, 4, 4))
        await settled(session, loop)
        before = session.last_good_for(GENERAL)
        assert before is not None and b"4" in before.data

        flow = MigrationFlow(
            paths=paths,
            schema=SCHEMA,
            app_version=APP_VERSION,
            client=CommandClient(nested.instance),
        )
        flow.detect()
        flow.build_preview(source)
        flow.back_up()
        result = await flow.switch()
        assert result.ok, result.checks
        flow.keep()
        session.adopt_import()
        await settled(session, loop)
        imported = paths.file_for(GENERAL).read_bytes()
        imported_live = capture(nested, options=OPTIONS)
        imported_live.write(artifacts / "imported.json")
        assert session.model.get(GAPS_IN) == CssGaps(9, 9, 9, 9)

        paths.file_for(GENERAL).write_bytes(b"-- hand edited\n")
        good = session.last_good_for(GENERAL)
        assert good is not None and good.data == imported, "offered the pre-import bytes"
        outcomes: list[bool] = []
        assert session.restore_last_good(GENERAL, done=outcomes.append).queued
        await settled(session, loop)

        assert outcomes == [True]
        assert paths.file_for(GENERAL).read_bytes() == imported
        assert session.model.get(GAPS_IN) == CssGaps(9, 9, 9, 9)
        live = live_once_landed(nested, imported_live)
        live.write(artifacts / "restored.json")
        assert diff(imported_live, live).empty, diff(imported_live, live).describe()
        await session.aclose()

    with NestedHyprland(
        paths.entrypoint, home=harness_home, log=artifacts / "nested.log"
    ) as nested:
        asyncio.run(journey(nested))
