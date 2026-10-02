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
from _support import (
    SAMPLE_APP_VERSION,
    Runner,
    sample_schema,
    section_conversation,
    session_for,
    synthetic_schema_dir,
)

from hyprtweaker.engine.ipc import Instance, LiveHyprland, NoInstance
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema.resolve import SCHEMA_DIR_ENV
from hyprtweaker.session import Session
from hyprtweaker.ui.rows.state import row_state

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


NEW_SIZE = {
    "name": "general:new_size",
    "description": "a size this Hyprland added",
    "default": 3,
    "current": 3,
    "min": 0,
    "max": 20,
    "map": None,
}
SNAP_NEW = {"name": "general:snap_new", "description": "snap new windows", "default": False}


def _described_with_extras(version: str) -> LiveHyprland:
    """Every shipped 0.56.2 option, plus two this Hyprland added and one plugin's."""
    plugin = {"name": "plugin:hyprbars:bar_height", "description": "x", "default": 15}
    shipped = tuple({"name": option.name} for option in sample_schema())
    return LiveHyprland(version, (*shipped, NEW_SIZE, SNAP_NEW, plugin))


def test_a_newer_hyprland_adds_its_new_options_and_keeps_the_shipped_schema_version(
    tmp_path: Path,
) -> None:
    """ADR-0012 §Pinning: newer than every shipped schema (0.58.0 here), so the options
    0.59.0 describes beyond the loaded schema join it, settable and written like any other;
    the Manifest still names the shipped schema, because the supplement is not one."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        live = _described_with_extras("0.59.0")
        session = session_for(fake, tmp_path, runner, live_hyprland=live)
        session.start()
        await runner.settle()

        assert {option.name for option in session.schema} - {
            option.name for option in sample_schema()
        } == {"general:new_size", "general:snap_new"}
        session.set_option("general:new_size", 7)
        await session.aclose()

        module = tmp_path / "hypr" / "hyprtweaker" / "options" / "general.lua"
        assert "new_size = 7" in module.read_text()
        paths = ConfigPaths.rooted_at(tmp_path)
        assert json.loads(paths.manifest.read_text())["schema_version"] == "0.56.2"

    reply = {"option": "general:new_size", "set": True, "int": 7}
    run_with_fake(
        scenario,
        FakeHyprland(
            {
                **section_conversation("general"),
                "j/getoption general:new_size": json.dumps(reply),
            },
            reload_emits_event=True,
        ),
    )


@pytest.mark.parametrize("version", ["0.58.0", "0.57.1"])
def test_at_or_below_the_newest_shipped_schema_nothing_is_added(
    tmp_path: Path, version: str
) -> None:
    session = session_for(
        FakeHyprland(), tmp_path, Runner(), live_hyprland=_described_with_extras(version)
    )

    assert "general:new_size" not in session.schema
    assert len(session.schema) == len(sample_schema())


def _described_without(missing: str) -> LiveHyprland:
    return LiveHyprland(
        "0.56.0", tuple({"name": o.name} for o in sample_schema() if o.name != missing)
    )


def test_a_row_names_the_option_the_running_hyprland_lacks(tmp_path: Path) -> None:
    """#77's unknown-to-this-version badge, through the real Session's `RowContext`."""
    live = _described_without("decoration:rounding")
    session = session_for(FakeHyprland(), tmp_path, Runner(), live_hyprland=live)
    rounding = session.schema["decoration:rounding"]

    assert session.unknown_to_version(rounding)
    assert not session.unknown_to_version(session.schema["general:gaps_in"])
    (pill,) = row_state(rounding, session).pills
    assert (pill.label, pill.tooltip) == (
        "Not in this Hyprland",
        "Hyprland 0.56.0 does not have this option; the app is using its 0.56.2 schema.",
    )


def test_with_no_compositor_described_no_row_wears_the_pill(tmp_path: Path) -> None:
    """No snapshot is no evidence: an offline session badges nothing as missing."""
    session = session_for(FakeHyprland(), tmp_path, Runner(), live_hyprland=None)

    labels = {
        pill.label for option in session.schema for pill in row_state(option, session).pills
    }

    assert not any(session.unknown_to_version(option) for option in session.schema)
    assert "Not in this Hyprland" not in labels
    assert labels >= {"Advanced", "Restart"}, "the other pills still show"
