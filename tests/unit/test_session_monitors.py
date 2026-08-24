"""The session's monitor and workspace rule seam, headless (#68).

Driven with a stub applier rather than a scripted socket: what is under test is the CRUD
envelope -- refuse-mutate-commit, identity addressing, snapshot/restore -- not the Apply
pipeline, which has its own tiers. The stub records commits, so every test also asserts
the one thing instant apply cannot forgive: an accepted edit that never wrote.
"""

from __future__ import annotations

from pathlib import Path

from hyprtweaker.engine.ipc import Instance, NoInstance
from hyprtweaker.engine.model.entities import MonitorRule, WorkspaceRule
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.session import Session

APP_VERSION = "0.0.0-test"


class StubApplier:
    def __init__(self) -> None:
        self.commits = 0

    def commit_entities(self) -> None:
        self.commits += 1


def live_session(tmp_path: Path) -> tuple[Session, StubApplier]:
    """A session that accepts edits: the stub stands where `_go_live` puts the real one."""

    def no_compositor() -> Instance:
        raise NoInstance("headless")

    session = Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )
    applier = StubApplier()
    session._applier = applier  # type: ignore[assignment]
    session._offline_reason = None
    return session, applier


def read_only_session(tmp_path: Path) -> Session:
    def no_compositor() -> Instance:
        raise NoInstance("headless")

    return Session(
        spawn=lambda coro: coro.close(),
        paths=ConfigPaths.rooted_at(tmp_path),
        app_version=APP_VERSION,
        connect=no_compositor,
    )


class TestMonitorRules:
    def test_patch_creates_then_merges(self, tmp_path: Path) -> None:
        session, applier = live_session(tmp_path)

        assert session.patch_monitor_rule("desc:BOE", {"scale": 1.5})
        assert session.patch_monitor_rule("desc:BOE", {"mode": "1920x1080@60"})

        assert len(session.monitor_rules) == 1
        assert session.monitor_rules[0].fields == {"scale": 1.5, "mode": "1920x1080@60"}
        assert applier.commits == 2

    def test_snapshot_is_insulated_from_later_edits(self, tmp_path: Path) -> None:
        session, _applier = live_session(tmp_path)
        session.patch_monitor_rule("DP-1", {"scale": 2})

        snapshot = session.monitor_snapshot()
        session.patch_monitor_rule("DP-1", {"scale": 1})
        session.patch_monitor_rule("DP-2", {"mode": "preferred"})

        assert [rule.fields["scale"] for rule in snapshot] == [2]

    def test_restore_puts_the_previous_state_back(self, tmp_path: Path) -> None:
        """The acceptance criterion's second half: no confirmation restores the state."""
        session, applier = live_session(tmp_path)
        session.patch_monitor_rule("desc:BOE", {"scale": 1.5})

        snapshot = session.monitor_snapshot()
        session.patch_monitor_rule("desc:BOE", {"scale": 2, "transform": 1})
        assert session.monitor_rules[0].fields["scale"] == 2

        assert session.restore_monitor_rules(snapshot)
        assert len(session.monitor_rules) == 1
        assert session.monitor_rules[0].fields == {"scale": 1.5}
        assert applier.commits == 3  # the restore is a write like any other

    def test_rename_keeps_fields_and_position(self, tmp_path: Path) -> None:
        session, _applier = live_session(tmp_path)
        session.patch_monitor_rule("desc:BOE 0x0791", {"scale": 1.5})
        session.patch_monitor_rule("DP-3", {"mode": "preferred"})

        assert session.rename_monitor_rule("desc:BOE 0x0791", "eDP-1")
        assert [rule.output for rule in session.monitor_rules] == ["eDP-1", "DP-3"]
        assert session.monitor_rules[0].fields == {"scale": 1.5}

    def test_rename_onto_a_taken_identity_is_refused(self, tmp_path: Path) -> None:
        session, applier = live_session(tmp_path)
        session.patch_monitor_rule("desc:BOE", {"scale": 1.5})
        session.patch_monitor_rule("eDP-1", {"scale": 2})
        commits = applier.commits

        assert not session.rename_monitor_rule("desc:BOE", "eDP-1")
        assert applier.commits == commits  # a refusal never writes

    def test_remove_by_identity(self, tmp_path: Path) -> None:
        session, _applier = live_session(tmp_path)
        session.patch_monitor_rule("DP-1", {"scale": 2})
        session.patch_monitor_rule("", {"mode": "preferred"})

        assert session.remove_monitor_rule("DP-1")
        assert [rule.output for rule in session.monitor_rules] == [""]

    def test_read_only_refuses_and_leaves_the_model_alone(self, tmp_path: Path) -> None:
        session = read_only_session(tmp_path)

        assert not session.patch_monitor_rule("DP-1", {"scale": 2})
        assert not session.restore_monitor_rules((MonitorRule(output="DP-1"),))
        assert session.monitor_rules == []


class TestWorkspaceRules:
    def test_save_adds_then_replaces_by_original(self, tmp_path: Path) -> None:
        session, _applier = live_session(tmp_path)

        assert session.save_workspace_rule(
            WorkspaceRule(workspace="1", fields={"default": True})
        )
        assert session.save_workspace_rule(
            WorkspaceRule(workspace="1", fields={"persistent": True}), original="1"
        )

        assert len(session.workspace_rules) == 1
        assert session.workspace_rules[0].fields == {"persistent": True}

    def test_one_rule_per_selector_is_enforced(self, tmp_path: Path) -> None:
        """ADR-0008: Hyprland merges duplicates, so a second rule would be a lie."""
        session, applier = live_session(tmp_path)
        session.save_workspace_rule(WorkspaceRule(workspace="special:scratch"))
        commits = applier.commits

        assert not session.save_workspace_rule(WorkspaceRule(workspace="special:scratch"))
        assert not session.save_workspace_rule(
            WorkspaceRule(workspace="special:scratch"), original="9"
        )
        assert len(session.workspace_rules) == 1
        assert applier.commits == commits

    def test_remove_by_selector(self, tmp_path: Path) -> None:
        session, _applier = live_session(tmp_path)
        session.save_workspace_rule(WorkspaceRule(workspace="1"))
        session.save_workspace_rule(WorkspaceRule(workspace="2"))

        assert session.remove_workspace_rule("1")
        assert [rule.workspace for rule in session.workspace_rules] == ["2"]


class TestHotplugWatch:
    def test_watchers_hear_the_hotplug(self, tmp_path: Path) -> None:
        session = read_only_session(tmp_path)
        heard: list[bool] = []
        unwatch = session.watch_monitors(lambda: heard.append(True))

        session._on_monitor_hotplug(None)
        assert heard == [True]

        unwatch()
        session._on_monitor_hotplug(None)
        assert heard == [True]

    def test_unwatch_twice_is_harmless(self, tmp_path: Path) -> None:
        session = read_only_session(tmp_path)
        unwatch = session.watch_monitors(lambda: None)
        unwatch()
        unwatch()


CONNECTED = (
    {"name": "eDP-1", "description": "BOE 0x0791"},
    {"name": "DP-3", "description": "Dell U2720Q"},
)


def docked_session(tmp_path: Path) -> tuple[Session, StubApplier, str]:
    """A live session holding a docked setup, with that setup saved as a profile."""
    session, applier = live_session(tmp_path)
    session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@60", "position": "0x0"})
    session.patch_monitor_rule("desc:Dell U2720Q", {"position": "1920x0"})
    session.save_workspace_rule(
        WorkspaceRule(workspace="1", fields={"monitor": "desc:Dell U2720Q", "default": True})
    )
    session.save_workspace_rule(WorkspaceRule(workspace="2", fields={"gapsout": 8}))
    slug = session.save_monitor_profile("Docked", CONNECTED)
    return session, applier, slug


class TestMonitorProfiles:
    def test_capture_then_activate_round_trips_exactly(self, tmp_path: Path) -> None:
        """The #69 acceptance golden: the rendered Modules come back byte for byte."""
        from hyprtweaker.engine.writer.monitors import (
            render_monitors_module,
            render_workspace_rules_module,
        )

        session, _applier, slug = docked_session(tmp_path)
        before = (
            render_monitors_module(session.monitor_rules, app_version=APP_VERSION),
            render_workspace_rules_module(session.workspace_rules, app_version=APP_VERSION),
        )

        # Undock by hand: rules change, a pin is dropped, a foreign pin appears.
        session.remove_monitor_rule("desc:Dell U2720Q")
        session.patch_monitor_rule("eDP-1", {"mode": "1920x1080@48"})
        session.save_workspace_rule(
            WorkspaceRule(workspace="1", fields={"monitor": "eDP-1", "default": True}),
            original="1",
        )
        session.save_workspace_rule(
            WorkspaceRule(workspace="2", fields={"gapsout": 8, "monitor": "eDP-1"}),
            original="2",
        )

        assert session.activate_monitor_profile(slug)
        after = (
            render_monitors_module(session.monitor_rules, app_version=APP_VERSION),
            render_workspace_rules_module(session.workspace_rules, app_version=APP_VERSION),
        )
        assert after == before

    def test_activation_is_one_transaction(self, tmp_path: Path) -> None:
        session, applier, slug = docked_session(tmp_path)
        session.remove_monitor_rule("desc:Dell U2720Q")
        commits = applier.commits

        assert session.activate_monitor_profile(slug)
        assert applier.commits == commits + 1

    def test_activation_sets_the_pointer_and_clears_drift(self, tmp_path: Path) -> None:
        session, _applier, slug = docked_session(tmp_path)
        assert session.active_monitor_profile() is None

        assert session.activate_monitor_profile(slug)
        active = session.active_monitor_profile()
        assert active is not None and active[0] == slug
        assert not session.monitor_profile_drift()

    def test_an_edit_after_activation_drifts(self, tmp_path: Path) -> None:
        session, _applier, slug = docked_session(tmp_path)
        session.activate_monitor_profile(slug)

        session.patch_monitor_rule("eDP-1", {"transform": 1})
        assert session.monitor_profile_drift()

    def test_update_recaptures_and_clears_drift(self, tmp_path: Path) -> None:
        session, _applier, slug = docked_session(tmp_path)
        session.activate_monitor_profile(slug)
        session.patch_monitor_rule("eDP-1", {"transform": 1})

        assert session.update_monitor_profile(slug, CONNECTED)
        assert not session.monitor_profile_drift()
        assert not session.update_monitor_profile("ghost", CONNECTED)

    def test_detach_forgets_the_pointer_and_keeps_the_config(self, tmp_path: Path) -> None:
        session, applier, slug = docked_session(tmp_path)
        session.activate_monitor_profile(slug)
        commits = applier.commits

        session.detach_monitor_profile()
        assert session.active_monitor_profile() is None
        assert not session.monitor_profile_drift()
        assert applier.commits == commits

    def test_delete_of_the_active_profile_clears_the_pointer(self, tmp_path: Path) -> None:
        session, _applier, slug = docked_session(tmp_path)
        session.activate_monitor_profile(slug)

        session.delete_monitor_profile(slug)
        assert session.monitor_profiles() == ()
        assert session.active_monitor_profile() is None

    def test_read_only_refuses_activation_but_allows_capture(self, tmp_path: Path) -> None:
        _live, _applier, _slug = docked_session(tmp_path)
        readonly = read_only_session(tmp_path)
        slug = readonly.save_monitor_profile("Before experimenting", CONNECTED)
        assert readonly._profile_store.load(slug) is not None

        assert not readonly.activate_monitor_profile(slug)
        assert readonly.active_monitor_profile() is None

    def test_activating_a_ghost_is_refused(self, tmp_path: Path) -> None:
        session, applier, _slug = docked_session(tmp_path)
        commits = applier.commits
        assert not session.activate_monitor_profile("ghost")
        assert applier.commits == commits

    def test_matching_offers_only_a_profile_that_would_change_something(
        self, tmp_path: Path
    ) -> None:
        session, _applier, slug = docked_session(tmp_path)
        # The setup already equals the capture, so there is nothing to offer.
        assert session.matching_monitor_profile(CONNECTED) is None

        session.patch_monitor_rule("eDP-1", {"transform": 1})
        match = session.matching_monitor_profile(CONNECTED)
        assert match is not None and match[0] == slug

        # A different connected set never matches, however far the config drifts.
        assert session.matching_monitor_profile(CONNECTED[:1]) is None

    def test_restore_puts_lists_and_pointer_back_in_one_transaction(
        self, tmp_path: Path
    ) -> None:
        session, applier, slug = docked_session(tmp_path)
        snapshot = session.monitor_state_snapshot()

        session.activate_monitor_profile(slug)
        session.remove_monitor_rule("eDP-1")
        commits = applier.commits

        assert session.restore_monitor_state(snapshot)
        assert applier.commits == commits + 1
        assert session.monitor_rules == list(snapshot[0])
        assert session.workspace_rules == list(snapshot[1])
        assert session.active_monitor_profile() is None
