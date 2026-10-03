"""A refused drag against a real compositor: the desktop shows what the Row and the file say.

A drag previews each tick by `eval` (ADR-0010) and writes once on release. When the Module
was edited by hand after the first tick, the release is refused (ADR-0005), and before #267
the compositor kept the dragged value until the next reload while the Row went back. Only a
compositor can say what it is showing, so this reads it back with `hyprctl -j getoption`.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest <this file> -m hyprland
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION, option_value

from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.session import Session

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
SCHEMA = load_schema(SCHEMA_VERSION, SCHEMA_DIR)

ROUNDING = "decoration:rounding"
SAVED = 6
DRAGGED = 21


class Loop:
    """One `Session`'s work on a plain asyncio loop, as in `test_shell_session.py`."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def settle(self) -> None:
        while self._tasks:
            batch, self._tasks = self._tasks, []
            await asyncio.gather(*batch)


def config_root(home: Path) -> ConfigPaths:
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    paths.entrypoint.write_text(f'require("{paths.require_path(paths.user_lua)}")\n')
    return paths


def live(nested: NestedHyprland) -> Any:
    return option_value(nested.getoptions([ROUNDING])[ROUNDING])


async def drag(
    nested: NestedHyprland, paths: ConfigPaths, *, hand_edit_mid_drag: bool
) -> tuple[Session, bytes, Any]:
    """Save `SAVED`, drag to `DRAGGED`, optionally hand-edit the Module after the first tick,
    then release. Returns the session, the Module's bytes before the release, and the live
    value the compositor showed mid-drag."""
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
    session.set_option(ROUNDING, SAVED)
    await session.drain()
    await loop.settle()

    for value in (SAVED + 5, DRAGGED):
        session.preview_option(ROUNDING, value)
        assert session._applier is not None
        await session._applier.flush_previews()
    mid_drag = live(nested)
    module = paths.app_dir / "options" / "decoration.lua"
    if hand_edit_mid_drag:
        module.write_text(module.read_text() + "-- edited by hand\n")
    before = module.read_bytes()

    session.set_option(ROUNDING, DRAGGED)
    await session.drain()
    await loop.settle()
    assert session._applier is not None
    await session._applier.flush_previews()
    return session, before, mid_drag


def test_a_drag_refused_by_a_hand_edit_leaves_the_compositor_on_the_saved_value(
    harness_home: Path, artifacts: Path
) -> None:
    paths = config_root(harness_home)

    with NestedHyprland(paths.entrypoint, home=harness_home, log=artifacts / "n.log") as nested:

        async def scenario() -> None:
            session, before, mid_drag = await drag(nested, paths, hand_edit_mid_drag=True)
            assert mid_drag == DRAGGED, "the drag's eval did not reach the compositor"

            assert session.model.get(ROUNDING) == SAVED
            assert live(nested) == SAVED
            assert (paths.app_dir / "options" / "decoration.lua").read_bytes() == before
            await session.aclose()

        asyncio.run(scenario())


def test_a_drag_released_normally_lands_in_the_file_and_the_compositor(
    harness_home: Path, artifacts: Path
) -> None:
    paths = config_root(harness_home)

    with NestedHyprland(paths.entrypoint, home=harness_home, log=artifacts / "n.log") as nested:

        async def scenario() -> None:
            session, _, mid_drag = await drag(nested, paths, hand_edit_mid_drag=False)
            assert mid_drag == DRAGGED

            assert session.model.get(ROUNDING) == DRAGGED
            assert live(nested) == DRAGGED
            text = (paths.app_dir / "options" / "decoration.lua").read_text()
            assert f"rounding = {DRAGGED}" in text
            await session.aclose()

        asyncio.run(scenario())
