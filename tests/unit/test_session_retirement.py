"""ADR-0012 §Retirement through the Session, from one app start to the next.

Each test runs the app the way a user does: a first session writes an Option, the next
starts against a Hyprland (or a Schema) that no longer has it. What is asserted is what the
user's disk and screen end up holding -- the Module bytes against a golden, the value in
the Manifest, the notice -- never which retirement function ran.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from _fake_hyprland import NO_SUCH_OPTION, FakeHyprland, option_reply, run_with_fake
from _golden import assert_matches_golden
from _support import (
    GOLDEN_DIR,
    SAMPLE_APP_VERSION,
    Runner,
    sample_schema,
    schema_renaming,
    section_conversation,
    session_for,
    synthetic_schema_dir,
)

from hyprtweaker.engine.importer.lua import sandbox
from hyprtweaker.engine.ipc import LiveHyprland
from hyprtweaker.engine.model import UNSET, CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import Schema
from hyprtweaker.engine.schema.resolve import SCHEMA_DIR_ENV
from hyprtweaker.engine.state import Manifest, RetiredValue, RetireReason
from hyprtweaker.engine.state.retirement import RenamedNotice, RetiredNotice, UnkeptNotice
from hyprtweaker.session import Notice, Session

SCHEMA = sample_schema()
GAPS_IN = "general:gaps_in"
RESIZE = "general:resize_on_border"
GOLDENS = GOLDEN_DIR / "writer"


def live(version: str, *, without: tuple[str, ...] = ()) -> LiveHyprland:
    """A running Hyprland at `version` describing every sample Option but `without`."""
    return LiveHyprland(
        version, tuple({"name": o.name} for o in SCHEMA if o.name not in without)
    )


def conversation() -> dict[str, str]:
    """The compositor the first session wrote to: gaps 12 and resize-on-border on."""
    return section_conversation("general", **{GAPS_IN: CssGaps(12, 12, 12, 12), RESIZE: True})


def module(root: Path) -> str:
    return (ConfigPaths.rooted_at(root).app_dir / "options" / "general.lua").read_text()


def manifest(root: Path) -> Manifest:
    return Manifest.load(
        ConfigPaths.rooted_at(root).manifest, app_version="x", schema_version="x"
    )


async def first_start(fake: FakeHyprland, root: Path) -> None:
    """The release before: the user turns resize-on-border on and sets inner gaps."""
    runner = Runner()
    session = session_for(fake, root, runner, live_hyprland=live("0.56.2"))
    session.start()
    await runner.settle()
    session.set_option(GAPS_IN, 12)
    session.set_option(RESIZE, True)
    await session.aclose()


async def start(
    fake: FakeHyprland,
    root: Path,
    snapshot: LiveHyprland | None,
    *,
    schema: Schema = SCHEMA,
    seen: Callable[[Session, Notice], None] | None = None,
) -> tuple[Session, list[Notice]]:
    """One more app start: run it to quiescence and collect the notices it raised.

    `seen` stands in for the user dismissing each notice, which is when it is recorded."""
    runner = Runner()
    session = Session(
        spawn=runner.spawn,
        schema=schema,
        paths=ConfigPaths.rooted_at(root),
        app_version=SAMPLE_APP_VERSION,
        connect=lambda: fake.instance,
        read_live=lambda: snapshot,
    )
    notices: list[Notice] = []

    def on_notice(notice: Notice) -> None:
        notices.append(notice)
        if seen is not None:
            seen(session, notice)

    session.on_notice = on_notice
    session.start()
    await runner.settle()
    await session.aclose()
    return session, notices


def dismissed(session: Session, notice: Notice) -> None:
    session.notice_seen(notice)


class TestGolden:
    """#77's end-to-end goldens: retired, kept, and emitted again when the Option returns."""

    def test_a_retired_option_leaves_the_module_and_keeps_its_value(
        self, tmp_path: Path
    ) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            assert_matches_golden(
                module(tmp_path), GOLDENS / "retirement-set.lua", "the module as set"
            )

            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            session, _ = await start(fake, tmp_path, live("0.57.0", without=(RESIZE,)))

            assert_matches_golden(
                module(tmp_path), GOLDENS / "retirement-retired.lua", "the retired module"
            )
            assert manifest(tmp_path).retired == {RESIZE: RetiredValue("0.57.0", True)}
            assert session.model.get(RESIZE) is UNSET
            assert session.model.get(GAPS_IN) == CssGaps(12, 12, 12, 12)

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))

    def test_it_is_emitted_again_when_the_option_returns(self, tmp_path: Path) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            await start(fake, tmp_path, live("0.57.0", without=(RESIZE,)))
            assert manifest(tmp_path).retired == {RESIZE: RetiredValue("0.57.0", True)}

            fake.conversation.update(conversation())
            session, _ = await start(fake, tmp_path, live("0.57.1"))

            assert_matches_golden(
                module(tmp_path), GOLDENS / "retirement-set.lua", "the restored module"
            )
            assert manifest(tmp_path).retired == {}
            assert session.model.get(RESIZE) is True

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))

    def test_a_renamed_option_is_emitted_under_its_new_name(self, tmp_path: Path) -> None:
        renamed = schema_renaming(RESIZE, "general:resize_on_edge", version="0.57.0")

        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation["j/getoption general:resize_on_edge"] = option_reply(
                renamed["general:resize_on_edge"], True
            )
            session, notices = await start(fake, tmp_path, None, schema=renamed)

            assert_matches_golden(
                module(tmp_path), GOLDENS / "retirement-renamed.lua", "the renamed module"
            )
            assert manifest(tmp_path).retired == {}
            assert session.model.get("general:resize_on_edge") is True
            assert notices == [RenamedNotice(((RESIZE, "general:resize_on_edge"),))]

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


class TestNotice:
    """AC 1: the notice shows once per release, and again for the next one."""

    def test_once_per_release_and_again_for_the_next(self, tmp_path: Path) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            gone = live("0.57.0", without=(RESIZE,))

            _, first = await start(fake, tmp_path, gone, seen=dismissed)
            _, second = await start(fake, tmp_path, gone, seen=dismissed)

            fake.conversation[f"j/getoption {GAPS_IN}"] = NO_SUCH_OPTION
            _, third = await start(
                fake, tmp_path, live("0.58.0", without=(RESIZE, GAPS_IN)), seen=dismissed
            )

            assert first == [RetiredNotice("0.57.0", (RESIZE,))]
            assert second == []
            assert third == [RetiredNotice("0.58.0", (GAPS_IN,))]
            assert manifest(tmp_path).retired_notices == ("0.57.0", "0.58.0")

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))

    def test_a_value_the_app_cannot_read_back_is_announced_not_dropped_silently(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#150 review finding 4: with no Lua to read its Module, the value cannot be kept,
        and the user hears that this start rather than finding it gone later."""

        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            monkeypatch.setattr(sandbox, "lua_binary", lambda: None)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION

            _, notices = await start(fake, tmp_path, live("0.57.0", without=(RESIZE,)))

            assert notices == [UnkeptNotice("0.57.0", (RESIZE,))]
            assert manifest(tmp_path).retired == {}
            assert "resize_on_border" not in module(tmp_path)

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))

    def test_a_notice_never_dismissed_shows_again_next_start(self, tmp_path: Path) -> None:
        """Recorded on dismissal, not on display: closing the app first loses nothing."""

        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            gone = live("0.57.0", without=(RESIZE,))

            _, first = await start(fake, tmp_path, gone)
            _, second = await start(fake, tmp_path, gone)

            assert first == second == [RetiredNotice("0.57.0", (RESIZE,))]
            assert manifest(tmp_path).retired_notices == ()

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


NEW_SIZE = "general:new_size"
NEWER = "0.59.0"
"""Newer than every schema `shipped_schemas` ships, so its extra Option is supplemented."""

NEW_SIZE_RECORD = {
    "name": NEW_SIZE,
    "description": "a size this Hyprland added",
    "default": 3,
    "current": 3,
    "min": 0,
    "max": 20,
    "map": None,
}


def newer_described() -> LiveHyprland:
    """Hyprland 0.59.0: every sample Option, plus one no shipped schema has."""
    return LiveHyprland(NEWER, (*({"name": o.name} for o in SCHEMA), NEW_SIZE_RECORD))


def newer_conversation() -> dict[str, str]:
    """That compositor's sockets, with the user's `new_size = 7` set and readable."""
    described = newer_described()
    return {
        **conversation(),
        "j/version": json.dumps({"version": NEWER, "tag": f"v{NEWER}", "flags": []}),
        "j/descriptions": json.dumps(list(described.descriptions)),
        f"j/getoption {NEW_SIZE}": json.dumps({"option": NEW_SIZE, "set": True, "int": 7}),
    }


async def newer_first_start(fake: FakeHyprland, root: Path) -> None:
    """A start that read Hyprland 0.59.0: its extra Option gets a Row, and the user sets it."""
    runner = Runner()
    session = session_for(fake, root, runner, live_hyprland=newer_described())
    session.start()
    await runner.settle()
    session.set_option(GAPS_IN, 12)
    session.set_option(NEW_SIZE, 7)
    await session.aclose()


class TestMissedStartupRead:
    """#214: the startup read missed, so the Schema lacks what only the supplement knew."""

    @pytest.fixture(autouse=True)
    def shipped_schemas(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shipped = synthetic_schema_dir(tmp_path / "schema", "0.56.2", "0.58.0")
        monkeypatch.setenv(SCHEMA_DIR_ENV, str(shipped))

    def test_a_live_option_is_kept_without_a_retired_notice(self, tmp_path: Path) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await newer_first_start(fake, tmp_path)
            assert "new_size = 7" in module(tmp_path)

            session, notices = await start(fake, tmp_path, None)

            assert notices == []
            assert manifest(tmp_path).retired == {
                NEW_SIZE: RetiredValue(NEWER, 7, RetireReason.NOT_IN_SCHEMA)
            }
            assert session.live_hyprland == newer_described()

        run_with_fake(scenario, FakeHyprland(newer_conversation(), reload_emits_event=True))

    def test_the_next_start_that_reads_it_restores_the_value_and_says_nothing(
        self, tmp_path: Path
    ) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await newer_first_start(fake, tmp_path)
            await start(fake, tmp_path, None)
            assert "new_size" not in module(tmp_path)
            before_restore = session_for(
                fake, tmp_path, Runner(), live_hyprland=newer_described()
            )
            # Its Row exists from construction; until the restore, it is not badged Retired.
            assert before_restore.retired_in(before_restore.schema[NEW_SIZE]) is None

            session, notices = await start(fake, tmp_path, newer_described())

            assert notices == []
            assert "new_size = 7" in module(tmp_path)
            assert session.model.get(NEW_SIZE) == 7
            assert manifest(tmp_path).retired == {}
            assert manifest(tmp_path).retired_notices == ()

        run_with_fake(scenario, FakeHyprland(newer_conversation(), reload_emits_event=True))

    def test_when_the_read_on_connect_misses_too_retirement_runs_as_before(
        self, tmp_path: Path
    ) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await newer_first_start(fake, tmp_path)
            del fake.conversation["j/descriptions"]

            session, notices = await start(fake, tmp_path, None)

            assert session.live_hyprland is None
            assert notices == [RetiredNotice("0.56.2", (NEW_SIZE,))]
            assert manifest(tmp_path).retired == {NEW_SIZE: RetiredValue("0.56.2", 7)}

        run_with_fake(scenario, FakeHyprland(newer_conversation(), reload_emits_event=True))

    def test_after_a_startup_read_nothing_is_asked_again_and_removal_is_announced(
        self, tmp_path: Path
    ) -> None:
        """One read: the startup one. A name the running Hyprland lacks is still REMOVED."""

        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            reads: list[LiveHyprland] = []

            def read_live() -> LiveHyprland:
                reads.append(live("0.57.0", without=(RESIZE,)))
                return reads[-1]

            fake.requests.clear()
            runner = Runner()
            session = Session(
                spawn=runner.spawn,
                schema=SCHEMA,
                paths=ConfigPaths.rooted_at(tmp_path),
                app_version=SAMPLE_APP_VERSION,
                connect=lambda: fake.instance,
                read_live=read_live,
            )
            notices: list[Notice] = []
            session.on_notice = notices.append
            session.start()
            await runner.settle()
            await session.aclose()

            assert len(reads) == 1
            assert "j/version" not in fake.requests
            assert "j/descriptions" not in fake.requests
            assert notices == [RetiredNotice("0.57.0", (RESIZE,))]
            assert manifest(tmp_path).retired == {RESIZE: RetiredValue("0.57.0", True)}

        run_with_fake(scenario, FakeHyprland(newer_conversation(), reload_emits_event=True))


class TestRow:
    """ADR-0012: "the Row is badged" -- the Session says which release retired an Option."""

    def test_a_retired_option_names_its_release_and_others_do_not(self, tmp_path: Path) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            session, _ = await start(fake, tmp_path, live("0.57.0", without=(RESIZE,)))

            assert session.retired_in(SCHEMA[RESIZE]) == "0.57.0"
            assert session.retired_in(SCHEMA[GAPS_IN]) is None

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))

    def test_the_kept_value_is_typed_for_its_row(self, tmp_path: Path) -> None:
        """#215: the Row shows the value the Manifest kept, as its own control would."""

        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            for name in (RESIZE, GAPS_IN):
                fake.conversation[f"j/getoption {name}"] = NO_SUCH_OPTION
            session, _ = await start(fake, tmp_path, live("0.57.0", without=(RESIZE, GAPS_IN)))

            assert session.kept_value(RESIZE) is True
            assert session.kept_value(GAPS_IN) == CssGaps(12, 12, 12, 12)
            assert session.kept_value("general:border_size") is UNSET

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


class TestReadOnly:
    """#215: a Retired Option is not edited, by its Row or by any other caller."""

    def test_no_edit_or_reset_reaches_a_retired_option(self, tmp_path: Path) -> None:
        async def scenario(fake: FakeHyprland) -> None:
            await first_start(fake, tmp_path)
            fake.conversation[f"j/getoption {RESIZE}"] = NO_SUCH_OPTION
            gone = live("0.57.0", without=(RESIZE,))
            await start(fake, tmp_path, gone)

            runner = Runner()
            session = session_for(fake, tmp_path, runner, live_hyprland=gone)
            session.start()
            await runner.settle()
            session.set_option(RESIZE, False)
            session.touch_option(RESIZE, False)
            session.preview_option(RESIZE, False)
            session.unset_option(RESIZE)
            session.set_option(GAPS_IN, 4)
            await runner.settle()
            await session.aclose()

            assert session.model.get(RESIZE) is UNSET
            assert session.model.get(GAPS_IN) == CssGaps(4, 4, 4, 4), "the rest still edits"
            assert "resize_on_border" not in module(tmp_path)
            assert manifest(tmp_path).retired == {RESIZE: RetiredValue("0.57.0", True)}
            assert session.kept_value(RESIZE) is True

        run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
