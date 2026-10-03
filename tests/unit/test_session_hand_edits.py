"""A change into a Module somebody edited by hand: refused at once, out loud, and named.

The app never writes over a hand-edited Module (ADR-0005). Hand-test defect 13 of effort
#148: the app still said "Corner rounding changed" and offered Undo. The fix held such
edits back to replay on "Replace file", and the review of that fix (R3-R6) found it lost,
misnamed and missed them; now every change path refuses before the model moves, and
nothing is held. These drive a whole `Session` the way a Row does and assert what the user
is left with: the file, the model, the undo stack, the Banner.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, section_conversation, session_for

from hyprtweaker.engine.apply import Step
from hyprtweaker.engine.model import UNSET, Bind, DispatcherCall
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


def hand_edit(root: Path, name: str) -> str:
    path = module(root, name)
    edited = path.read_text() + "-- edited by hand\n"
    path.write_text(edited)
    return edited


def bind(keys: str) -> Bind:
    return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))


def test_an_edit_into_a_hand_edited_module_is_refused_at_once(tmp_path: Path) -> None:
    """Hand-test defect 13, and the #148 review's R3-R6 premise: the change is refused before
    the model moves, so there is nothing to take back later or to hold."""
    recorded: list[Step] = []
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_recorded = recorded.append
        session.on_refused = lambda what, file: refused.append((what, file))
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        recorded.clear()
        edited = hand_edit(tmp_path, DECORATION)
        writes = fake.requests.count("reload")

        session.set_option(ROUNDING, 11)

        assert session.model.get(ROUNDING) == 18
        assert refused == [("Corner rounding", DECORATION)]
        await settle(session, runner)
        assert fake.requests.count("reload") == writes
        assert module(tmp_path, DECORATION).read_text() == edited
        assert recorded == []
        assert session.last_gesture is not None
        assert session.last_gesture.edits[0].after == 18
        assert session.health.title == (
            "decoration.lua was edited outside this app, so changes to it are not saved."
        )
        assert session.health.button == "Details"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_replace_writes_the_apps_version_after_a_copy_and_the_change_is_made_again(
    tmp_path: Path,
) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        hand_edit(tmp_path, DECORATION)
        session.set_option(ROUNDING, 11)

        assert session.replace_edited_file(DECORATION)
        await settle(session, runner)

        path = module(tmp_path, DECORATION)
        assert "rounding = 18," in path.read_text()
        assert "-- edited by hand" not in path.read_text()
        copies = list((tmp_path / "state" / "edited-copies").rglob("*.lua"))
        assert [c.read_text().endswith("-- edited by hand\n") for c in copies] == [True]
        assert session.health.title == ""

        fake.conversation.update(conversation(**{ROUNDING: 11}))
        session.set_option(ROUNDING, 11)
        await settle(session, runner)
        assert "rounding = 11," in path.read_text()
        assert session.last_gesture is not None
        assert session.last_gesture.edits[0].after == 11

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_replace_refuses_when_no_copy_can_be_kept(tmp_path: Path) -> None:
    """The dialog promises a copy: without one, nothing is replaced."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        edited = hand_edit(tmp_path, DECORATION)
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state" / "edited-copies").write_text("in the way")

        assert not session.replace_edited_file(DECORATION)
        await settle(session, runner)

        assert module(tmp_path, DECORATION).read_text() == edited

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
        hand_edit(tmp_path, DECORATION)

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


def test_every_bind_added_into_a_hand_edited_binds_module_is_refused_and_said(
    tmp_path: Path,
) -> None:
    """R4 of the #148 review: of two held binds, Replace saved the last. Nothing is held
    now: each is refused at once, said, and saved once made again after the Replace."""
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.add_bind(bind("SUPER + A"))
        await settle(session, runner)
        edited = hand_edit(tmp_path, "binds.lua")

        assert not session.add_bind(bind("SUPER + B"))
        assert not session.add_bind(bind("SUPER + C"))
        await settle(session, runner)

        assert module(tmp_path, "binds.lua").read_text() == edited
        assert [b.keys for b in session.model.entities.binds] == ["SUPER + A"]
        assert refused == [("Keybind added", "binds.lua")] * 2
        assert session.health.title == (
            "binds.lua was edited outside this app, so changes to it are not saved."
        )

        assert session.replace_edited_file("binds.lua")
        await settle(session, runner)
        assert session.add_bind(bind("SUPER + B"))
        assert session.add_bind(bind("SUPER + C"))
        await settle(session, runner)
        text = module(tmp_path, "binds.lua").read_text()
        assert all(keys in text for keys in ("SUPER + A", "SUPER + B", "SUPER + C"))

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_each_refusal_names_its_own_file_and_the_banner_keeps_both(tmp_path: Path) -> None:
    """R5 of the #148 review: with two edited files, a Border size edit was said to be
    stopped by decoration.lua, and general.lua's choices could not be reached."""
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.set_option(ROUNDING, 18)
        session.set_option(BORDER_SIZE, 2)
        await settle(session, runner)
        hand_edit(tmp_path, DECORATION)
        hand_edit(tmp_path, "options/general.lua")

        session.set_option(ROUNDING, 11)
        session.set_option(BORDER_SIZE, 4)
        await settle(session, runner)

        assert refused == [
            ("Corner rounding", DECORATION),
            ("Border size", "options/general.lua"),
        ]
        assert session.health.edited_files == (DECORATION, "options/general.lua")
        assert session.health.title == (
            "2 files were edited outside this app, so changes to them are not saved."
        )
        session.keep_edited_file(DECORATION)
        assert session.health.edited_files == ("options/general.lua",)

    run_with_fake(
        scenario,
        FakeHyprland(conversation(**{ROUNDING: 18, BORDER_SIZE: 2}), reload_emits_event=True),
    )


def test_a_profile_activated_into_a_hand_edited_monitors_module_is_refused(
    tmp_path: Path,
) -> None:
    """R6 of the #148 review: the activation was reported as done -- the model and the
    pointer moved and the countdown ran -- while the file stayed as the user edited it."""
    refused: list[tuple[str, str]] = []
    connected = (
        {"name": "eDP-1", "description": "BOE 0x0791"},
        {"name": "DP-3", "description": "Dell U2720Q"},
    )

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60", "position": "0x0"})
        await settle(session, runner)
        slug = session.save_monitor_profile("Docked", connected)
        session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@48"})
        await settle(session, runner)
        active = session.active_monitor_profile()
        rules = [(r.output, r.fields) for r in session.monitor_rules]
        edited = hand_edit(tmp_path, "monitors.lua")

        assert not session.activate_monitor_profile(slug)
        await settle(session, runner)

        assert refused == [("Display profile", "monitors.lua")]
        assert [(r.output, r.fields) for r in session.monitor_rules] == rules
        assert session.active_monitor_profile() == active
        assert module(tmp_path, "monitors.lua").read_text() == edited

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_an_undo_into_a_hand_edited_module_is_refused_and_stays_undoable(
    tmp_path: Path,
) -> None:
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        step = session.last_gesture
        edited = hand_edit(tmp_path, DECORATION)

        assert not session.undo()
        await settle(session, runner)

        assert refused == [("Undo", DECORATION)]
        assert session.model.get(ROUNDING) == 18
        assert session.last_gesture is step
        assert module(tmp_path, DECORATION).read_text() == edited

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


def test_a_kept_import_into_a_live_session_is_what_the_next_edit_builds_on(
    tmp_path: Path,
) -> None:
    """F5 of the #148 review: the wizard writes the App dir with its own Writer, so every
    Module matches the Manifest and the hash-gated re-read skipped them all. The next edit
    then wrote the session's old binds over the imported ones."""
    from hyprtweaker.engine.model import ConfigModel
    from hyprtweaker.engine.writer import Writer

    def bind(keys: str) -> Bind:
        return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.add_bind(bind("SUPER + A"))
        await settle(session, runner)

        imported = ConfigModel(session.schema)
        imported.entities.binds.append(bind("SUPER + Z"))
        imported.mark_entities_loaded()
        Writer(session.paths, app_version=session.app_version).write(imported)

        session.adopt_import()
        await settle(session, runner)
        assert [b.keys for b in session.model.entities.binds] == ["SUPER + Z"]
        assert session.last_gesture is None, "an undo step from before the import survived"

        session.add_bind(bind("SUPER + B"))
        await settle(session, runner)
        text = module(tmp_path, "binds.lua").read_text()
        assert "SUPER + Z" in text and "SUPER + B" in text and "SUPER + A" not in text

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- #273: a hand edit between the gate and the write ------------------------------------
#
# The tests above edit before the gesture, so `_edited_module` refuses it. These edit after
# the gesture returns -- the gate passed, the commit is only queued -- and before the queued
# transaction writes, so the Writer is what finds the file edited and leaves it alone
# (ADR-0005). Each expects what the gate-time refusal leaves: the file as edited, the model
# and the pointer as the file has them, the undo still on the stack, the file named.

DOCKED = (
    {"name": "eDP-1", "description": "BOE 0x0791"},
    {"name": "DP-3", "description": "Dell U2720Q"},
)


def refused_state(session: Session, refused: list[tuple[str, str]]) -> dict[str, object]:
    """What the user is left with after the refusal, beside the file's bytes."""
    active = session.active_monitor_profile()
    return {
        "monitor_rules": [(r.output, dict(r.fields)) for r in session.monitor_rules],
        "rounding": session.model.get(ROUNDING),
        "binds": [b.keys for b in session.model.entities.binds],
        "active_profile": None if active is None else active[0],
        "can_undo": session.can_undo,
        "refused_files": [file for _, file in refused],
        "banner_files": session.health.edited_files,
    }


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#273: the pointer moves at enqueue; a skipped activation write takes nothing back",
)
def test_a_hand_edit_after_the_gate_leaves_a_profile_activation_refused_whole(
    tmp_path: Path,
) -> None:
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60", "position": "0x0"})
        await settle(session, runner)
        slug = session.save_monitor_profile("Docked", DOCKED)
        session.detach_monitor_profile()
        session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@48"})
        await settle(session, runner)

        assert session.activate_monitor_profile(slug)  # the gate passed
        edited = hand_edit(tmp_path, "monitors.lua")  # before the queued write runs
        await settle(session, runner)

        assert module(tmp_path, "monitors.lua").read_text() == edited
        assert refused_state(session, refused) == {
            "monitor_rules": [("eDP-1", {"mode": "1920x1080@48", "position": "0x0"})],
            "rounding": UNSET,
            "binds": [],
            "active_profile": None,
            "can_undo": False,  # the activation forgets the display steps at the gate
            "refused_files": ["monitors.lua"],
            "banner_files": ("monitors.lua",),
        }

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#273: undo pops before the write and a skipped Option undo takes nothing back",
)
def test_a_hand_edit_after_the_gate_leaves_an_option_undo_refused_and_undoable(
    tmp_path: Path,
) -> None:
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        fake.conversation.update(conversation(**{ROUNDING: 11}))
        session.set_option(ROUNDING, 11)
        await settle(session, runner)
        step = session.last_gesture

        assert session.undo()  # the gate passed
        edited = hand_edit(tmp_path, DECORATION)  # before the queued write runs
        await settle(session, runner)

        assert module(tmp_path, DECORATION).read_text() == edited
        assert (refused_state(session, refused), session.last_gesture is step) == (
            {
                "monitor_rules": [],
                "rounding": 11,
                "binds": [],
                "active_profile": None,
                "can_undo": True,
                "refused_files": [DECORATION],
                "banner_files": (DECORATION,),
            },
            True,
        )

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 18}), reload_emits_event=True)
    )


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#273: undo pops before the write and a skipped Entity undo takes nothing back",
)
def test_a_hand_edit_after_the_gate_leaves_an_entity_undo_refused_and_undoable(
    tmp_path: Path,
) -> None:
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.add_bind(bind("SUPER + A"))
        await settle(session, runner)
        session.add_bind(bind("SUPER + B"))
        await settle(session, runner)
        step = session.last_gesture

        assert session.undo()  # the gate passed
        edited = hand_edit(tmp_path, "binds.lua")  # before the queued write runs
        await settle(session, runner)

        assert module(tmp_path, "binds.lua").read_text() == edited
        assert (refused_state(session, refused), session.last_gesture is step) == (
            {
                "monitor_rules": [],
                "rounding": UNSET,
                "binds": ["SUPER + A", "SUPER + B"],
                "active_profile": None,
                "can_undo": True,
                "refused_files": ["binds.lua"],
                "banner_files": ("binds.lua",),
            },
            True,
        )

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="#273: a hand-edited Module the write would remove is kept but not named skipped",
)
def test_a_hand_edit_after_the_gate_leaves_an_undo_that_empties_its_module_refused(
    tmp_path: Path,
) -> None:
    """The Writer keeps a hand-edited Module it would prune (`_prune`'s `off_limits`) but
    leaves it out of `skipped`, so nothing after the write knows the change missed disk."""
    refused: list[tuple[str, str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_refused = lambda what, file: refused.append((what, file))
        session.add_bind(bind("SUPER + A"))
        await settle(session, runner)
        step = session.last_gesture

        assert session.undo()  # the gate passed; the undo leaves binds.lua nothing to hold
        edited = hand_edit(tmp_path, "binds.lua")  # before the queued write runs
        await settle(session, runner)

        assert module(tmp_path, "binds.lua").read_text() == edited
        assert (refused_state(session, refused), session.last_gesture is step) == (
            {
                "monitor_rules": [],
                "rounding": UNSET,
                "binds": ["SUPER + A"],
                "active_profile": None,
                "can_undo": True,
                "refused_files": ["binds.lua"],
                "banner_files": ("binds.lua",),
            },
            True,
        )

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
