"""Undo bookkeeping around a display write that does not stand (#222), against a scripted
compositor and the real Apply queue.

Two setups, named as the ticket's settlement names them. **Case 1** is ADR-0016's
auto-revert: a monitor rule write Hyprland rejects queues `_revert_transaction`, and an
Option edit is committed at a chosen moment around it -- while the rejected write is in
flight, or while the revert itself runs. The later edit stood, so it must have its own Undo
step. **Case 2** is the window's Confirm-or-revert countdown group (`begin_undo_group`, as
`MainWindow._behind_countdown` opens it, `_revert_display` closes it) with a commit inside it
that fails; the model's lists, the accepted bytes on disk and the step beneath are checked
after the failure and after the countdown's revert.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import Runner, section_conversation, session_for

from hyprtweaker.engine.apply import UndoStep
from hyprtweaker.engine.model import UNSET, MonitorRule
from hyprtweaker.engine.model.entities import DISPLAY_KINDS
from hyprtweaker.engine.monitors_catalog import revert_breaking
from hyprtweaker.session import AutoRevert, Session

ROUNDING = "decoration:rounding"
MONITORS_MODULE = "monitors.lua"
DISPLAY_CHANGED = "Display changed"

ACCEPTED = MonitorRule(output="eDP-1", fields={"mode": "1920x1080@60"})
REJECTED_MODE = {"mode": "1920x1080@144"}


def conversation(**set_values: object) -> dict[str, str]:
    return section_conversation("general", "decoration", **set_values)


def own_error(module: str) -> str:
    return f'[\n\t"/home/user/.config/hypr/hyprtweaker/{module}:4: unexpected symbol"\n]\n'


async def live_session(fake: FakeHyprland, root: Path, runner: Runner) -> Session:
    session = session_for(fake, root, runner)
    session.start()
    await runner.settle()
    assert session.live
    return session


async def settle(session: Session, runner: Runner) -> None:
    for _ in range(3):
        await session.drain()
        await runner.settle()


def module_bytes(root: Path, module: str) -> bytes:
    return (root / "hypr" / "hyprtweaker" / module).read_bytes()


def reject_next_reload_then(fake: FakeHyprland, on_reload: dict[int, Any]) -> None:
    """Reject the next reload's `configerrors` with an error in `monitors.lua`, accept every
    later one, and run `on_reload[n]` when the n-th reload from now (1-based) arrives --
    before it is answered, so mid-transaction."""
    clean = fake.conversation["j/configerrors"]
    broken = own_error(MONITORS_MODULE)
    reloads = [0]
    errors = [0]

    def hook(request: str, _seen: int) -> None:
        if request == "reload":
            reloads[0] += 1
            action = on_reload.get(reloads[0])
            if action is not None:
                action()
        elif request == "j/configerrors":
            errors[0] += 1
            fake.conversation["j/configerrors"] = broken if errors[0] == 1 else clean

    fake.on_request = hook


# --- case 1: a later Option edit around a queued auto-revert (T21) ---------------------------


@pytest.mark.parametrize(
    "moment",
    [
        pytest.param(1, id="committed-while-the-rejected-write-is-in-flight"),
        pytest.param(2, id="committed-while-the-revert-runs"),
    ],
)
def test_an_option_edit_beside_a_queued_auto_revert_gets_its_own_undo_step(
    tmp_path: Path, moment: int
) -> None:
    reverts: list[AutoRevert] = []
    recorded: list[object] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = await live_session(fake, tmp_path, runner)
        session.patch_monitor_rule("eDP-1", dict(ACCEPTED.fields))
        await settle(session, runner)
        accepted = module_bytes(tmp_path, MONITORS_MODULE)
        below = session.last_gesture
        session.on_reverted = reverts.append
        session.on_recorded = recorded.append

        fake.conversation.update(conversation(**{ROUNDING: 12}))
        reject_next_reload_then(fake, {moment: lambda: session.set_option(ROUNDING, 12)})
        session.patch_monitor_rule("eDP-1", REJECTED_MODE)
        await settle(session, runner)

        # The rejected display write is reverted (ADR-0016), whichever moment the edit took.
        assert len(reverts) == 1 and reverts[0].restored
        assert session.monitor_rules == [ACCEPTED]
        assert module_bytes(tmp_path, MONITORS_MODULE) == accepted
        assert not session.recovery_halted
        # The later edit stood, so it is the newest step, announced, and undoes alone.
        assert session.model.get(ROUNDING) == 12
        step = session.last_gesture
        assert isinstance(step, UndoStep)
        assert [(e.name, e.before, e.after) for e in step.edits] == [(ROUNDING, UNSET, 12)]
        assert recorded == [step]
        assert session.undo()
        await settle(session, runner)
        assert session.model.get(ROUNDING) is UNSET
        assert session.last_gesture is below

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


# --- case 2: a failed commit inside a display countdown group --------------------------------


def _rejected(fake: FakeHyprland, _session: Session, _monkeypatch: pytest.MonkeyPatch) -> None:
    reject_next_reload_then(fake, {})


def _write_failed(
    _fake: FakeHyprland, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = session._writer
    real = writer.write
    armed = [True]

    def failing(*args: Any, **kwargs: Any) -> Any:
        if armed[0]:
            armed[0] = False
            raise OSError(28, "No space left on device")
        return real(*args, **kwargs)

    monkeypatch.setattr(writer, "write", failing)


FAILURES = {"rejected by Hyprland": _rejected, "write failed": _write_failed}


async def failed_inside_a_countdown(
    fake: FakeHyprland,
    root: Path,
    runner: Runner,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> tuple[Session, Any, Any, bytes, Any]:
    """An accepted rule with its step, then `_behind_countdown`: snapshot, open the group,
    make a breaking change whose commit fails as `failure` says."""
    session = await live_session(fake, root, runner)
    session.patch_monitor_rule("eDP-1", dict(ACCEPTED.fields))
    await settle(session, runner)
    accepted = module_bytes(root, MONITORS_MODULE)
    below = session.last_gesture
    assert below is not None

    snapshot = session.monitor_state_snapshot()
    group = session.begin_undo_group(DISPLAY_KINDS)
    FAILURES[failure](fake, session, monkeypatch)
    assert session.patch_monitor_rule("eDP-1", REJECTED_MODE)
    await settle(session, runner)
    return session, snapshot, group, accepted, below


@pytest.mark.parametrize("failure", list(FAILURES))
def test_a_failed_commit_inside_a_display_countdown_leaves_the_accepted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    recorded: list[object] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, _snapshot, group, accepted, below = await failed_inside_a_countdown(
            fake, tmp_path, runner, monkeypatch, failure
        )
        session.on_recorded = recorded.append

        # The commit did not stand: the lists show what the disk accepted, and it is that.
        assert session.monitor_rules == [ACCEPTED]
        assert module_bytes(tmp_path, MONITORS_MODULE) == accepted
        assert not session.recovery_halted
        assert session.last_gesture is below

        # `_keep_display`: the dialog is still up, and Keep is one click away.
        session.end_undo_group(group, title=DISPLAY_CHANGED)
        await settle(session, runner)
        assert session.monitor_rules == [ACCEPTED]
        assert module_bytes(tmp_path, MONITORS_MODULE) == accepted
        assert session.last_gesture is below
        assert recorded == []

        # A later benign edit writes only itself, not the mode that never stood.
        session.patch_monitor_rule("eDP-1", {"vrr": 1})
        await settle(session, runner)
        assert session.monitor_rules == [
            MonitorRule(output="eDP-1", fields={"mode": "1920x1080@60", "vrr": 1})
        ]
        assert b"1920x1080@144" not in module_bytes(tmp_path, MONITORS_MODULE)

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))


@pytest.mark.parametrize("failure", list(FAILURES))
def test_the_countdowns_revert_after_a_failed_commit_lands_on_the_accepted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """The Revert ending is sound today: it rewrites the snapshot's breaking fields, which
    puts back what the failure left, and the step beneath is still the one to undo."""
    recorded: list[object] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session, snapshot, group, accepted, below = await failed_inside_a_countdown(
            fake, tmp_path, runner, monkeypatch, failure
        )
        session.on_recorded = recorded.append

        # `_revert_display`: the countdown expires or the user presses Revert.
        session.restore_monitor_rules(revert_breaking(snapshot.monitors, session.monitor_rules))
        session.end_undo_group(group, title=DISPLAY_CHANGED)
        await settle(session, runner)

        assert session.monitor_rules == [ACCEPTED]
        assert module_bytes(tmp_path, MONITORS_MODULE) == accepted
        assert not session.recovery_halted
        assert session.last_gesture is below
        assert recorded == []
        assert session.undo()
        await settle(session, runner)
        assert session.monitor_rules == []

    run_with_fake(scenario, FakeHyprland(conversation(), reload_emits_event=True))
