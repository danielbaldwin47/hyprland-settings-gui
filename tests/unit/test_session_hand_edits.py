"""An edit into a Module somebody edited by hand: refused out loud, never claimed as saved.

The Writer leaves a hand-edited Module alone (ADR-0005), so an edit whose Option or Entity
lives in it never reaches disk. Hand-test defect 13 of effort #148: the app still said
"Corner rounding changed" and offered Undo. These drive a whole `Session` the way a Row
does and assert what the user is left with: the file, the model, the undo stack, the Banner.
"""

from __future__ import annotations

from pathlib import Path

from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, section_conversation, session_for

from hyprtweaker.engine.apply import Step
from hyprtweaker.engine.model import Bind, DispatcherCall
from hyprtweaker.session import Session

ROUNDING = "decoration:rounding"
BORDER_SIZE = "general:border_size"
DECORATION = "options/decoration.lua"


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    await session.drain()
    await runner.settle()
    await session.drain()


def module(root: Path, name: str) -> Path:
    return root / "hypr" / "hyprtweaker" / name


def test_an_edit_into_a_hand_edited_module_is_not_claimed_as_saved(tmp_path: Path) -> None:
    recorded: list[Step] = []
    held: list[tuple[str, ...]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_recorded = recorded.append
        session.on_held_back = lambda titles, _files: held.append(titles)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        recorded.clear()

        path = module(tmp_path, DECORATION)
        edited = path.read_text() + "-- edited by hand\n"
        path.write_text(edited)
        session.set_option(ROUNDING, 11)
        await settle(session, runner)

        assert path.read_text() == edited
        assert session.model.get(ROUNDING) == 18
        assert recorded == []
        assert session.last_gesture is not None
        assert session.last_gesture.names == (ROUNDING,)
        assert session.last_gesture.edits[0].after == 18
        assert held == [("Corner rounding",)]
        assert session.health.title == (
            "decoration.lua was edited outside this app, so changes to it are not saved."
        )
        assert session.health.button == "Details"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_replacing_the_edited_file_saves_the_held_back_change(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        path = module(tmp_path, DECORATION)
        path.write_text(path.read_text() + "-- edited by hand\n")
        session.set_option(ROUNDING, 11)
        await settle(session, runner)

        fake.conversation.update(conversation(**{ROUNDING: 11}))
        assert session.held_back_titles(DECORATION) == ("Corner rounding",)
        changed: list[float] = []
        session.on_state_changed = lambda: changed.append(session.model.get(ROUNDING))
        session.replace_edited_file(DECORATION)
        assert changed == [11], "the Row was not told the model now holds the change"
        await settle(session, runner)

        text = path.read_text()
        assert "rounding = 11," in text
        assert "-- edited by hand" not in text
        copies = list((tmp_path / "state" / "edited-copies").rglob("*.lua"))
        assert [c.read_text().endswith("-- edited by hand\n") for c in copies] == [True]
        assert session.model.get(ROUNDING) == 11
        assert session.health.title == ""
        assert session.last_gesture is not None
        assert session.last_gesture.edits[0].after == 11

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_an_edit_into_another_module_still_saves(tmp_path: Path) -> None:
    """Only the edited file is off limits: the rest of the app keeps working."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        path = module(tmp_path, DECORATION)
        path.write_text(path.read_text() + "-- edited by hand\n")

        fake.conversation.update(conversation(**{ROUNDING: 18, BORDER_SIZE: 4}))
        session.set_option(BORDER_SIZE, 4)
        await settle(session, runner)

        assert "border_size = 4," in module(tmp_path, "options/general.lua").read_text()
        assert session.last_gesture is not None
        assert session.last_gesture.names == (BORDER_SIZE,)
        assert session.health.title == ""

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_a_bind_added_into_a_hand_edited_binds_module_is_held_back(tmp_path: Path) -> None:
    held: list[tuple[str, ...]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_held_back = lambda titles, _files: held.append(titles)
        first = Bind(
            keys="SUPER + A", dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",))
        )
        session.add_bind(first)
        await settle(session, runner)
        path = module(tmp_path, "binds.lua")
        edited = path.read_text() + "-- edited by hand\n"
        path.write_text(edited)

        second = Bind(
            keys="SUPER + B", dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",))
        )
        session.add_bind(second)
        await settle(session, runner)

        assert path.read_text() == edited
        assert [bind.keys for bind in session.model.entities.binds] == ["SUPER + A"]
        assert held == [("Keybind added",)]
        assert session.health.title == (
            "binds.lua was edited outside this app, so changes to it are not saved."
        )

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
