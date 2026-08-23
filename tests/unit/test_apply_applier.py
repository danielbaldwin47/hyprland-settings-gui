"""The composed pipeline, and the one thing only the composition can get wrong.

`Applier` is three objects wired in the one order that works, so what is worth testing here
is the wiring: an edit really does reach disk and the compositor through the queue, and a
`configreloaded` is attributed to the app or to somebody else by the *transaction's*
in-flight flag rather than by anything coarser.

Foreign-reload correlation is the failure that would never show up in a unit test of any one
piece: correlate on "the queue has work" instead and the app stops noticing a Bridge tool
that reloads while a transaction is still rendering.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from _fake_hyprland import FakeHyprland, model_conversation, run_with_fake
from _support import SAMPLE_APP_VERSION, SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.apply import Applier, ApplyOutcome, ApplyResult
from hyprtweaker.engine.ipc import MONITOR_ADDED, RELOAD_STARTED, CommandClient, EventStream
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.writer import Writer

T = TypeVar("T")

SETTLE = 0.05
"""Long enough for a pushed event line to cross a loopback socket and be dispatched."""


def model_with(**values: object) -> ConfigModel:
    model = ConfigModel(load_schema(SAMPLE_VERSION, SCHEMA_DIR))
    for key, value in values.items():
        model.set(key.replace("__", ":"), value)
    return model


def with_applier(
    tmp_path: Path,
    model: ConfigModel,
    scenario: Callable[[Applier, FakeHyprland], Awaitable[T]],
    *,
    on_result: Callable[[ApplyResult], None] | None = None,
    on_foreign_reload: Callable[[], None] = lambda: None,
    debounce: float = 0.01,
) -> T:
    """Run `scenario` against a started Applier over a scripted compositor."""
    fake = FakeHyprland(model_conversation(model), reload_emits_event=True)

    async def main(started: FakeHyprland) -> T:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            async with Applier(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                on_result=on_result,
                on_foreign_reload=on_foreign_reload,
                debounce=debounce,
                reload_timeout=0.5,
            ) as applier:
                return await scenario(applier, started)

    return run_with_fake(main, fake)


# --- the wiring ------------------------------------------------------------------------------


def test_an_edit_reaches_disk_and_the_compositor(tmp_path: Path) -> None:
    model = model_with(decoration__rounding=10)

    async def scenario(applier: Applier, _: FakeHyprland) -> ApplyResult:
        return await applier.apply("decoration:rounding")

    result = with_applier(tmp_path, model, scenario)

    assert result.outcome is ApplyOutcome.OK
    module = ConfigPaths.rooted_at(tmp_path).app_dir / "options" / "decoration.lua"
    assert "rounding = 10" in module.read_text()


def test_a_touched_burst_becomes_one_reload(tmp_path: Path) -> None:
    """The whole point of the pipeline, exercised through the object the app actually holds."""
    model = model_with(general__gaps_in=5, general__gaps_out=10, decoration__rounding=10)

    async def scenario(applier: Applier, _: FakeHyprland) -> None:
        applier.touch("general:gaps_in")
        applier.touch("general:gaps_out")
        applier.touch("decoration:rounding")
        await applier.drain()

    fake = FakeHyprland(model_conversation(model), reload_emits_event=True)

    async def main(started: FakeHyprland) -> list[str]:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            async with Applier(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                on_foreign_reload=lambda: None,
                debounce=0.01,
                reload_timeout=0.5,
            ) as applier:
                await scenario(applier, started)
        return started.requests

    requests = run_with_fake(main, fake)

    assert requests.count("reload") == 1


def test_a_drag_previews_per_tick_and_costs_exactly_one_reload(tmp_path: Path) -> None:
    """ADR-0010's Eval preview tier, through the object the app holds.

    The acceptance criterion for the continuous controls, stated as the compositor sees it:
    every tick of the gesture is an `eval` and the whole drag buys exactly one reload -- the
    one the release commits. Ten reloads instead of one is not a slow drag, it is ten full
    teardowns of the compositor's config state.
    """
    model = model_with(general__gaps_in=1)

    async def main(started: FakeHyprland) -> list[str]:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            async with Applier(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                on_foreign_reload=lambda: None,
                debounce=0.01,
                reload_timeout=0.5,
            ) as applier:
                for gap in range(2, 12):
                    model.set("general:gaps_in", gap)
                    applier.preview("general:gaps_in")
                    # One turn of the loop per tick, so the worker really does send each
                    # one -- coalescing is tested where it belongs, and hiding behind it
                    # here would make this pass without a single preview being sent.
                    await applier.flush_previews()
                applier.commit("general:gaps_in")
                await applier.drain()
        return started.requests

    model.set("general:gaps_in", 11)
    fake = FakeHyprland(model_conversation(model), reload_emits_event=True)
    model.set("general:gaps_in", 1)
    requests = run_with_fake(main, fake)

    assert requests.count("reload") == 1
    assert len([one for one in requests if one.startswith("eval ")]) == 10


def test_a_preview_never_lands_between_a_reload_and_its_read_back(tmp_path: Path) -> None:
    """`eval` clears `configerrors`, so a tick arriving mid-transaction would erase the
    errors that transaction is about to read -- and a rejected config would report clean."""
    model = model_with(general__gaps_in=5)

    async def main(started: FakeHyprland) -> list[str]:
        async with EventStream(started.instance) as events:
            await started.wait_for_listeners(1)
            async with Applier(
                model=model,
                writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                client=CommandClient(started.instance),
                events=events,
                on_foreign_reload=lambda: None,
                debounce=0.01,
                reload_timeout=0.5,
            ) as applier:
                applying = asyncio.create_task(applier.apply("general:gaps_in"))
                # Ticking while that transaction runs is exactly the race: the drag has not
                # stopped just because the release's write is in flight.
                while not applier.busy:
                    await asyncio.sleep(0)
                applier.preview("general:gaps_in")
                # A real turn of the loop, so the preview worker genuinely wakes up and
                # decides -- without it this could pass by the tick never being considered.
                await asyncio.sleep(0.01)
                assert applier.busy, "the transaction must still be running when it decided"
                await applying
                await applier.flush_previews()
        return started.requests

    requests = run_with_fake(
        main,
        FakeHyprland(model_conversation(model), reload_emits_event=True, reply_delay=0.02),
    )

    assert [one for one in requests if one.startswith("eval ")] == []
    assert "j/configerrors" in requests, "the transaction did get to read its errors"


def test_results_reach_the_subscriber(tmp_path: Path) -> None:
    model = model_with(decoration__rounding=10)
    seen: list[ApplyResult] = []

    async def scenario(applier: Applier, _: FakeHyprland) -> None:
        applier.touch("decoration:rounding")
        await applier.drain()

    with_applier(tmp_path, model, scenario, on_result=seen.append)

    assert [result.outcome for result in seen] == [ApplyOutcome.OK]


# --- foreign reloads -------------------------------------------------------------------------


class TestForeignReloads:
    """AC-adjacent (ADR-0010): an uncorrelated `configreloaded` triggers a full re-read."""

    def test_somebody_elses_reload_is_reported(self, tmp_path: Path) -> None:
        """A Bridge tool, a hand edit, or a `hyprctl reload` from a script."""
        model = model_with(decoration__rounding=10)
        foreign: list[int] = []

        async def scenario(_: Applier, fake: FakeHyprland) -> None:
            await fake.emit(RELOAD_STARTED)
            await asyncio.sleep(SETTLE)

        with_applier(tmp_path, model, scenario, on_foreign_reload=lambda: foreign.append(1))

        assert foreign == [1]

    def test_our_own_reload_is_not(self, tmp_path: Path) -> None:
        """Otherwise every successful apply would trigger a full re-read of the config."""
        model = model_with(decoration__rounding=10)
        foreign: list[int] = []

        async def scenario(applier: Applier, _: FakeHyprland) -> None:
            await applier.apply("decoration:rounding")
            await asyncio.sleep(SETTLE)

        with_applier(tmp_path, model, scenario, on_foreign_reload=lambda: foreign.append(1))

        assert foreign == []

    def test_a_reload_after_ours_is_foreign_again(self, tmp_path: Path) -> None:
        """The flag has to clear: a stuck `True` deafens the app for the rest of the session."""
        model = model_with(decoration__rounding=10)
        foreign: list[int] = []

        async def scenario(applier: Applier, fake: FakeHyprland) -> None:
            await applier.apply("decoration:rounding")
            await fake.emit(RELOAD_STARTED)
            await asyncio.sleep(SETTLE)

        with_applier(tmp_path, model, scenario, on_foreign_reload=lambda: foreign.append(1))

        assert foreign == [1]

    def test_other_events_are_ignored(self, tmp_path: Path) -> None:
        model = model_with(decoration__rounding=10)
        foreign: list[int] = []

        async def scenario(_: Applier, fake: FakeHyprland) -> None:
            await fake.emit(MONITOR_ADDED, "1,DP-1,Dell Inc. U2723QE")
            await asyncio.sleep(SETTLE)

        with_applier(tmp_path, model, scenario, on_foreign_reload=lambda: foreign.append(1))

        assert foreign == []

    def test_closing_stops_the_watch(self, tmp_path: Path) -> None:
        """A closed Applier must not keep calling back into a UI that is being torn down."""
        model = model_with(decoration__rounding=10)
        foreign: list[int] = []
        fake = FakeHyprland(model_conversation(model), reload_emits_event=True)

        async def main(started: FakeHyprland) -> None:
            async with EventStream(started.instance) as events:
                await started.wait_for_listeners(1)
                applier = Applier(
                    model=model,
                    writer=Writer(ConfigPaths.rooted_at(tmp_path), SAMPLE_APP_VERSION),
                    client=CommandClient(started.instance),
                    events=events,
                    on_foreign_reload=lambda: foreign.append(1),
                    debounce=0.01,
                )
                applier.start()
                await applier.aclose()
                await started.emit(RELOAD_STARTED)
                await asyncio.sleep(SETTLE)

        run_with_fake(main, fake)

        assert foreign == []
