"""The shell tracer against a real compositor: a UI edit changes Hyprland, and survives.

`tests/unit/test_session.py` drives the same object against a scripted socket and proves the
*sequence*. Only a compositor can answer the two things this ticket actually promises:

* **a change is visible in the live compositor immediately** -- the value the model held is
  the value Hyprland holds, after nothing but `Session.set_option`;
* **values round-trip truthfully after close and reopen** -- a second `Session` over the same
  App dir recovers what the first one wrote, from the compositor that loaded it.

`Session` is what the window calls, so this is the app's own path with the widgets left off.
Nothing is stubbed: the real `Writer`, the real `Applier`, the real sockets.

    pytest tests/integration/test_shell_session.py -m hyprland
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

import pytest
from harness import NestedHyprland, write_determinism_preamble
from harness.state import SCHEMA_DIR, SCHEMA_VERSION

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult
from hyprtweaker.engine.model import CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.session import Session

pytestmark = pytest.mark.hyprland

APP_VERSION = "0.0.0-harness"
SCHEMA = load_schema(SCHEMA_VERSION, SCHEMA_DIR)

GAPS_IN = "general:gaps_in"
ROUNDING = "decoration:rounding"

BEFORE = {GAPS_IN: 2, ROUNDING: 0}
AFTER_GAPS = 24
AFTER_ROUNDING = 18


def config_root(home: Path) -> ConfigPaths:
    """An App dir inside the nested compositor's own `$HOME`.

    The determinism preamble is written before anything else: the Entrypoint only requires
    files that exist when it is rendered, so a `user.lua` created later never loads.
    """
    paths = ConfigPaths.rooted_at(home / ".config")
    paths.hypr_dir.mkdir(parents=True, exist_ok=True)
    write_determinism_preamble(paths.user_lua)
    return paths


class Loop:
    """Runs one `Session`'s work on a plain asyncio loop.

    The app hands `Session` the GTK main loop's scheduler (`ui/shell/runtime.py`); the object
    itself only ever asks for "run this coroutine", so a bare loop is the same seam without a
    display.
    """

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def settle(self) -> None:
        while self._tasks:
            batch, self._tasks = self._tasks, []
            await asyncio.gather(*batch)


async def run_session(
    nested: NestedHyprland, paths: ConfigPaths, edits: dict[str, Any]
) -> tuple[dict[str, Any], list[ApplyResult]]:
    """Open a session, apply `edits`, close it. Returns the recovered model and results.

    The recovered model is read *before* the edits, which is what makes this usable for both
    halves of the round trip: the first run recovers nothing and writes, the second recovers
    what the first wrote.
    """
    loop = Loop()
    results: list[ApplyResult] = []

    session = Session(
        spawn=loop.spawn,
        schema=SCHEMA,
        paths=paths,
        app_version=APP_VERSION,
        connect=lambda: nested.instance,
    )
    session.on_applied = results.append
    session.start()
    await loop.settle()

    assert session.live, f"the session did not connect: {session.offline_reason}"
    recovered = {option.name: value for option, value in session.model.set_options()}

    for name, value in edits.items():
        session.set_option(name, value)
    await session.aclose()

    return recovered, results


def start_nested(paths: ConfigPaths, home: Path, log: Path) -> NestedHyprland:
    """A nested compositor on a config the Writer produced, with the App dir already there."""
    return NestedHyprland(paths.entrypoint, home=home, log=log)


def test_an_edit_made_through_the_session_lands_in_the_live_compositor(
    harness_home: Path, artifacts: Path
) -> None:
    paths = config_root(harness_home)

    # A first config so the compositor has an Entrypoint to boot on. Written through a
    # throwaway session against no compositor would be a different code path, so the
    # nested Hyprland starts on a bare Entrypoint and the session writes everything else.
    paths.entrypoint.write_text(f'require("{paths.require_path(paths.user_lua)}")\n')

    with start_nested(paths, harness_home, artifacts / "nested.log") as nested:
        assert nested.config_errors() == ()

        _, results = asyncio.run(
            run_session(nested, paths, {GAPS_IN: AFTER_GAPS, ROUNDING: AFTER_ROUNDING})
        )

        assert results, "the session applied nothing"
        assert all(result.outcome is ApplyOutcome.OK for result in results), (
            f"apply did not confirm: {[result.outcome for result in results]}"
        )
        assert nested.config_errors() == ()

        live = nested.getoptions([GAPS_IN, ROUNDING])
        assert _value(live[ROUNDING]) == AFTER_ROUNDING
        assert _gaps(live[GAPS_IN]) == (AFTER_GAPS,) * 4


def test_values_round_trip_after_the_app_is_closed_and_reopened(
    harness_home: Path, artifacts: Path
) -> None:
    """Close, reopen, and the Rows show what was set -- not the compositor's defaults."""
    paths = config_root(harness_home)
    paths.entrypoint.write_text(f'require("{paths.require_path(paths.user_lua)}")\n')

    with start_nested(paths, harness_home, artifacts / "nested.log") as nested:
        first, _ = asyncio.run(run_session(nested, paths, BEFORE | {GAPS_IN: AFTER_GAPS}))
        assert first == {}, "a fresh App dir must adopt nothing (spec story 13)"

        second, _ = asyncio.run(run_session(nested, paths, {}))

        assert second[GAPS_IN] == CssGaps(AFTER_GAPS, AFTER_GAPS, AFTER_GAPS, AFTER_GAPS)
        assert second[ROUNDING] == BEFORE[ROUNDING]
        assert set(second) == set(BEFORE), "and nothing beyond what the app itself wrote"


def _value(record: Any) -> Any:
    from harness.state import option_value

    return option_value(record)


def _gaps(record: Any) -> tuple[int, int, int, int]:
    """The four sides of a css-gaps reply, whichever key this engine reported it under."""
    payload = record.get("css", record.get("custom"))
    if isinstance(payload, str):
        top, right, bottom, left = (int(part) for part in payload.split())
        return top, right, bottom, left
    return (payload["top"], payload["right"], payload["bottom"], payload["left"])
