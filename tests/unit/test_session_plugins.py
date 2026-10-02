"""The plugin load list through the Session (#174): edits, undo, read-back, loaded state.

The list is a declarative kind like the seven of #70 (`edit_declarations("plugins", ...)`),
so what is asserted is what is new about it: identity by path, the reorder, the disabled
toggle, that every edit is one undo step with its own title, and that the loaded state is
read from the compositor rather than guessed.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from _fake_hyprland import FakeHyprland, run_with_fake
from _golden import assert_matches_golden
from _support import Runner, drain_events, entity_session, section_conversation, session_for

from hyprtweaker.engine.apply import EntityStep
from hyprtweaker.engine.model import entity_title
from hyprtweaker.engine.model.entities import PluginLoad
from hyprtweaker.session import Session

GOLDEN = Path(__file__).parent.parent / "golden" / "writer"

BARS = PluginLoad("/usr/lib/hyprland-plugins/libhyprbars.so")
EXPO = PluginLoad("/usr/lib/hyprland-plugins/libhyprexpo.so")
SPACE = PluginLoad("/home/me/plugins/Hyprspace.so")

LOADED_REPLY = json.dumps(
    [
        {
            "name": "hyprbars",
            "author": "Vaxry",
            "handle": "56404960b5a0",
            "version": "1.0",
            "description": "Adds title bars to windows.",
        }
    ]
)
"""The shape `hyprctl -j plugin list` answered on a nested 0.56.2 with a plugin loaded."""


def listed(session: Session) -> list[tuple[str, bool]]:
    return [(plugin.path, plugin.enabled) for plugin in session.declarations("plugins")]


def title(session: Session) -> str:
    step = session.last_gesture
    assert isinstance(step, EntityStep)
    return step.title


def plugins_lua(root: Path) -> str:
    return (root / "hypr" / "hyprtweaker" / "plugins.lua").read_text(encoding="utf-8")


# --- edits --------------------------------------------------------------------------------


def test_add_toggle_reorder_and_remove_write_the_expected_module(tmp_path: Path) -> None:
    """Against a scripted compositor, so the bytes compared are what the Writer landed."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live

        for plugin in (BARS, EXPO, SPACE):
            assert session.add_declaration("plugins", plugin)
        assert session.edit_declarations(
            "plugins",
            lambda items: items.__setitem__(1, replace(items[1], enabled=False)),
            title=entity_title("plugins", "disabled"),
        )
        assert session.move_declaration("plugins", 2, 0)
        assert session.remove_declaration("plugins", 1)
        await session.drain()
        await runner.settle()

        assert listed(session) == [(SPACE.path, True), (EXPO.path, False)]
        assert_matches_golden(
            plugins_lua(tmp_path), GOLDEN / "plugins-edited.lua", "plugins.lua after edits"
        )

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


def test_each_edit_is_one_undo_step_titled_for_what_it_did(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    titles = []

    session.add_declaration("plugins", BARS)
    applier.settle()
    titles.append(title(session))
    session.add_declaration("plugins", EXPO)
    applier.settle()
    session.edit_declarations(
        "plugins",
        lambda items: items.__setitem__(0, replace(items[0], enabled=False)),
        title=entity_title("plugins", "disabled"),
    )
    applier.settle()
    titles.append(title(session))
    session.move_declaration("plugins", 1, 0)
    applier.settle()
    titles.append(title(session))
    session.remove_declaration("plugins", 0)
    applier.settle()
    titles.append(title(session))

    assert titles == ["Plugin added", "Plugin disabled", "Plugins reordered", "Plugin removed"]


def test_removing_a_plugin_then_undo_restores_it_in_place(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    for plugin in (BARS, EXPO, SPACE):
        session.add_declaration("plugins", plugin)
        applier.settle()
    session.remove_declaration("plugins", 1)
    applier.settle()
    assert listed(session) == [(BARS.path, True), (SPACE.path, True)]

    assert session.undo()

    assert listed(session) == [(BARS.path, True), (EXPO.path, True), (SPACE.path, True)]


def test_a_path_already_in_the_list_is_refused(tmp_path: Path) -> None:
    """Loading one `.so` twice is not a second plugin; the row the user filled in would
    otherwise vanish into the one already listed."""
    session, _applier = entity_session(tmp_path)
    session.add_declaration("plugins", BARS)

    assert session.add_declaration("plugins", PluginLoad(BARS.path)) is False
    assert listed(session) == [(BARS.path, True)]


def test_a_move_off_the_end_or_onto_itself_records_nothing(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    session.add_declaration("plugins", BARS)
    session.add_declaration("plugins", EXPO)
    applier.settle()

    session.move_declaration("plugins", 0, 5)
    session.move_declaration("plugins", 1, 1)
    applier.settle()

    assert listed(session) == [(BARS.path, True), (EXPO.path, True)]
    assert title(session) == "Plugin added"


# --- read-back ------------------------------------------------------------------------------


def test_a_hand_edited_plugins_module_is_adopted(tmp_path: Path) -> None:
    """The foreign-reload hook, driven directly as `test_session_monitors` drives its own."""
    session, _applier = entity_session(tmp_path)
    module = session.paths.app_dir / "plugins.lua"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(
        "hl.plugin.load('/p/libone.so')\n-- disabled: hl.plugin.load(\"/p/two.so\")\n",
        encoding="utf-8",
    )

    session._reread_declarations()

    assert listed(session) == [("/p/libone.so", True), ("/p/two.so", False)]


def test_a_broken_plugins_module_leaves_every_declarative_list_alone(tmp_path: Path) -> None:
    """The all-or-none gate now spans seven files: a plugins.lua that will not evaluate must
    not license the Writer to prune env.lua, or the reverse."""
    session, _applier = entity_session(tmp_path)
    app = session.paths.app_dir
    app.mkdir(parents=True, exist_ok=True)
    (app / "env.lua").write_text('hl.env("A", "1")\n', encoding="utf-8")
    (app / "plugins.lua").write_text("hl.plugin.load(\n", encoding="utf-8")

    session._reread_declarations()

    assert session.declarations("env") == []
    assert session.declarations("plugins") == []


def test_a_second_configreloaded_after_an_applys_own_reload_changes_nothing(
    tmp_path: Path,
) -> None:
    """Settled for #174: Hyprland loading a plugin can reload again right after the app's
    own reload. That event is somebody else's reload to the app, and it must leave the
    model, the undo stack, the notices and the file as the Apply left them."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        notices: list[object] = []
        session.on_notice = notices.append
        session.start()
        await runner.settle()
        assert session.add_declaration("plugins", BARS)
        await session.drain()
        await runner.settle()
        step = session.last_gesture
        written = plugins_lua(tmp_path)
        rounding = session.model.get("decoration:rounding")
        reads = fake.requests.count("j/configerrors")

        await fake.emit("configreloaded")
        await drain_events(runner)
        await session.drain()
        await runner.settle()

        assert fake.requests.count("j/configerrors") > reads, "the precondition: it was read"

        assert listed(session) == [(BARS.path, True)]
        assert session.model.get("decoration:rounding") == rounding
        assert session.last_gesture is step
        assert plugins_lua(tmp_path) == written
        assert notices == []
        assert not session.health.unhealthy
        assert session.undo()
        await session.drain()
        await runner.settle()
        assert listed(session) == []
        assert not session.can_undo

    run_with_fake(
        scenario,
        FakeHyprland(section_conversation("general", "decoration"), reload_emits_event=True),
    )


# --- loaded state ---------------------------------------------------------------------------


def test_the_loaded_plugins_are_read_from_the_compositor_and_follow_it(tmp_path: Path) -> None:
    """A reload or an unload changes the answer: it is asked again, never cached."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        answers: list[tuple[str, ...] | None] = []

        session.fetch_loaded_plugins(answers.append)
        await runner.settle()
        fake.conversation["j/plugin list"] = "[]"
        session.fetch_loaded_plugins(answers.append)
        await runner.settle()

        assert answers == [("hyprbars",), ()]

    conversation = section_conversation("general")
    conversation["j/plugin list"] = LOADED_REPLY
    run_with_fake(scenario, FakeHyprland(conversation))


def test_with_nobody_to_ask_the_loaded_state_is_unknown(tmp_path: Path) -> None:
    session, _applier = entity_session(tmp_path)
    answers: list[tuple[str, ...] | None] = []

    session.fetch_loaded_plugins(answers.append)

    assert answers == [None]
