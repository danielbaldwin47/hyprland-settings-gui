"""The Session loads the Schema for the Hyprland that is running (#176, ADR-0012 §Pinning).

The compositor is a scripted socket answering `j/version`, and the shipped schemas are a
synthetic directory, so "between two shipped schemas" stays true whatever `data/schema`
ships. The default reader blocks, and the fake serves on the test's loop, so the Session is
built on a worker thread, as the GTK app builds it before its loop runs anything.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from _fake_hyprland import CONVERSATION, FakeHyprland, run_with_fake
from _support import SAMPLE_APP_VERSION, Runner, synthetic_schema_dir

from hyprtweaker.engine.ipc import Instance, LiveHyprland, NoInstance
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema.resolve import SCHEMA_DIR_ENV
from hyprtweaker.session import Session

SHIPPED = ("0.56.2", "0.58.0")


@pytest.fixture(autouse=True)
def shipped_schemas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SCHEMA_DIR_ENV, str(synthetic_schema_dir(tmp_path / "schema", *SHIPPED)))


def running(version: str) -> FakeHyprland:
    reply = json.dumps({"version": version, "tag": f"v{version}", "flags": []})
    return FakeHyprland({**CONVERSATION, "j/version": reply})


def session_against(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    """Must be called off the fake's loop: the default reader blocks."""
    return Session(
        spawn=runner.spawn,
        paths=ConfigPaths.rooted_at(root),
        app_version=SAMPLE_APP_VERSION,
        connect=lambda: fake.instance,
    )


def built(fake: FakeHyprland, root: Path) -> Session:
    async def scenario(started: FakeHyprland) -> Session:
        return await asyncio.to_thread(session_against, started, root, Runner())

    return run_with_fake(scenario, fake)


def test_a_compositor_between_two_shipped_schemas_gets_the_lower(tmp_path: Path) -> None:
    session = built(running("0.57.1"), tmp_path)

    assert session.schema.hyprland_version == "0.56.2"
    assert session.live_hyprland is not None
    assert session.live_hyprland.version == "0.57.1"
    assert "general:gaps_in" in session.live_hyprland.names


def test_a_compositor_older_than_every_shipped_schema_gets_the_oldest(tmp_path: Path) -> None:
    session = built(running("0.56.0"), tmp_path)

    assert session.schema.hyprland_version == "0.56.2"
    assert session.live_hyprland is not None
    assert session.live_hyprland.version == "0.56.0"


def test_a_compositor_that_never_answers_gets_the_newest(tmp_path: Path) -> None:
    session = built(FakeHyprland(never_answer=True), tmp_path)

    assert session.schema.hyprland_version == "0.58.0"
    assert session.live_hyprland is None


def test_a_git_build_gets_the_newest_and_no_snapshot(tmp_path: Path) -> None:
    session = built(running("0.57.0-12-gabcdef"), tmp_path)

    assert session.schema.hyprland_version == "0.58.0"
    assert session.live_hyprland is None


def test_no_compositor_gets_the_newest(tmp_path: Path) -> None:
    def no_instance() -> Instance:
        raise NoInstance("not running under Hyprland")

    session = Session(
        spawn=Runner().spawn,
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=SAMPLE_APP_VERSION,
        connect=no_instance,
    )

    assert session.schema.hyprland_version == "0.58.0"
    assert session.live_hyprland is None


def test_an_injected_snapshot_picks_the_schema_and_is_what_the_session_exposes(
    tmp_path: Path,
) -> None:
    live = LiveHyprland("0.57.1", ({"name": "general:border_size"},))

    session = Session(
        spawn=Runner().spawn,
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=SAMPLE_APP_VERSION,
        read_live=lambda: live,
    )

    assert session.schema.hyprland_version == "0.56.2"
    assert session.live_hyprland is live


def test_below_lua_hyprland_the_session_stays_read_only_and_never_connects(
    tmp_path: Path,
) -> None:
    """A read-only state that flipped live on `start()` would let edits reach a compositor
    this app cannot write a config for."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await asyncio.to_thread(session_against, fake, tmp_path, runner)
        session.start()
        await runner.settle()

        assert session.live is False
        assert session.health.title == (
            "Hyprland 0.55.0 is running, and this app needs Hyprland 0.56 or newer"
            " — settings are read-only."
        )
        assert fake.requests == ["j/version", "j/descriptions"]
        assert session.schema.hyprland_version == "0.56.2"

    run_with_fake(scenario, running("0.55.0"))
