"""Entity undo (#189), from outside the Session: when an entity gesture becomes a step.

The verdict decides, as for Options (ADR-0016): an entity commit is asynchronous, so a step
exists only once the transaction that carried it has come back ok. `SettlingApplier` holds
every commit until the test says `settle()`, which is what makes "not yet" observable.

The compositor-backed half -- the rendered file after an undo, and Option and entity steps on
one stack -- is in `test_session_undo.py`, beside the Option tests it interleaves with.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from _support import entity_session

from hyprtweaker.engine.apply import EntityStep, Step
from hyprtweaker.engine.model import (
    Bind,
    Curve,
    DispatcherCall,
    EnvVar,
    MonitorRule,
    WindowRule,
    WorkspaceRule,
)
from hyprtweaker.session import Session

DISPLAYS = frozenset({"monitors", "workspace_rules"})


def bind(keys: str) -> Bind:
    return Bind(keys=keys, dispatcher=DispatcherCall(path="exec_cmd", positional=("foot",)))


def rule(app: str) -> WindowRule:
    return WindowRule(match={"class": app}, effects={"float": True})


def keys(session: Session) -> list[str]:
    return [each.keys for each in session.model.entities.binds]


def entity_top(session: Session) -> EntityStep:
    step = session.last_gesture
    assert isinstance(step, EntityStep)
    return step


# --- one gesture, one step, once it stands ---------------------------------------------------

Gesture = Callable[[Session], bool]

SITES: dict[str, tuple[Gesture, Gesture, str]] = {
    # site: (setup, the gesture, the toast title it records)
    "edit_binds": (
        lambda s: s.add_bind(bind("SUPER + A")) and s.add_bind(bind("SUPER + B")),
        lambda s: s.remove_bind(0),
        "Keybind removed",
    ),
    "save_submap": (
        lambda s: True,
        lambda s: s.save_submap(original=None, name="resize", reset_target=""),
        "Submap added",
    ),
    "edit_rules": (
        lambda s: s.add_rule("window", rule("foot")) and s.add_rule("window", rule("mpv")),
        lambda s: s.move_rule("window", 1, 0),
        "Window rules reordered",
    ),
    "_commit_entity_edit, monitor rules": (
        lambda s: s.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"}),
        lambda s: s.patch_monitor_rule("eDP-1", {"vrr": 1}),
        "Display changed",
    ),
    "_commit_entity_edit, workspace rules": (
        lambda s: True,
        lambda s: s.save_workspace_rule(WorkspaceRule(workspace="1", fields={"gapsout": 8})),
        "Workspace rule added",
    ),
    "_commit_entity_edit, declarations": (
        lambda s: True,
        lambda s: s.add_declaration("curves", Curve("easy", {"type": "bezier"})),
        "Curve added",
    ),
}


@pytest.mark.parametrize("site", SITES)
def test_each_commit_site_records_one_step_once_its_transaction_stands(
    tmp_path: Path, site: str
) -> None:
    setup, gesture, title = SITES[site]
    session, applier = entity_session(tmp_path)
    assert setup(session)
    applier.settle()
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    assert gesture(session)
    assert recorded == [], "recorded before the verdict landed"
    applier.settle()

    assert len(recorded) == 1
    assert recorded[0] is session.last_gesture
    assert entity_top(session).title == title


@pytest.mark.parametrize("site", SITES)
def test_a_failed_commit_records_no_step(tmp_path: Path, site: str) -> None:
    """ADR-0016: a gesture Hyprland rejected never reaches the stack."""
    setup, gesture, _title = SITES[site]
    session, applier = entity_session(tmp_path)
    assert setup(session)
    applier.settle()
    below = session.last_gesture
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    assert gesture(session)
    applier.settle("config-errors")

    assert recorded == []
    assert session.last_gesture is below


@pytest.mark.parametrize("site", SITES)
def test_a_refused_read_only_edit_records_no_step(tmp_path: Path, site: str) -> None:
    _setup, gesture, _title = SITES[site]
    session, applier = entity_session(tmp_path)
    session.set_read_only("Hyprland is no longer running")

    assert not gesture(session)
    applier.settle()

    assert session.last_gesture is None


def test_an_edit_that_moved_nothing_spends_no_ctrl_z(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    session.add_bind(bind("SUPER + A"))
    applier.settle()
    added = session.last_gesture

    session.remove_bind(7)
    session.move_rule("window", 0, 0)
    applier.settle()

    assert session.last_gesture is added


def test_toast_titles_name_the_kind_and_what_happened(tmp_path: Path) -> None:
    """The open call, built as recommended: `<Kind> added/removed/changed/enabled/disabled`,
    `<Kinds> reordered`, in the words of the Page the user is on."""
    session, applier = entity_session(tmp_path)
    titles: list[str] = []

    def record(step: Step) -> None:
        assert isinstance(step, EntityStep)
        titles.append(step.title)

    session.on_recorded = record
    gestures: list[Gesture] = [
        lambda s: s.add_bind(bind("SUPER + A")),
        lambda s: s.add_bind(bind("SUPER + B")),
        lambda s: s.replace_bind(0, bind("SUPER + C")),
        lambda s: s.set_bind_enabled(0, False),
        lambda s: s.set_bind_enabled(0, True),
        lambda s: s.swap_binds(0, 1),
        lambda s: s.add_rule("layer", rule("waybar")),
        lambda s: s.set_rule_enabled("layer", 0, False),
        lambda s: s.remove_rule("layer", 0),
        lambda s: s.save_submap(original=None, name="resize", reset_target=""),
        lambda s: s.save_submap(original="resize", name="move", reset_target=""),
        lambda s: s.patch_monitor_rule("eDP-1", {"mode": "preferred"}),
        lambda s: s.rename_monitor_rule("eDP-1", "desc:BOE"),
        lambda s: s.remove_monitor_rule("desc:BOE"),
        lambda s: s.save_workspace_rule(WorkspaceRule(workspace="2")),
        lambda s: s.save_workspace_rule(
            WorkspaceRule(workspace="2", fields={"gapsout": 4}), original="2"
        ),
        lambda s: s.remove_workspace_rule("2"),
        lambda s: s.add_declaration("env", EnvVar("A", "1")),
        lambda s: s.remove_declaration("env", 0),
        lambda s: s.edit_binds(lambda binds: binds.clear()),
    ]
    for gesture in gestures:
        gesture(session)
        applier.settle()

    assert titles == [
        "Keybind added",
        "Keybind added",
        "Keybind changed",
        "Keybind disabled",
        "Keybind enabled",
        "Keybinds reordered",
        "Layer rule added",
        "Layer rule disabled",
        "Layer rule removed",
        "Submap added",
        "Submap changed",
        "Display changed",
        "Display changed",
        "Display removed",
        "Workspace rule added",
        "Workspace rule changed",
        "Workspace rule removed",
        "Variable added",
        "Variable removed",
        "Keybinds changed",
    ]


def test_commits_coalesced_into_one_transaction_are_one_step_each_in_order(
    tmp_path: Path,
) -> None:
    """Two gestures a real queue folded into one reload are still two things the user did;
    `on_recorded` fires once, with the newer."""
    session, applier = entity_session(tmp_path)
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    session.add_bind(bind("SUPER + A"))
    session.add_rule("window", rule("foot"))
    applier.settle()

    assert [step.title for step in recorded if isinstance(step, EntityStep)] == [
        "Window rule added"
    ]
    assert session.undo()
    assert entity_top(session).title == "Keybind added"


# --- undo -------------------------------------------------------------------------------------


def test_undo_puts_the_list_back_and_writes_it_without_recording(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    for each in ("SUPER + A", "SUPER + B", "SUPER + C"):
        session.add_bind(bind(each))
        applier.settle()
    session.remove_bind(1)
    applier.settle()
    commits = applier.serial

    assert session.undo()

    assert keys(session) == ["SUPER + A", "SUPER + B", "SUPER + C"]
    assert applier.serial == commits + 1, "the undo did not go through commit_entities"
    applier.settle()
    assert entity_top(session).title == "Keybind added", "the undo recorded a step of its own"


def test_undo_refuses_a_step_whose_list_changed_off_the_stack(tmp_path: Path) -> None:
    """S1.4: replaying a whole list over a change the stack never saw would erase it."""
    session, applier = entity_session(tmp_path)
    session.add_bind(bind("SUPER + A"))
    applier.settle()
    session.add_bind(bind("SUPER + B"))
    applier.settle()
    session._model.entities.binds.append(bind("SUPER + HAND"))

    assert not session.undo()

    assert keys(session) == ["SUPER + A", "SUPER + B", "SUPER + HAND"]
    assert applier.serial == 2, "a refused undo wrote something"
    assert entity_top(session).title == "Keybind added", "the stale step is still on top"


def test_undo_during_an_edit_in_flight_waits_for_it_and_drops_nothing(tmp_path: Path) -> None:
    """Review of #151, finding 12: Ctrl+Z pressed while a removal is still being written
    used to pop the step beneath it, find it stale and drop it. The removal is the gesture
    the user means; it is undone once it lands, and the step beneath survives."""
    session, applier = entity_session(tmp_path)
    for each in ("SUPER + A", "SUPER + B"):
        session.add_bind(bind(each))
        applier.settle()
    session.remove_bind(0)

    assert not session.undo()
    assert session.undo_queued
    assert entity_top(session).title == "Keybind added", "the step under the edit was dropped"
    assert applier.serial == 3, "the waiting undo wrote something"

    applier.settle()
    assert keys(session) == ["SUPER + A", "SUPER + B"], "the removal was not undone"
    assert not session.undo_queued
    applier.settle()
    assert entity_top(session).title == "Keybind added"
    assert session.undo()
    assert keys(session) == ["SUPER + A"]


def test_a_waiting_undo_is_dropped_when_its_edit_fails(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    for each in ("SUPER + A", "SUPER + B"):
        session.add_bind(bind(each))
        applier.settle()
    session.remove_bind(0)
    assert not session.undo()

    applier.settle("config-errors")

    assert not session.undo_queued
    assert keys(session) == ["SUPER + B"], "an undo ran for a gesture that never stood"
    assert entity_top(session).title == "Keybind added"


def test_undo_never_drops_a_step_an_open_group_holds_edits_over(tmp_path: Path) -> None:
    """Finding 12's second reproduction: scale 1 to 2 kept, then a countdown holding 2 to 3.
    Undoing the kept step would find its list moved and drop it; the window turns Ctrl+Z
    into Revert there, and the session refuses without touching the stack."""
    session, applier = entity_session(tmp_path)
    kept = session.begin_undo_group(DISPLAYS)
    session.patch_monitor_rule("eDP-1", {"scale": 2})
    applier.settle()
    session.end_undo_group(kept, title="Display changed")
    top = session.last_gesture
    session.begin_undo_group(DISPLAYS)
    session.patch_monitor_rule("eDP-1", {"scale": 3})
    applier.settle()

    assert not session.undo()

    assert session.last_gesture is top
    assert not session.undo_queued, "a countdown is not waited out"
    assert [rule.fields for rule in session.monitor_rules] == [{"scale": 3}]


# --- lists changed off the stack --------------------------------------------------------------


def test_profile_activation_records_no_step_and_forgets_display_steps(
    tmp_path: Path,
) -> None:
    """The one commit site that records nothing (S1.5): the active-profile pointer lives
    outside the model, and the activation's own countdown is its take-back (ADR-0015)."""
    session, applier = entity_session(tmp_path)
    session.add_bind(bind("SUPER + A"))
    applier.settle()
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    slug = session.save_monitor_profile("Docked")
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@48"})
    applier.settle()
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    assert session.activate_monitor_profile(slug)
    applier.settle()

    assert recorded == []
    assert entity_top(session).title == "Keybind added"


# --- undo groups ------------------------------------------------------------------------------


def test_a_reverted_group_leaves_no_step(tmp_path: Path) -> None:
    """AC 4: the edit never stood, so there is nothing to undo -- not even the revert."""
    session, applier = entity_session(tmp_path)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    applier.settle()
    before = session.last_gesture
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    group = session.begin_undo_group(DISPLAYS)
    snapshot = session.monitor_state_snapshot().monitors
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@144"})
    applier.settle()
    session.restore_monitor_rules(snapshot)
    session.end_undo_group(group, title="Display changed")
    applier.settle()

    assert recorded == []
    assert session.last_gesture is before


def test_a_kept_group_is_one_step_from_before_the_first_edit(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)
    recorded: list[Step] = []
    session.on_recorded = recorded.append

    group = session.begin_undo_group(DISPLAYS)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    applier.settle()
    session.patch_monitor_rule("eDP-1", {"scale": 2})
    session.add_bind(bind("SUPER + A"))
    session.end_undo_group(group, title="Display changed")
    assert [getattr(step, "title", None) for step in recorded] == []
    applier.settle()

    # One toast for the transaction, naming the newest step: the merge, which lands
    # after the bind that was committed beside it.
    assert [getattr(step, "title", None) for step in recorded] == ["Display changed"]
    assert session.undo()
    assert session.monitor_rules == []
    assert keys(session) == ["SUPER + A"]
    assert entity_top(session).title == "Keybind added"


def test_a_group_with_a_failed_commit_records_nothing(tmp_path: Path) -> None:
    session, applier = entity_session(tmp_path)

    group = session.begin_undo_group(DISPLAYS)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60"})
    applier.settle("config-errors")
    session.patch_monitor_rule("eDP-1", {"scale": 2})
    applier.settle()
    session.end_undo_group(group, title="Display changed")

    assert session.last_gesture is None


def test_only_one_undo_group_at_a_time(tmp_path: Path) -> None:
    session, _applier = entity_session(tmp_path)
    session.begin_undo_group(DISPLAYS)

    with pytest.raises(RuntimeError):
        session.begin_undo_group(frozenset({"binds"}))


def test_a_bare_list_edit_is_titled_by_its_kind(tmp_path: Path) -> None:
    """`edit_*` without a title of its own -- a caller with no better word for its gesture."""
    session, applier = entity_session(tmp_path)
    session.edit_monitor_rules(lambda rules: rules.append(MonitorRule(output="DP-1")))
    applier.settle()

    assert entity_top(session).title == "Displays changed"
