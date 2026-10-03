"""`Session.entities_unreadable`: whether the Entity lists say "none yet" truthfully (#269).

An empty list is a claim about the user's config. It is true when the lists were read
(live, or off the files offline with Lua) and on a fresh install with no App dir, and false
when an App dir exists and its lists could not be read: no Lua, a Hyprland too old to
connect to, an import still on offer, or a Module that would not load. In those cases the
pages say the data cannot be read, with the cause.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import (
    SAMPLE_APP_VERSION,
    Runner,
    sample_schema,
    section_conversation,
    session_for,
)

from hyprtweaker.engine.importer.lua.sandbox import lua_binary
from hyprtweaker.engine.ipc import Instance, LiveHyprland, NoInstance
from hyprtweaker.engine.migration.flow import fresh_start
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.session import Session

needs_lua = pytest.mark.skipif(lua_binary() is None, reason="no Lua interpreter installed")

NO_LUA = (
    "This app reads your settings with Lua, which is not installed. Install Lua (lua5.5, "
    "lua5.4, lua5.3, lua or luajit) and open the app again."
)


def offline_session(root: Path, runner: Runner, *, live: LiveHyprland | None = None) -> Session:
    def no_compositor() -> Instance:
        raise NoInstance("headless")

    return Session(
        spawn=runner.spawn,
        schema=sample_schema(),
        paths=ConfigPaths.rooted_at(root),
        app_version=SAMPLE_APP_VERSION,
        connect=no_compositor,
        read_live=lambda: live,
    )


def app_config(root: Path) -> None:
    fresh_start(ConfigPaths.rooted_at(root), sample_schema(), app_version=SAMPLE_APP_VERSION)


def started(session: Session, runner: Runner) -> Session:
    async def go() -> None:
        session.start()
        await runner.settle()

    asyncio.run(go())
    return session


def test_without_lua_an_app_config_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_config(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    runner = Runner()
    session = started(offline_session(tmp_path, runner), runner)
    assert session.entities_unreadable == NO_LUA


def test_without_lua_a_fresh_install_keeps_its_none_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No App dir: there is nothing to read, so "none yet" is true."""
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    runner = Runner()
    session = started(offline_session(tmp_path, runner), runner)
    assert not session.live
    assert session.entities_unreadable is None


@needs_lua
def test_offline_with_lua_the_lists_are_read_off_the_files(tmp_path: Path) -> None:
    app_config(tmp_path)
    runner = Runner()
    session = started(offline_session(tmp_path, runner), runner)
    assert session.offline_sentence == "This app is not connected to Hyprland."
    assert session.entities_unreadable is None


def test_a_hyprland_too_old_to_connect_to_reads_no_lists(tmp_path: Path) -> None:
    app_config(tmp_path)
    runner = Runner()
    old = LiveHyprland("0.54.0", ())
    session = started(offline_session(tmp_path, runner, live=old), runner)
    assert session.entities_unreadable == (
        "This app cannot read your settings right now. Hyprland 0.54.0 is running, and this "
        "app needs Hyprland 0.56 or newer."
    )


def test_an_import_on_offer_over_an_unread_app_config_says_so(tmp_path: Path) -> None:
    app_config(tmp_path)
    session = offline_session(tmp_path, Runner())
    session.set_read_only(
        "You are still on hyprland.conf, so settings can't be saved yet.",
        sentence="Your config has not been converted yet: use Convert... at the top of the "
        "window.",
    )
    assert session.entities_unreadable == (
        "This app cannot read your settings right now. Your config has not been converted "
        "yet: use Convert... at the top of the window."
    )


def test_an_import_on_offer_with_no_app_config_keeps_its_none_yet(tmp_path: Path) -> None:
    session = offline_session(tmp_path, Runner())
    session.set_read_only("You are still on hyprland.conf, so settings can't be saved yet.")
    assert session.entities_unreadable is None


def test_while_connecting_nothing_is_claimed_yet(tmp_path: Path) -> None:
    """Before `start` has answered, the lists are about to load: no flash of "cannot"."""
    app_config(tmp_path)
    session = offline_session(tmp_path, Runner())
    assert session.offline_reason == "Connecting to Hyprland…"
    assert session.entities_unreadable is None


@needs_lua
def test_live_with_a_module_that_would_not_load(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        app_config(tmp_path)
        (tmp_path / "hypr" / "hyprtweaker" / "binds.lua").write_text("this is not lua (\n")
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live, session.offline_reason
        assert session.entities_unreadable == (
            "This app cannot read your settings right now. One of its files would not load, "
            "so it is left as it is."
        )

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


@needs_lua
def test_live_with_every_module_read(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        app_config(tmp_path)
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live, session.offline_reason
        assert session.entities_unreadable is None

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))
