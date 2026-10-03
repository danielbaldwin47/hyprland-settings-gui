"""A kept import is the user's restore boundary (#259, ADR-0016 Last known good).

The wizard writes the imported Modules with its own Writer and reads no Options back, so
nothing about Keep can say whether the imported bytes are good. The Session's next
read-back can, and these tests drive that through a whole `Session`: the fresh import a
first start reads, the menu import a running session adopts, and the read-back that could
not confirm it. Every assertion is what Restore last good would put back.
"""

from __future__ import annotations

from pathlib import Path

from _fake_hyprland import NO_SUCH_OPTION, FakeHyprland, run_with_fake
from _support import Runner, section_conversation, session_for

from hyprtweaker.engine.migration import sentinel as sentinels
from hyprtweaker.engine.model import ConfigModel
from hyprtweaker.engine.state import Manifest, kept_import
from hyprtweaker.engine.writer import Writer
from hyprtweaker.session import Session

BORDER_SIZE = "general:border_size"
GENERAL_MODULE = "options/general.lua"


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


def border(value: int) -> str:
    return f'{{"option": "{BORDER_SIZE}", "int": {value}, "set": true }}'


async def settle(session: Session, runner: Runner) -> None:
    await session.drain()
    await runner.settle()
    await session.drain()
    await runner.settle()


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


def general(root: Path) -> Path:
    return root / "hypr" / "hyprtweaker" / GENERAL_MODULE


def import_and_keep(session_or_root: Session | Path, border_size: int) -> bytes:
    """What the wizard leaves: its own Writer's tree, then Keep's record (`flow.keep`)."""
    from _support import SAMPLE_APP_VERSION, sample_schema

    from hyprtweaker.engine.paths import ConfigPaths

    if isinstance(session_or_root, Session):
        paths, schema = session_or_root.paths, session_or_root.schema
    else:
        paths, schema = ConfigPaths.rooted_at(session_or_root), sample_schema()
    imported = ConfigModel(schema)
    imported.set(BORDER_SIZE, border_size)
    Writer(paths, app_version=SAMPLE_APP_VERSION).write(imported)
    kept_import.write(paths, Manifest.load(paths.manifest, app_version="x", schema_version="y"))
    return paths.file_for(GENERAL_MODULE).read_bytes()


def test_a_kept_fresh_import_is_restored_to_its_imported_bytes(tmp_path: Path) -> None:
    """AC1: the first start reads the import back clean, so it is the restore point."""

    async def scenario(fake: FakeHyprland) -> None:
        imported = import_and_keep(tmp_path, 4)
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        general(tmp_path).write_bytes(b"-- hand edited\n")
        assert session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)

        assert general(tmp_path).read_bytes() == imported
        assert session.model.get(BORDER_SIZE) == 4
        assert kept_import.read(session.paths) is None, "the record is consumed once"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 4}), reload_emits_event=True)
    )


def test_a_kept_menu_import_restores_imported_bytes_not_the_config_before_it(
    tmp_path: Path,
) -> None:
    """AC2: before #259 Restore offered the session's own pre-import write."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)

        imported = import_and_keep(session, 5)
        fake.conversation["j/getoption " + BORDER_SIZE] = border(5)
        session.adopt_import()
        await settle(session, runner)

        general(tmp_path).write_bytes(b"-- hand edited\n")
        good = session.last_good_for(GENERAL_MODULE)
        assert good is not None and good.data == imported
        assert session.restore_last_good(GENERAL_MODULE)
        await settle(session, runner)
        assert general(tmp_path).read_bytes() == imported
        assert session.model.get(BORDER_SIZE) == 5

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_an_import_read_back_unconfirmed_offers_no_restore_point_and_says_why(
    tmp_path: Path,
) -> None:
    """AC4: neither the unverified import nor the bytes it replaced is a restore point."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        history = len(session.journal.entries())

        import_and_keep(session, 5)
        fake.conversation["j/getoption " + BORDER_SIZE] = NO_SUCH_OPTION
        session.adopt_import()
        await settle(session, runner)

        general(tmp_path).write_bytes(b"-- hand edited\n")
        assert session.last_good_for(GENERAL_MODULE) is None
        assert not session.restorable(GENERAL_MODULE)
        assert session.unverified_since_import(GENERAL_MODULE)
        assert not session.restore_last_good(GENERAL_MODULE)
        assert general(tmp_path).read_bytes() == b"-- hand edited\n"
        assert len(session.journal.entries()) == history + 1, "older history is kept"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )


def test_an_unanswered_switch_is_no_boundary_until_it_is_kept(tmp_path: Path) -> None:
    """AC3: a record beside a migration sentinel is a Keep that never finished; the
    relaunch's offer answers it first, and a Roll back drops it (`flow.roll_back`)."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 3)
        await settle(session, runner)
        before = general(tmp_path).read_bytes()

        import_and_keep(session, 5)
        sentinels.write(
            session.paths,
            kind="foreign-lua",
            source=None,
            backup=None,
            restore=None,
        )
        fake.conversation["j/getoption " + BORDER_SIZE] = border(5)
        session.adopt_import()
        await settle(session, runner)

        good = session.last_good_for(GENERAL_MODULE)
        assert good is not None and good.data == before
        assert kept_import.read(session.paths) is not None, "left for the answer"

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 3}), reload_emits_event=True)
    )
