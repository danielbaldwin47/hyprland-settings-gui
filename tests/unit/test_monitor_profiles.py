"""Monitor profiles, headless: capture, activation, matching, drift, the store (#69).

Everything ADR-0015 promises that no GTK is needed to prove. Activation is a pure
function here -- the session tier (`test_session_monitors.py`) proves it rides one
transaction; this file proves the function computes the right state, and that the store
round-trips it byte-honestly.
"""

from __future__ import annotations

from pathlib import Path

from hyprtweaker.engine.model.entities import MonitorRule, WorkspaceRule
from hyprtweaker.engine.profiles import (
    ACTIVE_NAME,
    ConnectedOutput,
    MonitorProfile,
    ProfileStore,
    activated,
    capture,
    connected_outputs,
    drift,
    matches,
    slugify,
)

LAPTOP = MonitorRule(output="eDP-1", fields={"mode": "1920x1080@60", "position": "0x0"})
DOCK = MonitorRule(
    output="desc:Dell U2720Q", fields={"mode": "2560x1440@60", "position": "1920x0"}
)
CATCH_ALL = MonitorRule(output="", fields={"mode": "preferred"})


def docked_profile(**overrides: object) -> MonitorProfile:
    base = dict(
        name="Docked",
        monitors=(LAPTOP, DOCK, CATCH_ALL),
        pins={"1": "desc:Dell U2720Q", "2": None},
        connected=(
            ConnectedOutput(name="eDP-1", description="BOE 0x0791"),
            ConnectedOutput(name="DP-3", description="Dell U2720Q"),
        ),
    )
    base.update(overrides)
    return MonitorProfile(**base)  # type: ignore[arg-type]


class TestSlugify:
    def test_lowers_and_dashes(self) -> None:
        assert slugify("Docked, at home") == "docked-at-home"

    def test_never_empty(self) -> None:
        assert slugify("...") == "profile"


class TestCapture:
    def test_captures_the_whole_rule_set_with_origins_stripped(self) -> None:
        rules = [
            MonitorRule(output="eDP-1", fields={"mode": "preferred"}, origin="monitors.lua:3"),
            MonitorRule(output="", fields={}, origin="monitors.lua:4"),
        ]
        profile = capture("Docked", monitors=rules, workspace_rules=[])
        assert profile.monitors == (
            MonitorRule(output="eDP-1", fields={"mode": "preferred"}),
            MonitorRule(output="", fields={}),
        )

    def test_every_workspace_rule_contributes_a_pin(self) -> None:
        rules = [
            WorkspaceRule(workspace="1", fields={"monitor": "DP-3", "default": True}),
            WorkspaceRule(workspace="special:mail", fields={"gapsout": 20}),
        ]
        profile = capture("Docked", monitors=[], workspace_rules=rules)
        assert profile.pins == {"1": "DP-3", "special:mail": None}

    def test_records_the_connected_fingerprint(self) -> None:
        connected = connected_outputs(
            [{"name": "eDP-1", "description": " BOE 0x0791 "}, {"name": "DP-3"}]
        )
        profile = capture("Docked", monitors=[], workspace_rules=[], connected=connected)
        assert profile.connected == (
            ConnectedOutput(name="eDP-1", description="BOE 0x0791"),
            ConnectedOutput(name="DP-3", description=""),
        )


class TestActivated:
    def test_replaces_the_monitor_list_wholesale(self) -> None:
        monitors, _ = activated(docked_profile(), workspace_rules=[])
        assert monitors == (LAPTOP, DOCK, CATCH_ALL)

    def test_sets_and_clears_pins_by_selector(self) -> None:
        current = [
            WorkspaceRule(workspace="1", fields={"default": True}),
            WorkspaceRule(workspace="2", fields={"monitor": "HDMI-A-1", "gapsout": 8}),
        ]
        _, patched = activated(docked_profile(), workspace_rules=current)
        assert patched == (
            WorkspaceRule(
                workspace="1", fields={"default": True, "monitor": "desc:Dell U2720Q"}
            ),
            WorkspaceRule(workspace="2", fields={"gapsout": 8}),
        )

    def test_leaves_selectors_the_profile_never_saw(self) -> None:
        current = [WorkspaceRule(workspace="9", fields={"monitor": "eDP-1"})]
        _, patched = activated(docked_profile(), workspace_rules=current)
        assert patched == tuple(current)

    def test_never_creates_a_workspace_rule(self) -> None:
        # The profile pins "1", but no rule for "1" exists any more: a pin is an overlay
        # patch, not a rule (ADR-0015).
        _, patched = activated(docked_profile(), workspace_rules=[])
        assert patched == ()


class TestMatches:
    def test_order_insensitive(self) -> None:
        live = (
            ConnectedOutput(name="DP-3", description="Dell U2720Q"),
            ConnectedOutput(name="eDP-1", description="BOE 0x0791"),
        )
        assert matches(docked_profile(), live)

    def test_a_different_set_does_not_match(self) -> None:
        live = (ConnectedOutput(name="eDP-1", description="BOE 0x0791"),)
        assert not matches(docked_profile(), live)

    def test_a_fingerprintless_profile_never_matches(self) -> None:
        assert not matches(docked_profile(connected=()), ())


class TestDrift:
    def test_no_drift_right_after_activation(self) -> None:
        current = [
            WorkspaceRule(workspace="1", fields={}),
            WorkspaceRule(workspace="2", fields={}),
        ]
        monitors, workspaces = activated(docked_profile(), workspace_rules=current)
        assert not drift(docked_profile(), monitors=monitors, workspace_rules=workspaces)

    def test_a_changed_field_drifts(self) -> None:
        monitors, workspaces = activated(docked_profile(), workspace_rules=[])
        edited = list(monitors)
        edited[0] = MonitorRule(
            output="eDP-1", fields={"mode": "1920x1080@48", "position": "0x0"}
        )
        assert drift(docked_profile(), monitors=edited, workspace_rules=workspaces)

    def test_a_changed_pin_drifts(self) -> None:
        current = [WorkspaceRule(workspace="1", fields={"monitor": "eDP-1"})]
        monitors, _ = activated(docked_profile(), workspace_rules=[])
        assert drift(docked_profile(), monitors=monitors, workspace_rules=current)

    def test_origins_do_not_drift(self) -> None:
        monitors, _ = activated(docked_profile(), workspace_rules=[])
        read_back = [
            MonitorRule(output=rule.output, fields=dict(rule.fields), origin="monitors.lua:2")
            for rule in monitors
        ]
        assert not drift(docked_profile(), monitors=read_back, workspace_rules=[])

    def test_a_deleted_pinned_workspace_rule_does_not_drift(self) -> None:
        # Activation could not bring the rule back, so drifting here would badge a state
        # "Activate again" cannot fix.
        monitors, _ = activated(docked_profile(), workspace_rules=[])
        assert not drift(docked_profile(), monitors=monitors, workspace_rules=[])


class TestStore:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(docked_profile())
        assert slug == "docked"
        assert store.load(slug) == docked_profile()

    def test_save_never_overwrites_a_slug(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        first = store.save(docked_profile())
        second = store.save(docked_profile(name="docked"))
        assert (first, second) == ("docked", "docked-2")
        assert store.load(first) is not None and store.load(second) is not None

    def test_replace_updates_in_place(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(docked_profile())
        store.replace(slug, docked_profile(monitors=(LAPTOP,)))
        loaded = store.load(slug)
        assert loaded is not None and loaded.monitors == (LAPTOP,)

    def test_list_sorts_by_name_and_skips_broken_files(self, tmp_path: Path) -> None:
        directory = tmp_path / "monitor-profiles"
        store = ProfileStore(directory)
        store.save(docked_profile(name="Travel"))
        store.save(docked_profile(name="Docked"))
        (directory / "broken.json").write_text("{not json", encoding="utf-8")
        assert [name for _, profile in store.list() for name in [profile.name]] == [
            "Docked",
            "Travel",
        ]

    def test_load_missing_is_none(self, tmp_path: Path) -> None:
        assert ProfileStore(tmp_path / "monitor-profiles").load("ghost") is None

    def test_active_pointer_round_trips(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(docked_profile())
        assert store.active_slug() is None
        store.set_active(slug)
        assert store.active_slug() == slug
        store.set_active(None)
        assert store.active_slug() is None

    def test_a_pointer_at_a_ghost_reads_as_none(self, tmp_path: Path) -> None:
        directory = tmp_path / "monitor-profiles"
        directory.mkdir(parents=True)
        (directory / ACTIVE_NAME).write_text('{"slug": "ghost"}', encoding="utf-8")
        assert ProfileStore(directory).active_slug() is None

    def test_delete_takes_the_pointer_with_it(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(docked_profile())
        store.set_active(slug)
        store.delete(slug)
        assert store.load(slug) is None
        assert store.active_slug() is None

    def test_the_pointer_is_not_listed_as_a_profile(self, tmp_path: Path) -> None:
        store = ProfileStore(tmp_path / "monitor-profiles")
        slug = store.save(docked_profile())
        store.set_active(slug)
        assert [listed for listed, _ in store.list()] == [slug]
