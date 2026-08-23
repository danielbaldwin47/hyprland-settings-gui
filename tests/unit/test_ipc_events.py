"""The socket2 event stream: delivery, arming, and what `configreloaded` does not mean.

The fake pushes real lines down a real unix socket, so these cover the framing (`>>`,
per-line dispatch) as well as the fan-out.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from _fake_hyprland import CONFIG_ERRORS, FakeHyprland

from hyprtweaker.engine.ipc import (
    MONITOR_ADDED,
    MONITOR_REMOVED,
    RELOAD_STARTED,
    CommandClient,
    Event,
    EventStream,
    SocketUnavailable,
)

T = TypeVar("T")

SETTLE = 0.05
"""Long enough for a pushed line to cross a loopback unix socket and be dispatched. The
tests that assert an event *arrived* do not depend on it -- they await the arrival -- so it
only paces the ones asserting an event was correctly filtered out."""


def run(scenario: Callable[[EventStream, FakeHyprland], Awaitable[T]]) -> T:
    async def main() -> T:
        async with FakeHyprland() as fake, EventStream(fake.instance) as stream:
            return await scenario(stream, fake)

    return asyncio.run(main())


# --- delivery -----------------------------------------------------------------------------


def test_events_surface_as_engine_callbacks() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        seen: list[Event] = []
        stream.subscribe(seen.append)

        await fake.emit(RELOAD_STARTED)
        await fake.emit(MONITOR_ADDED, "1,DP-1,Dell Inc. U2723QE")
        await asyncio.sleep(SETTLE)

        assert seen == [
            Event(name="configreloaded", data=""),
            Event(name="monitoraddedv2", data="1,DP-1,Dell Inc. U2723QE"),
        ]

    run(scenario)


def test_a_subscriber_can_name_the_events_it_wants() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        hotplug: list[Event] = []
        stream.subscribe(hotplug.append, MONITOR_ADDED, MONITOR_REMOVED)

        await fake.emit(RELOAD_STARTED)
        await fake.emit("activewindow", "kitty,fish")
        await fake.emit(MONITOR_REMOVED, "1,DP-1,Dell Inc. U2723QE")
        await asyncio.sleep(SETTLE)

        assert [event.name for event in hotplug] == [MONITOR_REMOVED]

    run(scenario)


def test_unsubscribing_stops_delivery() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        seen: list[Event] = []
        unsubscribe = stream.subscribe(seen.append)

        await fake.emit(RELOAD_STARTED)
        await asyncio.sleep(SETTLE)
        unsubscribe()
        await fake.emit(RELOAD_STARTED)
        await asyncio.sleep(SETTLE)

        assert len(seen) == 1

    run(scenario)


def test_data_keeps_everything_after_the_first_separator() -> None:
    """Window titles are user text and can contain anything, `>>` included."""

    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        seen: list[Event] = []
        stream.subscribe(seen.append)

        await fake.emit("windowtitlev2", "5934c50,git log >> out.txt")
        await asyncio.sleep(SETTLE)

        assert seen == [Event(name="windowtitlev2", data="5934c50,git log >> out.txt")]

    run(scenario)


def test_a_line_that_is_not_an_event_is_dropped_not_fatal() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        seen: list[Event] = []
        stream.subscribe(seen.append)

        await fake.emit_raw("garbage with no separator\n")
        await fake.emit(RELOAD_STARTED)
        await asyncio.sleep(SETTLE)

        assert [event.name for event in seen] == [RELOAD_STARTED]
        assert stream.running

    run(scenario)


def test_one_failing_subscriber_does_not_cost_the_app_its_stream() -> None:
    """Hyprland drops a listener that stops draining, so a dead reader means going deaf to
    every later event -- much worse than the exception that caused it."""

    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        def explode(event: Event) -> None:
            raise RuntimeError("subscriber bug")

        seen: list[Event] = []
        stream.subscribe(explode)
        stream.subscribe(seen.append)

        await fake.emit(RELOAD_STARTED)
        await fake.emit(MONITOR_ADDED, "1,DP-1,")
        await asyncio.sleep(SETTLE)

        assert [event.name for event in seen] == [RELOAD_STARTED, MONITOR_ADDED]
        assert stream.running

    run(scenario)


# --- arming -------------------------------------------------------------------------------


def test_arming_catches_an_event_that_arrives_immediately() -> None:
    """The race this exists for: a reload can finish before a naive `wait` starts."""

    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        with stream.arm(RELOAD_STARTED) as reloaded:
            await fake.emit(RELOAD_STARTED)
            assert await reloaded.wait(timeout=1.0) == Event(name=RELOAD_STARTED, data="")

    run(scenario)


def test_a_waiter_that_never_hears_back_reports_a_timeout_as_a_value() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        with stream.arm(RELOAD_STARTED) as reloaded:
            await fake.emit("activewindow", "kitty,fish")
            assert await reloaded.wait(timeout=SETTLE) is None

    run(scenario)


def test_a_closed_waiter_stops_listening() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        reloaded = stream.arm(RELOAD_STARTED)
        reloaded.close()
        reloaded.close()  # idempotent

        await fake.emit(RELOAD_STARTED)
        assert await reloaded.wait(timeout=SETTLE) is None

    run(scenario)


def test_reload_started_is_not_apply_done() -> None:
    """The whole reason the constant is not called `applied`.

    `configreloaded` fires at the end of every reload, rejected config included. Here the
    reload reports back and the config is broken -- the only thing that says so is the
    `configerrors` read afterwards (ADR-0010 step 5).
    """

    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        fake.conversation["j/configerrors"] = CONFIG_ERRORS
        client = CommandClient(fake.instance)

        with stream.arm(RELOAD_STARTED) as reloaded:
            await client.reload()
            await fake.emit(RELOAD_STARTED)
            assert await reloaded.wait(timeout=1.0) is not None

        assert await client.configerrors(), "the reload reported back, and it failed"

    run(scenario)


# --- lifecycle ----------------------------------------------------------------------------


def test_losing_the_compositor_is_reported_once() -> None:
    async def main() -> None:
        lost: list[bool] = []
        async with FakeHyprland() as fake:
            stream = EventStream(fake.instance, on_lost=lambda: lost.append(True))
            await stream.start()
            await fake.wait_for_listeners(1)
            await fake.drop_listeners()
            await asyncio.sleep(SETTLE)

            assert lost == [True]
            assert not stream.running
            await stream.aclose()

    asyncio.run(main())


def test_closing_is_safe_twice_and_disconnects() -> None:
    async def main() -> None:
        async with FakeHyprland() as fake:
            stream = EventStream(fake.instance)
            await stream.start()
            await fake.wait_for_listeners(1)

            await stream.aclose()
            await stream.aclose()
            await fake.wait_for_listeners(0)

    asyncio.run(main())


def test_starting_twice_is_a_programming_error() -> None:
    async def scenario(stream: EventStream, fake: FakeHyprland) -> None:
        with pytest.raises(RuntimeError):
            await stream.start()

    run(scenario)


def test_no_event_socket_is_unavailable() -> None:
    async def main() -> None:
        fake = FakeHyprland()
        instance = await fake.start(event_socket=False)
        try:
            with pytest.raises(SocketUnavailable):
                await EventStream(instance).start()
        finally:
            await fake.stop()

    asyncio.run(main())
