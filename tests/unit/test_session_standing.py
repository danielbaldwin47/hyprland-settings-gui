"""Whether a transaction stands, and what the model, the undo stack and the Banner do if not.

One rule (#227), driven through a whole `Session` against a scripted compositor. A
transaction **does not stand** when Hyprland rejected a Module it wrote, when it aborted
before writing, or when its write failed: its edits leave the model, and no step reaches the
stack (ADR-0016 §Auto-revert, step 2). Everything else stands -- a `user.lua` error, a
read-back mismatch, a compositor that went away, and a timeout, whose re-read is the check --
and its step is recorded, Entity steps as Option steps. Separately, a result that ran no
reload knows nothing about the config, so it never clears the Banner.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, drain_events, section_conversation, session_for

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult, EntityStep, Step, UndoStep
from hyprtweaker.engine.model import UNSET, Bind, DispatcherCall
from hyprtweaker.session import AutoRevert, Session

BORDER_SIZE = "general:border_size"
ROUNDING = "decoration:rounding"
GENERAL_MODULE = "options/general.lua"
DECORATION_MODULE = "options/decoration.lua"
BINDS_MODULE = "binds.lua"

USER_ERROR = "[\n\t\"/home/user/.config/hypr/user.lua:12: unexpected symbol near '}'\"\n]\n"
CLEAN = '[\n\t""\n]\n'


def own_error(module: str) -> str:
    """A `configerrors` reply blaming one of the app's own Modules, as Hyprland spells it."""
    return f'[\n\t"/home/user/.config/hypr/hyprtweaker/{module}:4: unexpected symbol"\n]\n'


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


def exec_bind(keys: str) -> Bind:
    return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))


def bind_keys(session: Session) -> list[str]:
    return [bind.keys for bind in session.model.entities.binds]


def module_bytes(root: Path, module: str) -> bytes:
    return (root / "hypr" / "hyprtweaker" / module).read_bytes()


def reject_the_next_reload(fake: FakeHyprland, errors: str) -> None:
    """Answer the next `configerrors` with `errors`, and every later one cleanly."""
    clean = fake.conversation["j/configerrors"]
    armed = [True]

    def hook(request: str, _seen: int) -> None:
        if request != "j/configerrors":
            return
        fake.conversation["j/configerrors"] = errors if armed[0] else clean
        armed[0] = False

    fake.on_request = hook


def fail_once(
    monkeypatch: pytest.MonkeyPatch, target: object, name: str, error: Exception
) -> None:
    """Make `target.name` raise `error` on its next call, and behave from then on."""
    real = getattr(target, name)
    armed = [True]

    def failing(*args: Any, **kwargs: Any) -> Any:
        if armed[0]:
            armed[0] = False
            raise error
        return real(*args, **kwargs)

    monkeypatch.setattr(target, name, failing)


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    """The queue, what its results spawned (a revert, a timeout's re-read), the queue again."""
    await session.drain()
    await runner.settle()
    await session.drain()
    await runner.settle()


# --- a timeout stands, and its re-read is the check -----------------------------------------


def test_a_timed_out_bind_is_undone_first_and_the_step_beneath_survives(tmp_path: Path) -> None:
    """Review of #151 § 3: the timed-out add used to leave the model with no step, and the next
    Ctrl+Z found bind A's step stale and destroyed it. `binds.lua` holds B, so B is kept."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)

        fake.reload_emits_event = False
        session.add_bind(exec_bind("SUPER + B"))
        await settle(session, runner)
        assert bind_keys(session) == ["SUPER + A", "SUPER + B"], "the re-read kept B"

        fake.reload_emits_event = True
        assert session.undo()
        await settle(session, runner)
        assert bind_keys(session) == ["SUPER + A"]

        assert session.undo(), "bind A's step was dropped as stale"
        await settle(session, runner)
        assert bind_keys(session) == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_timed_out_edit_keeps_the_users_value_in_the_model_and_the_file(
    tmp_path: Path,
) -> None:
    """Finding 11 of the #153 review: the timeout's re-read took the old live value into the
    model while the file held the new one, so the next write in that Section silently put
    the old value back over the user's edit."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(BORDER_SIZE, 5)
        await settle(session, runner)

        fake.reload_emits_event = False
        session.set_option(BORDER_SIZE, 10)
        await settle(session, runner)

        assert session.model.get(BORDER_SIZE) == 10
        assert b"border_size = 10" in module_bytes(tmp_path, GENERAL_MODULE)
        assert session.can_undo
        fake.reload_emits_event = True
        assert session.undo()
        await settle(session, runner)
        assert session.model.get(BORDER_SIZE) == 5

    run_with_fake(
        scenario, FakeHyprland(conversation(**{BORDER_SIZE: 5}), reload_emits_event=True)
    )


def test_a_timed_out_bind_whose_file_the_re_read_changed_keeps_no_step(tmp_path: Path) -> None:
    """The re-read is the check: a `binds.lua` changed by hand while the reload was unanswered
    is adopted, and a step over the list it replaced would write the old list over it."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)
        hand_edited = module_bytes(tmp_path, BINDS_MODULE)

        fake.reload_emits_event = False

        def hand_edit_on_reload(request: str, _seen: int) -> None:
            if request == "reload":
                (tmp_path / "hypr" / "hyprtweaker" / BINDS_MODULE).write_bytes(hand_edited)

        fake.on_request = hand_edit_on_reload
        session.add_bind(exec_bind("SUPER + B"))
        await settle(session, runner)

        assert bind_keys(session) == ["SUPER + A"], "the re-read adopted the file"
        assert session.last_gesture is None

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- an own-write rejection is reverted, Entity edits as Option edits -------------------------


def test_a_rejected_entity_only_write_is_reverted_like_an_option_edit(tmp_path: Path) -> None:
    """ADR-0016 §Auto-revert, all three steps, for an edit with no Option delta at all."""
    reverts: list[AutoRevert] = []
    recorded: list[Step] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)
        good = module_bytes(tmp_path, BINDS_MODULE)
        below = session.last_gesture
        session.on_recorded = recorded.append

        reject_the_next_reload(fake, own_error(BINDS_MODULE))
        session.add_bind(exec_bind("SUPER + B"))
        await settle(session, runner)

        assert bind_keys(session) == ["SUPER + A"]
        assert module_bytes(tmp_path, BINDS_MODULE) == good
        assert session.last_gesture is below
        assert recorded == []
        assert not session.recovery_halted
        assert len(reverts) == 1
        assert reverts[0].keys == ()
        assert reverts[0].modules == (BINDS_MODULE,)
        assert reverts[0].restored
        assert reverts[0].outcome is ApplyOutcome.CONFIG_ERRORS

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejection_the_app_may_not_revert_records_no_option_step(tmp_path: Path) -> None:
    """Halted (ADR-0016: "stop auto-writing until the user acts"), the next rejected edit is
    left in place and reported -- and Ctrl+Z still means the gesture before it."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = lambda _revert: None
        fake.conversation.update(conversation(**{ROUNDING: 12}))
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        fake.conversation["j/configerrors"] = own_error(GENERAL_MODULE)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)
        assert session.recovery_halted, "the precondition: the restore was rejected too"

        session.set_option(BORDER_SIZE, 12)
        await settle(session, runner)

        assert session.model.get(BORDER_SIZE) == 12, "halted: nothing was written back"
        step = session.last_gesture
        assert isinstance(step, UndoStep) and step.names == (ROUNDING,)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_user_lua_error_keeps_the_entity_step(tmp_path: Path) -> None:
    """Not this transaction's error: the bind is on disk, so Ctrl+Z must be able to take it
    back (the Option side is `test_an_error_in_a_file_the_app_never_writes_...`)."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        fake.conversation["j/configerrors"] = USER_ERROR
        session = await live_session(fake, tmp_path, runner)

        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)

        assert bind_keys(session) == ["SUPER + A"]
        step = session.last_gesture
        assert isinstance(step, EntityStep) and step.title == "Keybind added"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- nothing reached disk, or the disk refused ------------------------------------------------


def test_an_aborted_drag_shows_the_compositor_the_old_value_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#267: nothing reached disk and nothing reloaded, so the drag's `eval` would have left
    the desktop on 25 while the model and the Row went back to 12."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**{ROUNDING: 12}))
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        assert session._applier is not None
        session.preview_option(ROUNDING, 25)
        await session._applier.flush_previews()

        fail_once(monkeypatch, session._writer, "write", ValueError("gate refused"))
        session.set_option(ROUNDING, 25)
        await settle(session, runner)
        await session._applier.flush_previews()

        assert session.model.get(ROUNDING) == 12
        assert [r for r in fake.requests if r.startswith("eval ")] == [
            "eval hl.config{decoration={rounding=25}}",
            "eval hl.config{decoration={rounding=12}}",
        ]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_an_aborted_apply_puts_the_model_back_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    results: list[ApplyResult] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        fake.conversation.update(conversation(**{ROUNDING: 12}))
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        session.on_applied = results.append
        reloads = fake.requests.count("reload")

        fail_once(monkeypatch, session._writer, "write", ValueError("gate refused"))
        session.set_option(BORDER_SIZE, 9)
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)

        assert [result.outcome for result in results] == [ApplyOutcome.ABORTED]
        assert session.model.get(BORDER_SIZE) is UNSET
        assert bind_keys(session) == []
        step = session.last_gesture
        assert isinstance(step, UndoStep) and step.names == (ROUNDING,)
        assert fake.requests.count("reload") == reloads, "an abort writes nothing back"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_failed_write_is_reverted_and_records_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reverts: list[AutoRevert] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append

        fail_once(monkeypatch, session._writer, "write", OSError(28, "No space left on device"))
        session.set_option(BORDER_SIZE, 9)
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)

        assert session.model.get(BORDER_SIZE) is UNSET
        assert bind_keys(session) == []
        assert not session.can_undo
        assert len(reverts) == 1
        assert reverts[0].outcome is ApplyOutcome.WRITE_FAILED
        assert reverts[0].keys == (BORDER_SIZE,)
        assert reverts[0].restored

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- a result that ran no reload never clears the Banner --------------------------------------


def _write_fails(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    fail_once(monkeypatch, session._writer, "write", OSError(28, "No space left on device"))
    session.set_option(BORDER_SIZE, 9)


def _write_aborts(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    fail_once(monkeypatch, session._writer, "write", ValueError("gate refused"))
    session.set_option(BORDER_SIZE, 9)


def _nothing_to_do(_monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    # Set and taken back before the queue runs: one transaction, whose bytes are on disk.
    session.set_option(BORDER_SIZE, 9)
    session.unset_option(BORDER_SIZE)


def _restore_write_fails(monkeypatch: pytest.MonkeyPatch, session: Session) -> None:
    fail_once(monkeypatch, session._writer, "restore", OSError(28, "No space left on device"))
    assert session.restore_last_good(DECORATION_MODULE)


NO_RELOAD: dict[str, Callable[[pytest.MonkeyPatch, Session], None]] = {
    "apply, write failed": _write_fails,
    "apply, aborted": _write_aborts,
    "apply, nothing to do": _nothing_to_do,
    "restore last good, write failed": _restore_write_fails,
}


@pytest.mark.parametrize("case", NO_RELOAD)
def test_a_result_that_ran_no_reload_never_clears_the_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """Review of #152, findings 2 and 21: with no reload, the result's empty `errors` say
    nothing about the config, and reading them as a clean reload cleared a Banner whose
    cause was still on disk. The fake answers cleanly from here on, so any reload this case
    did run would clear it."""
    results: list[ApplyResult] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = lambda _revert: None
        fake.conversation.update(conversation(**{ROUNDING: 3}))
        session.set_option(ROUNDING, 3)
        await settle(session, runner)

        fake.conversation["j/configerrors"] = USER_ERROR
        await fake.emit("configreloaded")
        await drain_events(runner)
        await settle(session, runner)
        assert session.health.unhealthy, "the precondition: a broken config on the Banner"
        fake.conversation["j/configerrors"] = CLEAN
        reloads = fake.requests.count("reload")
        session.on_applied = results.append

        NO_RELOAD[case](monkeypatch, session)
        await settle(session, runner)

        assert fake.requests.count("reload") == reloads, "the case ran a reload after all"
        assert session.health.unhealthy
        assert len(results) == 1, "reported once"

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejected_first_bind_is_reverted_and_its_module_goes_with_it(tmp_path: Path) -> None:
    """No `binds.lua` before the write: the revert has to leave none behind to be `restored`."""
    reverts: list[AutoRevert] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append
        assert not (tmp_path / "hypr" / "hyprtweaker" / BINDS_MODULE).exists()

        reject_the_next_reload(fake, own_error(BINDS_MODULE))
        session.add_bind(exec_bind("SUPER + A"))
        await settle(session, runner)

        assert bind_keys(session) == []
        assert not (tmp_path / "hypr" / "hyprtweaker" / BINDS_MODULE).exists()
        assert [revert.restored for revert in reverts] == [True]
        assert not session.recovery_halted

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
