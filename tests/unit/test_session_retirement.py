"""ADR-0012 §Retirement through the Session, from one app start to the next.

Each test runs the app the way a user does: a first session writes an Option, the next
starts against a Hyprland (or a Schema) that no longer has it. What is asserted is what the
user's disk and screen end up holding -- the Module bytes against a golden, the value in
the Manifest, the notice -- never which retirement function ran.
"""

from __future__ import annotations

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
)

from hyprtweaker.engine.importer.lua import sandbox
from hyprtweaker.engine.ipc import LiveHyprland
from hyprtweaker.engine.model import UNSET, CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import Schema
from hyprtweaker.engine.state import Manifest, RetiredValue
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
