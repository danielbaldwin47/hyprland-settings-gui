"""The whole edit-to-compositor path, with no display anywhere near it.

`Session` is the app minus its widgets, so everything the shell tracer claims can be asserted
here against a scripted socket: a change reaches the compositor as one reload, the values
survive closing and reopening the app, and a session with no compositor says so instead of
writing a config nobody will read.

The one thing this tier cannot answer is whether the compositor *did* anything with the
write. That is `tests/integration/test_shell_session.py`, against a nested Hyprland.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from _fake_hyprland import NO_CONFIG_ERRORS, OK, FakeHyprland, option_reply, run_with_fake
from _support import SAMPLE_VERSION, SCHEMA_DIR

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult
from hyprtweaker.engine.ipc import Instance, NoInstance
from hyprtweaker.engine.model import UNSET, CssGaps
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.engine.schema import load_schema
from hyprtweaker.engine.state import Manifest, ModuleRecord
from hyprtweaker.session import Session

SCHEMA = load_schema(SAMPLE_VERSION, SCHEMA_DIR)
APP_VERSION = "0.0.0-test"

GAPS_IN = "general:gaps_in"
ROUNDING = "decoration:rounding"
XWAYLAND_ENABLED = "xwayland:enabled"


class Runner:
    """A `spawn` for tests: real tasks on the running loop, awaitable to quiescence."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task[None]] = []

    def spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        self._tasks.append(asyncio.create_task(coro))

    async def settle(self) -> None:
        """Wait for every spawned task, including ones spawned by the ones we waited on."""
        while self._tasks:
            batch, self._tasks = self._tasks, []
            await asyncio.gather(*batch)


def section_conversation(*sections: str, **set_values: Any) -> dict[str, str]:
    """A compositor that answers about whole Sections, not just a handful of keys.

    Startup re-reads every Option of every Section the app owns a Module for, so a script
    covering only the interesting keys would have the session fall over on the first
    uninteresting one -- and pass or fail for the wrong reason.
    """
    conversation = {"reload": OK, "j/configerrors": NO_CONFIG_ERRORS}
    for section in sections:
        for option in SCHEMA.section(section):
            value = set_values.get(option.name)
            conversation[f"j/getoption {option.name}"] = option_reply(
                option,
                value if value is not None else option.default,
                live_set=value is not None,
            )
    return conversation


def session_for(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    return Session(
        spawn=runner.spawn,
        schema=SCHEMA,
        paths=ConfigPaths.rooted_at(root),
        app_version=APP_VERSION,
        connect=lambda: fake.instance,
    )


# --- read-only sessions -----------------------------------------------------------------------


def test_without_a_compositor_the_session_is_read_only_and_says_why(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = Session(
            spawn=runner.spawn,
            schema=SCHEMA,
            paths=ConfigPaths.rooted_at(tmp_path),
            app_version=APP_VERSION,
            connect=_no_instance,
        )
        session.start()
        await runner.settle()

        assert not session.live
        assert session.offline_reason is not None
        assert "not running under Hyprland" in session.offline_reason

    run_with_fake(scenario)


def test_a_read_only_session_writes_nothing_and_changes_nothing(tmp_path: Path) -> None:
    """Instant apply means a change *is* a write plus a reload (ADR-0003). With no reload
    to be had, a write would leave values on disk that the next launch cannot read back --
    the app would open showing defaults over a config that says otherwise.

    The model is left alone too. Accepting the edit in memory would leave it holding a
    value that exists nowhere else -- no file, no compositor -- which a later re-read could
    not clear and a reconnecting session would write without being asked again.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = Session(
            spawn=runner.spawn,
            schema=SCHEMA,
            paths=ConfigPaths.rooted_at(tmp_path),
            app_version=APP_VERSION,
            connect=_no_instance,
        )
        session.start()
        await runner.settle()

        session.set_option(ROUNDING, 12)
        await session.aclose()

        assert session.model.get(ROUNDING) is UNSET
        assert not (tmp_path / "hypr").exists()

    run_with_fake(scenario)


# --- an edit reaches the compositor -----------------------------------------------------------


def test_an_edit_is_written_and_applied_as_one_reload(tmp_path: Path) -> None:
    results: list[ApplyResult] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.on_applied = results.append
        session.start()
        await runner.settle()
        assert session.live

        session.set_option(GAPS_IN, 12)
        await session.aclose()

        module = tmp_path / "hypr" / "hyprtweaker" / "options" / "general.lua"
        assert module.is_file()
        assert "gaps_in" in module.read_text()
        assert fake.requests.count("reload") == 1
        assert [result.outcome for result in results] == [ApplyOutcome.OK]

    run_with_fake(
        scenario,
        FakeHyprland(
            section_conversation("general", **{GAPS_IN: CssGaps(12, 12, 12, 12)}),
            reload_emits_event=True,
        ),
    )


def test_edits_made_moments_before_closing_are_still_flushed(tmp_path: Path) -> None:
    """A change is inside the apply debounce for ~150 ms. Letting the window go first
    would drop one the user just watched land in the UI."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()

        session.touch_option(GAPS_IN, 12)
        await session.aclose()

        assert fake.requests.count("reload") == 1

    run_with_fake(
        scenario,
        FakeHyprland(
            section_conversation("general", **{GAPS_IN: CssGaps(12, 12, 12, 12)}),
            reload_emits_event=True,
        ),
    )


def test_a_restart_flagged_write_is_remembered_before_the_ui_is_told(tmp_path: Path) -> None:
    """ "Applied to file, effective after Hyprland restart" (`CONTEXT.md`), and the Row's
    "Pending restart" pill reads it off the session (ADR-0013).

    The ordering is the load-bearing part: `on_applied` is what makes the window re-decide
    that Row's chrome, so the session has to already know the key is pending by the time it
    fires -- otherwise the pill lands one apply late.
    """
    seen: list[frozenset[str]] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.on_applied = lambda _result: seen.append(session.pending_restart)
        session.start()
        await runner.settle()
        assert session.pending_restart == frozenset()

        session.set_option(XWAYLAND_ENABLED, False)
        await session.aclose()

        assert session.pending_restart == frozenset({XWAYLAND_ENABLED})
        assert seen == [frozenset({XWAYLAND_ENABLED})]

    run_with_fake(
        scenario,
        FakeHyprland(
            section_conversation("xwayland", **{XWAYLAND_ENABLED: False}),
            reload_emits_event=True,
        ),
    )


def test_an_edit_that_needs_no_restart_leaves_the_pending_set_empty(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()

        session.set_option(GAPS_IN, 12)
        await session.aclose()

        assert session.pending_restart == frozenset()

    run_with_fake(
        scenario,
        FakeHyprland(
            section_conversation("general", **{GAPS_IN: CssGaps(12, 12, 12, 12)}),
            reload_emits_event=True,
        ),
    )


# --- round trip -------------------------------------------------------------------------------


def test_values_round_trip_after_closing_and_reopening_the_app(tmp_path: Path) -> None:
    """The acceptance criterion, end to end at this tier.

    The second session is a genuinely new object over the same App dir -- it recovers what
    the first one wrote from the compositor that loaded it, because the app cannot yet parse
    its own Lua back (that reader is #62).
    """
    conversation = section_conversation("general", **{GAPS_IN: CssGaps(12, 12, 12, 12)})

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        first = session_for(fake, tmp_path, runner)
        first.start()
        await runner.settle()
        first.set_option(GAPS_IN, 12)
        await first.aclose()

        runner_two = Runner()
        second = session_for(fake, tmp_path, runner_two)
        second.start()
        await runner_two.settle()

        assert second.live
        assert second.model.get(GAPS_IN) == CssGaps(12, 12, 12, 12)
        assert second.is_modified(SCHEMA[GAPS_IN])
        await second.aclose()

    run_with_fake(scenario, FakeHyprland(conversation, reload_emits_event=True))


def test_a_fresh_app_dir_adopts_nothing_the_compositor_already_sets(tmp_path: Path) -> None:
    """Spec story 13: a user with no config opens the app with everything Unset.

    The compositor here reports `general:gaps_in` as set -- by `user.lua`, by a rice, by
    anything. The app has written no Module for that Section, so it does not claim it.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()

        assert session.live
        assert session.model.get(GAPS_IN) is UNSET
        assert len(session.model) == 0
        await session.aclose()

    run_with_fake(
        scenario,
        FakeHyprland(section_conversation("general", **{GAPS_IN: CssGaps(6, 6, 6, 6)})),
    )


# --- somebody else changed the config -------------------------------------------------------


def test_a_foreign_reload_re_reads_the_model_and_tells_the_ui(tmp_path: Path) -> None:
    """ADR-0010: a `configreloaded` the app did not cause means everything it holds may be
    stale, so the whole model is re-read rather than merged."""
    notifications: list[None] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()

        session.model.set(GAPS_IN, 2)
        session.on_state_changed = lambda: notifications.append(None)

        fake.conversation[f"j/getoption {GAPS_IN}"] = option_reply(
            SCHEMA[GAPS_IN], CssGaps(9, 9, 9, 9)
        )
        await fake.emit("configreloaded")
        await _drain_events(runner)

        assert session.model.get(GAPS_IN) == CssGaps(9, 9, 9, 9)
        assert notifications, "the window was told to refresh its Rows"
        await session.aclose()

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


def test_a_foreign_reload_also_re_reads_owned_keys_the_model_does_not_hold(
    tmp_path: Path,
) -> None:
    """The half a "re-read what the model holds" would miss.

    The app owns `general:gaps_in` -- the Manifest records it having written the Module that
    sets it -- but this session recovered nothing, because at startup the compositor was
    running a config that never loaded that Module. When the config is fixed and reloaded,
    a re-read scoped to `model.set_options()` asks about nothing at all and the app stays
    blind to its own value. ADR-0010 asks for a *full* re-read for this reason.
    """
    _write_manifest(tmp_path, {"options/general.lua": (GAPS_IN,)})

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()

        assert len(session.model) == 0, "the Module was owned but not loaded"

        fake.conversation[f"j/getoption {GAPS_IN}"] = option_reply(
            SCHEMA[GAPS_IN], CssGaps(9, 9, 9, 9)
        )
        await fake.emit("configreloaded")
        await _drain_events(runner)

        assert session.model.get(GAPS_IN) == CssGaps(9, 9, 9, 9)
        await session.aclose()

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


def test_a_compositor_that_exits_makes_the_session_read_only(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live

        await fake.wait_for_listeners(1)
        await fake.drop_listeners()
        await asyncio.sleep(0.05)

        assert not session.live
        assert session.offline_reason == "Hyprland is no longer running"

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))


# --- helpers --------------------------------------------------------------------------------


def _no_instance() -> Instance:
    raise NoInstance("HYPRLAND_INSTANCE_SIGNATURE is unset -- not running under Hyprland")


def _write_manifest(root: Path, modules: dict[str, tuple[str, ...]]) -> None:
    """An App dir that records what an earlier session wrote, with no Modules on disk."""
    paths = ConfigPaths.rooted_at(root)
    paths.app_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(
        app_version=APP_VERSION,
        schema_version=SCHEMA.hyprland_version,
        modules={
            name: ModuleRecord.of("-- unread", options) for name, options in modules.items()
        },
    )
    paths.manifest.write_text(manifest.render(), encoding="utf-8")


async def _drain_events(runner: Runner) -> None:
    """Let the event stream dispatch, then wait for whatever it spawned."""
    await asyncio.sleep(0.05)
    await runner.settle()


def test_closing_a_session_that_never_connected_still_reports_done(tmp_path: Path) -> None:
    """The window holds itself open until `close` calls back.

    With no compositor -- or, on old PyGObject, with no asyncio integration at all and a
    `spawn` that does nothing -- there is no coroutine to carry the callback, and routing
    the case through the loop would leave a window that cannot be closed.
    """
    done: list[None] = []
    session = Session(
        spawn=lambda coro: coro.close(),
        schema=SCHEMA,
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=_no_instance,
    )
    session.start()

    session.close(lambda: done.append(None))

    assert done == [None]
