"""The blocking startup read of the running compositor's version and descriptions (#176).

The fake serves on the test's own event loop, and the reader blocks, so every read runs on
a worker thread (`asyncio.to_thread`): called on the loop's thread it would sit on the very
loop that has to answer it, and time out.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from _fake_hyprland import CONVERSATION, FakeHyprland, run_with_fake

from hyprtweaker.engine.ipc import (
    Instance,
    LiveHyprland,
    NoInstance,
    read_live_hyprland,
)
from hyprtweaker.engine.ipc.live import parse_release_version


def read_from(fake: FakeHyprland, *, timeout: float = 1.0) -> LiveHyprland | None:
    async def scenario(started: FakeHyprland) -> LiveHyprland | None:
        return await asyncio.to_thread(
            read_live_hyprland, lambda: started.instance, timeout=timeout
        )

    return run_with_fake(scenario, fake)


def test_the_captured_replies_read_as_a_snapshot() -> None:
    live = read_from(FakeHyprland())

    assert live is not None
    assert live.version == "0.56.2"
    assert live.names == frozenset({"general:border_size", "general:gaps_in"})
    assert live.descriptions[1]["default"] == "5 5 5 5"


def test_names_come_from_the_records() -> None:
    live = LiveHyprland("0.57.0", ({"name": "a:b"}, {"name": "c:d", "default": 1}))

    assert live.names == frozenset({"a:b", "c:d"})


def test_no_compositor_reads_as_none() -> None:
    def no_instance() -> Instance:
        raise NoInstance("not running under Hyprland")

    assert read_live_hyprland(no_instance) is None


def test_a_socket_nobody_listens_on_reads_as_none(tmp_path: Path) -> None:
    assert read_live_hyprland(lambda: Instance(tmp_path)) is None


def test_a_compositor_that_never_answers_reads_as_none_within_the_timeout() -> None:
    started = time.monotonic()

    assert read_from(FakeHyprland(never_answer=True), timeout=0.2) is None
    assert time.monotonic() - started < 1.0


def test_one_deadline_covers_both_requests() -> None:
    """Each reply alone fits the budget, the two together do not: one deadline, not two."""
    assert read_from(FakeHyprland(reply_delay=0.15), timeout=1.0) is not None
    assert read_from(FakeHyprland(reply_delay=0.15), timeout=0.25) is None


@pytest.mark.parametrize(
    ("request_", "reply"),
    [
        ("j/version", '{"branch": "main", "flags": []}'),
        ("j/version", '{"version": "unknown"}'),
        ("j/version", "not json"),
        ("j/descriptions", "[]"),
        ("j/descriptions", '[{"description": "no name"}]'),
        ("j/descriptions", '[{"name": "a:b", "description": "a "quoted" word"}]'),
    ],
)
def test_a_reply_a_release_would_not_send_reads_as_none(request_: str, reply: str) -> None:
    assert read_from(FakeHyprland({**CONVERSATION, request_: reply})) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.56.2", "0.56.2"),
        ("v0.57.0", "0.57.0"),
        ("0.56.10\n", "0.56.10"),
        ("0.56.2-12-gabcdef", None),
        ("unknown", None),
        ("", None),
    ],
)
def test_only_a_release_number_parses(text: str, expected: str | None) -> None:
    assert parse_release_version(text) == expected
