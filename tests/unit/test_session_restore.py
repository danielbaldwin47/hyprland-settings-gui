"""Restore last good keeps the hand edit it replaces, and the model in step with the bytes
it lays down (#266).

Driven through a whole `Session` with a scripted compositor, like `test_session_recovery`:
every assertion is on the file, the model, the Banner or what `restore_last_good` reports,
because those are what a user is left holding when a recovery goes half right.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _fake_hyprland import NO_BINDS, FakeHyprland, run_with_fake
from _support import Runner, drain_events, section_conversation, session_for

from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
ROUNDING = "decoration:rounding"
GENERAL_MODULE = "options/general.lua"
DECORATION_MODULE = "options/decoration.lua"


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    """Wait out the queue, then whatever the finished transactions spawned, then the queue."""
    await session.drain()
    await runner.settle()
    await session.drain()
    await runner.settle()


def module(root: Path, name: str) -> Path:
    return root / "hypr" / "hyprtweaker" / name


APP_ERROR = (
    '[\n\t"/home/user/.config/hypr/hyprtweaker/options/general.lua:4: '
    "unknown config key 'general.nope'\"\n]\n"
)


def break_once(fake: FakeHyprland, errors: str, binds: str) -> None:
    """Report a broken config for exactly one `configerrors` read, then be healthy again."""
    clean_errors = fake.conversation["j/configerrors"]
    clean_binds = fake.conversation["j/binds"]
    armed = [True]

    def hook(request: str, _seen: int) -> None:
        if request != "j/configerrors":
            return
        fake.conversation["j/configerrors"] = errors if armed[0] else clean_errors
        fake.conversation["j/binds"] = binds if armed[0] else clean_binds
        armed[0] = False

    fake.on_request = hook


async def foreign_reload(fake: FakeHyprland, session: Session, runner: Runner) -> None:
    await fake.emit("configreloaded")
    await drain_events(runner)
    await settle(session, runner)


def block_copies(root: Path) -> None:
    """A file where the edited copies' directory goes: no copy can be kept."""
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "edited-copies").write_text("in the way")


# --- the edited copy comes first ------------------------------------------------------------


def test_a_restore_whose_edited_copy_cannot_be_kept_writes_nothing_and_says_so(
    tmp_path: Path,
) -> None:
    """The dialog promises a copy of the file as it is now. Without one, the hand edit
    would exist only inside the Journal, which the user cannot open: nothing is restored."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        edited = b"-- hand edited\n"
        module(tmp_path, GENERAL_MODULE).write_bytes(edited)
        entries = len(session.journal.entries())
        block_copies(tmp_path)

        start = session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert not start
        assert start.uncopied == (GENERAL_MODULE,)
        assert module(tmp_path, GENERAL_MODULE).read_bytes() == edited
        assert len(session.journal.entries()) == entries

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_the_emergency_restore_goes_on_without_a_copy_and_the_banner_says_so(
    tmp_path: Path,
) -> None:
    """ADR-0016 §Zero-binds: a stranded user is never refused. The overwritten edit is
    still in the Journal, and the Banner says no copy of it could be kept as a file."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        good = module(tmp_path, GENERAL_MODULE).read_bytes()
        module(tmp_path, GENERAL_MODULE).write_bytes(b"-- hand edited, and broken\n")
        block_copies(tmp_path)
        break_once(fake, APP_ERROR, NO_BINDS)

        await foreign_reload(fake, session, runner)

        assert module(tmp_path, GENERAL_MODULE).read_bytes() == good
        assert session.health.title == (
            "Restored general.lua so your keybinds would load again. Your edited version "
            "is saved in this app's history, but no copy of it could be kept as a file."
        )

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_a_restore_takes_the_file_off_the_banner_and_names_its_copy(tmp_path: Path) -> None:
    """The file is the app's own again, so the Banner's "edited outside this app" would be
    a falsehood; and the copy the dialog promised is where the answer says it is."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        edited = module(tmp_path, GENERAL_MODULE).read_bytes() + b"-- edited by hand\n"
        module(tmp_path, GENERAL_MODULE).write_bytes(edited)
        session.set_option(BORDER_SIZE, 5)
        await settle(session, runner)
        assert session.health.edited_files == (GENERAL_MODULE,), "the precondition"

        start = session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert start
        assert start.copies[GENERAL_MODULE].read_bytes() == edited
        assert start.copies[GENERAL_MODULE].name == "general.lua"
        assert session.health.edited_files == ()
        assert session.health.title == ""

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def getoption(name: str, value: int) -> dict[str, str]:
    return {f"j/getoption {name}": f'{{"option": "{name}", "int": {value}, "set": true }}'}


def edit_by_hand(root: Path, name: str, old: str, new: str) -> bytes:
    """Change one line of an app Module the way an editor would, and return its bytes."""
    path = module(root, name)
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
    return path.read_bytes()


def test_a_restore_that_fails_part_way_leaves_the_landed_module_in_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two Modules, and the write of the second fails. The first is back on disk as the
    app's own, so the next edit in it renders the model: a model still holding the hand
    edit's value would write that value straight over the restored bytes."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        session.set_option(ROUNDING, 18)
        await settle(session, runner)
        good = module(tmp_path, GENERAL_MODULE).read_bytes()
        edit_by_hand(tmp_path, GENERAL_MODULE, "border_size = 3,", "border_size = 7,")
        edited = edit_by_hand(tmp_path, DECORATION_MODULE, "rounding = 18,", "rounding = 9,")
        fake.conversation.update(getoption(BORDER_SIZE, 7) | getoption(ROUNDING, 9))
        await foreign_reload(fake, session, runner)
        assert (session.model.get(BORDER_SIZE), session.model.get(ROUNDING)) == (7, 9)

        real = Writer.restore
        calls: list[str] = []

        def second_fails(self: Writer, model: object, name: str, *args: object, **kw: object):
            calls.append(name)
            if len(calls) == 2:
                raise OSError(28, "No space left on device")
            return real(self, model, name, *args, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(Writer, "restore", second_fails)
        fake.conversation.update(getoption(BORDER_SIZE, 3))

        assert session.restore_last_good(GENERAL_MODULE, DECORATION_MODULE)
        await settle(session, runner)
        monkeypatch.setattr(Writer, "restore", real)

        assert calls == [GENERAL_MODULE, DECORATION_MODULE]
        assert module(tmp_path, GENERAL_MODULE).read_bytes() == good
        assert module(tmp_path, DECORATION_MODULE).read_bytes() == edited
        assert (session.model.get(BORDER_SIZE), session.model.get(ROUNDING)) == (3, 9)

        session.set_option("general:gaps_workspaces", 7)
        await settle(session, runner)
        text = module(tmp_path, GENERAL_MODULE).read_text()
        assert "border_size = 3," in text and "border_size = 7" not in text
        assert "gaps_workspaces = 7," in text

    run_with_fake(
        scenario,
        FakeHyprland(conversation(**{BORDER_SIZE: 3, ROUNDING: 18}), reload_emits_event=True),
    )


def test_a_file_the_app_wrote_is_restored_with_no_copy_to_keep(tmp_path: Path) -> None:
    """Only a hand edit needs a copy: a Module holding the app's own unconfirmed write is
    restored even where no copy could be kept, and none is claimed."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        good = module(tmp_path, GENERAL_MODULE).read_bytes()
        # Written, but the compositor still answers 3: not confirmed, so 3 stays last good.
        session.set_option(BORDER_SIZE, 5)
        await settle(session, runner)
        assert "border_size = 5," in module(tmp_path, GENERAL_MODULE).read_text()
        assert not session.edited_outside(GENERAL_MODULE), "the precondition"
        block_copies(tmp_path)

        start = session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert start
        assert (start.copies, start.uncopied) == ({}, ())
        assert module(tmp_path, GENERAL_MODULE).read_bytes() == good
        assert session.model.get(BORDER_SIZE) == 3

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )
