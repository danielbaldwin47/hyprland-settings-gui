"""A loaded plugin's settings, through the runtime supplement (#175, ADR-0018 §Plugins).

A plugin's settings reach the app only through the running Hyprland's `descriptions`, read
once at startup. Each becomes a flagged Option the user can set; the app writes it guarded,
so a start without the plugin is never a config error the app caused; and while the plugin
is not loaded its value is kept quietly and comes back when the plugin does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, sample_schema, section_conversation, session_for

from hyprtweaker.engine.importer.lua import sandbox
from hyprtweaker.engine.ipc import LiveHyprland
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import SupplementKind
from hyprtweaker.engine.state import Manifest, RetiredValue, RetireReason
from hyprtweaker.session import Notice

BAR_HEIGHT = {"name": "plugin:hyprbars:bar_height", "description": "bar height", "default": 15}
BAR_HEIGHT_REPLY = {"option": "plugin:hyprbars:bar_height", "set": True, "int": 20}


def described(*plugin_records: dict[str, object], version: str = "0.56.2") -> LiveHyprland:
    """The shipped schema's options, as the Hyprland it was generated from describes them,
    plus whatever loaded plugins add."""
    shipped = tuple({"name": option.name} for option in sample_schema())
    return LiveHyprland(version, (*shipped, *plugin_records))


def plugin_module(root: Path) -> Path:
    return ConfigPaths.rooted_at(root).app_dir / "options" / "plugin.lua"


def kept(root: Path) -> dict[str, RetiredValue]:
    paths = ConfigPaths.rooted_at(root)
    return dict(Manifest.load(paths.manifest, app_version="x", schema_version="x").retired)


def conversation() -> dict[str, str]:
    return {
        **section_conversation(),
        "j/getoption plugin:hyprbars:bar_height": json.dumps(BAR_HEIGHT_REPLY),
    }


async def start(fake: FakeHyprland, root: Path, live: LiveHyprland) -> list[Notice]:
    """One app start against `live`, run to quiescence; the notices it raised."""
    runner = Runner()
    session = session_for(fake, root, runner, live_hyprland=live)
    notices: list[Notice] = []
    session.on_notice = notices.append
    session.start()
    await runner.settle()
    await session.aclose()
    return notices


async def set_with_plugin_loaded(fake: FakeHyprland, root: Path) -> None:
    runner = Runner()
    session = session_for(fake, root, runner, live_hyprland=described(BAR_HEIGHT))
    session.start()
    await runner.settle()
    session.set_option("plugin:hyprbars:bar_height", 20)
    await session.aclose()


def test_a_loaded_plugins_setting_is_a_flagged_option_the_user_can_set(tmp_path: Path) -> None:
    """Not gated on version: the running Hyprland is the shipped one, and the plugin's
    setting still joins, flagged as a plugin's, with no `New in` release."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner, live_hyprland=described(BAR_HEIGHT))
        session.start()
        await runner.settle()

        option = session.schema["plugin:hyprbars:bar_height"]
        assert option.supplement is not None
        assert (option.supplement.kind, option.added_in) == (SupplementKind.PLUGIN, None)
        session.set_option("plugin:hyprbars:bar_height", 20)
        await session.aclose()

        module = tmp_path / "hypr" / "hyprtweaker" / "options" / "plugin.lua"
        body = module.read_text().split("\n", 1)[1]
        assert body == (
            "-- Section: plugin\n"
            "-- Each setting applies only while the plugin that adds it is loaded.\n"
            "\n"
            'if hl.get_config("plugin:hyprbars:bar_height") ~= nil then\n'
            "  hl.config({\n"
            "    plugin = {\n"
            "      hyprbars = {\n"
            "        bar_height = 20,\n"
            "      },\n"
            "    },\n"
            "  })\n"
            "end\n"
        )

    run_with_fake(
        scenario,
        FakeHyprland(
            {
                **section_conversation(),
                "j/getoption plugin:hyprbars:bar_height": json.dumps(BAR_HEIGHT_REPLY),
            },
            reload_emits_event=True,
        ),
    )


def test_while_the_plugin_is_not_loaded_its_setting_is_kept_quietly_and_comes_back(
    tmp_path: Path,
) -> None:
    """Settled for #175: no silent loss, no false notice. A start without the plugin keeps
    the value out of the Module and in the Manifest, and says nothing (the setting was not
    removed from Hyprland); the first start with the plugin loaded writes it back."""

    async def scenario(fake: FakeHyprland) -> None:
        await set_with_plugin_loaded(fake, tmp_path)
        assert "bar_height = 20" in plugin_module(tmp_path).read_text()

        assert await start(fake, tmp_path, described()) == []
        assert not plugin_module(tmp_path).exists()
        assert kept(tmp_path) == {
            "plugin:hyprbars:bar_height": RetiredValue(
                "0.56.2", 20, RetireReason.PLUGIN_NOT_LOADED
            )
        }

        assert await start(fake, tmp_path, described(BAR_HEIGHT)) == []
        assert "bar_height = 20" in plugin_module(tmp_path).read_text()
        assert kept(tmp_path) == {}

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_plugin_setting_whose_value_cannot_be_kept_is_not_called_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec #152 review finding 3 lost the value without Lua. Ruling A1 of the #148 review
    (F9): without Lua the session opens read-only, so the value is never written away and
    no notice claims anything was removed."""

    async def scenario(fake: FakeHyprland) -> None:
        await set_with_plugin_loaded(fake, tmp_path)
        monkeypatch.setattr(sandbox, "lua_binary", lambda: None)

        notices = await start(fake, tmp_path, described())

        assert notices == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
