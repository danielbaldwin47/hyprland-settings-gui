"""Undo and auto-revert, driven through a whole `Session` against a scripted compositor.

Deliberately not against `UndoStack` -- that has its own tests and knows nothing worth
getting wrong. Everything interesting here is about *when* a step exists: a slider drag is
fifty model writes and one gesture, a keystroke burst is several and one, and a gesture
Hyprland rejects is none at all.

Auto-revert is the same question from the other end. ADR-0016 asks for three things at once
-- the file restored from its Snapshot, the model delta reverted, the gesture dropped from
the stack -- and the tests below assert all three from outside, because an implementation
that did two of them would look perfectly healthy until the user pressed Ctrl+Z.

`BORDER_SIZE` rather than a gaps Option for anything that compares a value: both live in
`options/general.lua`, which is what these tests need, and an int compares as itself where a
css-gaps value is a four-side record.
"""

from __future__ import annotations

from pathlib import Path

from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, drain_events, section_conversation, session_for

from hyprtweaker.engine.apply import ApplyOutcome, ApplyResult
from hyprtweaker.engine.model import UNSET, CssGaps
from hyprtweaker.session import AutoRevert, Session

BORDER_SIZE = "general:border_size"
GAPS_IN = "general:gaps_in"
GAPS_OUT = "general:gaps_out"
ROUNDING = "decoration:rounding"

GENERAL_MODULE = "options/general.lua"
DECORATION_MODULE = "options/decoration.lua"

CONFIG_ERRORS = (
    '[\n\t"/home/user/.config/hypr/hyprtweaker/{module}:4: '
    "unknown config key 'general.nope'\"\n]\n"
)
"""Spelled the way Hyprland spells it -- absolute, `file:line`-prefixed -- and deliberately
*not* the temp path this test's App dir really lives at. Attribution matches by suffix on
purpose: the path the compositor opened may have come through a symlinked dotfile directory
(`ownership.py`), and a test using the real path would never notice if that broke."""


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


def reject_every_reload(fake: FakeHyprland, module: str = GENERAL_MODULE) -> None:
    fake.conversation["j/configerrors"] = CONFIG_ERRORS.format(module=module)


def reject_the_next_reload(fake: FakeHyprland, module: str = GENERAL_MODULE) -> None:
    """Fail one reload and then be healthy -- what a rejected edit actually looks like.

    The restore transaction has to reach a compositor that accepts it, or the test would be
    measuring the escalation path instead of the recovery.
    """
    clean = fake.conversation["j/configerrors"]
    broken = CONFIG_ERRORS.format(module=module)
    armed = [True]

    def hook(request: str, _seen: int) -> None:
        # Counted here rather than off the fake's own request tally: a scenario that has
        # already applied something cleanly has read `configerrors` before, and "the next
        # one" has to mean the next one *after arming*.
        if request != "j/configerrors":
            return
        fake.conversation["j/configerrors"] = broken if armed[0] else clean
        armed[0] = False

    fake.on_request = hook


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    """Wait out the apply queue, then anything the results of it spawned.

    Both, and in that order: `drain` returns when no transaction is in flight, but an
    auto-revert is *spawned* from inside the one that just finished, so the session can be
    idle while the recovery has not started. `runner.settle` awaits that task, and the second
    drain covers the transaction it went on to queue.
    """
    await session.drain()
    await runner.settle()
    await session.drain()


def module_bytes(root: Path, module: str) -> bytes:
    return (root / "hypr" / "hyprtweaker" / module).read_bytes()


# --- one gesture, one step --------------------------------------------------------------------


def test_a_decided_edit_becomes_one_undo_step(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        assert session.can_undo
        step = session.last_gesture
        assert step is not None and step.names == (ROUNDING,)
        assert step.edits[0].before is UNSET
        assert step.edits[0].after == 12

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


def test_a_whole_drag_is_one_step_from_value_at_press_to_value_at_release(
    tmp_path: Path,
) -> None:
    """ADR-0010 §Undo, stated as a property of the session rather than of a widget.

    Every tick is an Eval preview that moves the model and writes nothing; the release is the
    one transaction. A stack that recorded the ticks would need fifty Ctrl+Z to get back to
    where the pointer went down.
    """

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation.update(conversation(**{ROUNDING: 4}))
        session.set_option(ROUNDING, 4)
        await settle(session, runner)

        for value in (5, 6, 7, 8):
            session.preview_option(ROUNDING, value)
        fake.conversation.update(conversation(**{ROUNDING: 9}))
        session.set_option(ROUNDING, 9)
        await settle(session, runner)

        step = session.last_gesture
        assert step is not None
        assert (step.edits[0].before, step.edits[0].after) == (4, 9)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_coalesced_keystroke_burst_is_one_step_over_every_option_it_touched(
    tmp_path: Path,
) -> None:
    """The queue has already decided this was one burst; the compositor saw one reload. A
    gesture the compositor saw as one change is one change to the undo stack too."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation.update(
            conversation(**{GAPS_IN: CssGaps.uniform(8), GAPS_OUT: CssGaps.uniform(15)})
        )
        session.touch_option(GAPS_IN, 5)
        session.touch_option(GAPS_OUT, 15)
        session.touch_option(GAPS_IN, 8)
        await settle(session, runner)

        assert fake.requests.count("reload") == 1
        step = session.last_gesture
        assert step is not None
        assert sorted(step.names) == sorted((GAPS_IN, GAPS_OUT))
        assert all(edit.before is UNSET for edit in step.edits)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_read_only_session_has_nothing_to_undo(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        session.set_read_only("Hyprland is no longer running")

        assert not session.can_undo
        assert not session.undo()

    run_with_fake(
        scenario, FakeHyprland(conversation(**{ROUNDING: 12}), reload_emits_event=True)
    )


# --- undoing --------------------------------------------------------------------------------


def test_undo_puts_the_value_back_through_a_normal_transaction(tmp_path: Path) -> None:
    """A *normal* transaction: one reload, the same write path any edit takes. An undo that
    wrote files directly would be a second way for bytes to reach the App dir."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation.update(conversation(**{ROUNDING: 4}))
        session.set_option(ROUNDING, 4)
        await settle(session, runner)

        fake.conversation.update(conversation(**{ROUNDING: 9}))
        session.set_option(ROUNDING, 9)
        await settle(session, runner)

        fake.conversation.update(conversation(**{ROUNDING: 4}))
        reloads_before = fake.requests.count("reload")
        assert session.undo()
        await settle(session, runner)

        assert session.model.get(ROUNDING) == 4
        assert fake.requests.count("reload") == reloads_before + 1
        assert b"rounding = 4" in module_bytes(tmp_path, DECORATION_MODULE)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_undo_walks_back_one_gesture_at_a_time_and_then_stops(tmp_path: Path) -> None:
    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        for value in (4, 9):
            fake.conversation.update(conversation(**{ROUNDING: value}))
            session.set_option(ROUNDING, value)
            await settle(session, runner)

        fake.conversation.update(conversation(**{ROUNDING: 4}))
        assert session.undo()
        await settle(session, runner)
        assert session.model.get(ROUNDING) == 4

        fake.conversation.update(conversation())
        assert session.undo()
        await settle(session, runner)
        assert session.model.get(ROUNDING) is UNSET

        assert not session.can_undo
        assert not session.undo()

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_undoing_does_not_itself_become_a_step(tmp_path: Path) -> None:
    """There is no redo tier in v1, and a stack that recorded its own reversals would turn
    Ctrl+Z pressed twice into a value oscillating rather than walking back."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation.update(conversation(**{ROUNDING: 12}))
        session.set_option(ROUNDING, 12)
        await settle(session, runner)

        fake.conversation.update(conversation())
        session.undo()
        await settle(session, runner)

        assert not session.can_undo

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_foreign_reload_voids_the_open_gesture_but_not_the_stack(tmp_path: Path) -> None:
    """A recorded step is a model delta and replays through a normal transaction whatever
    else has happened since. A *half-open* one would span somebody else's reload."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)

        fake.conversation.update(conversation(**{ROUNDING: 12}))
        session.set_option(ROUNDING, 12)
        await settle(session, runner)
        assert session.can_undo

        session.preview_option(ROUNDING, 20)
        await fake.emit("configreloaded")
        await drain_events(runner)
        await settle(session, runner)

        # The drag never became a step, and the decided edit before it is still there.
        step = session.last_gesture
        assert step is not None
        assert step.names == (ROUNDING,)
        assert step.edits[0].after == 12

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- auto-revert (ADR-0016) -------------------------------------------------------------------


def test_a_rejected_own_write_reverts_the_model_the_file_and_the_gesture(
    tmp_path: Path,
) -> None:
    """All three at once, because an implementation doing two of them looks healthy until
    the user presses Ctrl+Z and gets the value Hyprland just refused."""
    reverts: list[AutoRevert] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append

        fake.conversation.update(conversation(**{BORDER_SIZE: 5}))
        session.set_option(BORDER_SIZE, 5)
        await settle(session, runner)
        good = module_bytes(tmp_path, GENERAL_MODULE)

        reject_the_next_reload(fake)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        assert session.model.get(BORDER_SIZE) == 5
        assert module_bytes(tmp_path, GENERAL_MODULE) == good
        assert session.last_gesture is not None
        assert session.last_gesture.edits[0].after == 5

        assert len(reverts) == 1
        assert reverts[0].keys == (BORDER_SIZE,)
        assert reverts[0].modules == (GENERAL_MODULE,)
        assert reverts[0].restored
        assert reverts[0].errors and "general.lua:4" in reverts[0].errors[0]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_the_reverted_file_is_byte_for_byte_the_pre_write_snapshot(tmp_path: Path) -> None:
    """The Journal is what makes the recovery checkable rather than merely plausible: the
    revert re-renders the model, and the Snapshot is the evidence that re-render is the same
    thing as putting the bytes back."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = lambda _revert: None

        fake.conversation.update(conversation(**{BORDER_SIZE: 5}))
        session.set_option(BORDER_SIZE, 5)
        await settle(session, runner)

        reject_the_next_reload(fake)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        rejected = next(
            entry
            for entry in reversed(session.journal.entries())
            if entry.outcome == str(ApplyOutcome.CONFIG_ERRORS)
        )
        change = rejected.change(GENERAL_MODULE)
        assert change is not None
        assert session.journal.snapshot(change.before) == module_bytes(tmp_path, GENERAL_MODULE)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_the_failed_gesture_never_reaches_the_undo_stack(tmp_path: Path) -> None:
    """ADR-0016's "drop the failed gesture from the stack", held by construction: a step is
    recorded from the transaction's *result*, so a rejected one is never pushed at all."""

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = lambda _revert: None

        reject_the_next_reload(fake)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        assert not session.can_undo
        assert session.model.get(BORDER_SIZE) is UNSET

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_rejected_write_reports_through_on_reverted_rather_than_on_applied(
    tmp_path: Path,
) -> None:
    """One toast, not a failure chased by a recovery."""
    reverts: list[AutoRevert] = []
    results: list[ApplyResult] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append
        session.on_applied = results.append

        reject_the_next_reload(fake)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        assert len(reverts) == 1
        assert [result.outcome for result in results] == [ApplyOutcome.OK]

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_an_error_in_a_file_the_app_never_writes_is_not_auto_reverted(tmp_path: Path) -> None:
    """ADR-0016 attribution: the app cannot fix `user.lua`, and answering an error it did not
    cause with an automatic write would be exactly the "manager over your dots" behaviour
    ADR-0005 refuses. The edit stands, and the failure is reported the ordinary way."""
    reverts: list[AutoRevert] = []
    results: list[ApplyResult] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append
        session.on_applied = results.append

        fake.conversation["j/configerrors"] = (
            '[\n\t"/home/user/.config/hypr/user.lua:12: attempt to call a nil value"\n]\n'
        )
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        assert reverts == []
        assert session.model.get(BORDER_SIZE) == 9
        assert results[-1].outcome is ApplyOutcome.CONFIG_ERRORS
        assert session.can_undo

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


def test_a_restore_that_is_itself_rejected_stops_auto_writing(tmp_path: Path) -> None:
    """The cycle guard ADR-0016 asks for: "if the restore transaction itself errors ...
    escalate to the Banner and stop auto-writing until the user acts". Every reload here
    reports the same error, so a second auto-revert would be an endless loop of writes."""
    reverts: list[AutoRevert] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.on_reverted = reverts.append

        reject_every_reload(fake)
        session.set_option(BORDER_SIZE, 9)
        await settle(session, runner)

        assert len(reverts) == 1
        assert not reverts[0].restored
        assert session.recovery_halted
        # Exactly two: the rejected write and its one restore. A third would be the loop.
        assert fake.requests.count("reload") == 2

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
