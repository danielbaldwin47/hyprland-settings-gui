"""A bind editor held open across a foreign reload, against a real compositor (#225).

The editor captures a list index when it opens and saves through `Session.replace_bind`
at that index. `tests/unit/test_session_held_editor.py` proves both halves against a
scripted socket; this is the one nested run the diagnosis rests on: the real Writer,
Applier, Manifest gate and `configreloaded` from a `hyprctl reload` somebody else issued.

    HARNESS_DRM_CARD=/dev/dri/card0 pytest tests/integration/test_held_editor_live.py
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.model import Bind, DispatcherCall
from hyprtweaker.engine.model.entities import EntitySet
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer.binds import render_binds_module
from hyprtweaker.session import Session

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
SCHEMA = load_schema(SCHEMA_VERSION, SCHEMA_DIR)
BINDS = "binds.lua"


def exec_bind(keys: str, command: str) -> Bind:
    return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)))


A = exec_bind("SUPER + A", "alpha")
B = exec_bind("SUPER + B", "bravo")
C = exec_bind("SUPER + C", "charlie")
DRAFT = exec_bind("SUPER + D", "bravo-edited")
"""The user changed bravo's keys and command in the editor they opened on it."""


class Loop:
    """One `Session`'s work on a plain asyncio loop (as `test_shell_session.py`)."""

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


def live_keys(nested: NestedHyprland) -> list[str]:
    """The SUPER binds the compositor itself holds, in its order. By key: `hyprctl binds`
    names a Lua dispatcher by a function reference, not by its command."""
    return [entry["key"] for entry in nested.hyprctl("binds") if entry.get("modmask") == 64]


def commands(binds: list[Bind]) -> list[str]:
    return [b.dispatcher.positional[0] for b in binds if b.dispatcher is not None]


Scenario = Callable[[NestedHyprland, ConfigPaths, Session, Callable[[], Awaitable[None]]], Any]


def run(harness_home: Path, artifacts: Path, scenario: Scenario) -> None:
    paths = config_root(harness_home)
    with NestedHyprland(paths.entrypoint, home=harness_home, log=artifacts / "nested.log") as (
        nested
    ):

        async def main() -> None:
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

            async def settle() -> None:
                for _ in range(3):
                    await asyncio.sleep(0.3)
                    await loop.settle()
                    await session.drain()

            for entity in (A, B, C):
                assert session.add_bind(entity)
                await settle()
            assert live_keys(nested) == ["A", "B", "C"]
            try:
                await scenario(nested, paths, session, settle)
            finally:
                await session.aclose()

        asyncio.run(main())


def record(artifacts: Path, name: str, nested: NestedHyprland, paths: ConfigPaths) -> None:
    (artifacts / f"{name}.binds.lua").write_bytes((paths.app_dir / BINDS).read_bytes())
    (artifacts / f"{name}.hyprctl-binds.json").write_text(json.dumps(nested.hyprctl("binds")))


def hand_write(paths: ConfigPaths, binds: list[Bind]) -> None:
    text = render_binds_module(EntitySet(binds=binds), app_version="by-hand")
    assert text is not None
    (paths.app_dir / BINDS).write_text(text, encoding="utf-8")


def test_a_held_editor_save_into_a_hand_reordered_file_is_refused(
    harness_home: Path, artifacts: Path
) -> None:
    """Half (a): the editor opened on bravo (index 1); somebody moves bravo to the end and
    reloads. The save is refused and nothing is written: the gate holds."""

    async def scenario(
        nested: NestedHyprland, paths: ConfigPaths, session: Session, settle: Any
    ) -> None:
        refused: list[tuple[str, str]] = []
        session.on_refused = lambda what, file: refused.append((what, file))
        hand_write(paths, [A, C, B])
        nested.hyprctl_text("reload")
        await settle()
        assert commands(session.model.entities.binds) == ["alpha", "charlie", "bravo"]
        before = (paths.app_dir / BINDS).read_bytes()

        assert session.replace_bind(1, DRAFT) is False
        await settle()

        record(artifacts, "a-refused", nested, paths)
        assert refused == [("Keybind changed", BINDS)]
        assert (paths.app_dir / BINDS).read_bytes() == before
        assert live_keys(nested) == ["A", "C", "B"]

    run(harness_home, artifacts, scenario)


@pytest.mark.xfail(
    strict=True,
    reason="#225: the reverted file reopens the write gate over a stale model, and "
    "replace_bind's held index then lands on charlie",
)
def test_a_held_editor_save_after_a_reverted_hand_edit_keeps_every_other_bind(
    harness_home: Path, artifacts: Path
) -> None:
    """Half (b) by an external-only route: the reorder is reloaded, then put back to the
    app's own bytes and reloaded again, all while the editor on bravo (index 1) is open."""

    async def scenario(
        nested: NestedHyprland, paths: ConfigPaths, session: Session, settle: Any
    ) -> None:
        app_bytes = (paths.app_dir / BINDS).read_bytes()
        hand_write(paths, [A, C, B])
        nested.hyprctl_text("reload")
        await settle()
        (paths.app_dir / BINDS).write_bytes(app_bytes)
        nested.hyprctl_text("reload")
        await settle()
        record(artifacts, "b-reverted", nested, paths)

        assert session.replace_bind(1, DRAFT)
        await settle()

        record(artifacts, "b-saved", nested, paths)
        assert live_keys(nested) == ["A", "D", "C"]

    run(harness_home, artifacts, scenario)
