"""The session's declarative Entity seam, headless (#70).

Driven with a stub applier, like the monitor seam and for the same reason: what is under
test is the refuse-mutate-commit envelope and the identity rules, not the Apply pipeline.
Every accepted edit asserts a commit, because an edit the app accepted and never wrote is
the one failure instant apply cannot forgive.

The identity rules are the substance. Four of the seven kinds are keyed by something
Hyprland itself merges on -- a curve's name, an animation's leaf, a device's name, a
variable's name -- so a list holding two of one identity is a list describing a config the
compositor will not produce. The other three take duplicates, and must keep taking them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hyprtweaker.engine.ipc import Instance, NoInstance
from hyprtweaker.engine.model.entities import (
    Animation,
    Curve,
    Device,
    EnvVar,
    Gesture,
    Permission,
    StartupCommand,
)
from hyprtweaker.engine.paths import ConfigPaths
from hyprtweaker.session import Session

APP_VERSION = "0.0.0-test"


class StubApplier:
    def __init__(self) -> None:
        self.commits = 0

    def commit_entities(self) -> None:
        self.commits += 1


def live_session(tmp_path: Path) -> tuple[Session, StubApplier]:
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


SAMPLES = {
    "curves": Curve("easy", {"type": "bezier", "points": [[0.2, 1], [0.3, 1]]}),
    "animations": Animation("windowsIn", {"enabled": True, "bezier": "easy"}),
    "gestures": Gesture({"fingers": 3, "direction": "horizontal", "action": "workspace"}),
    "devices": Device("epic-mouse-v1", {"sensitivity": -0.5}),
    "env": EnvVar("XCURSOR_SIZE", "24"),
    "permissions": Permission("/usr/bin/grim", "screencopy", "allow"),
    "startup": StartupCommand("waybar"),
}


class TestTheEnvelope:
    @pytest.mark.parametrize("kind", sorted(SAMPLES))
    def test_every_kind_adds_and_writes(self, kind: str, tmp_path: Path) -> None:
        session, applier = live_session(tmp_path)

        assert session.add_declaration(kind, SAMPLES[kind])

        assert len(session.declarations(kind)) == 1
        assert applier.commits == 1

    @pytest.mark.parametrize("kind", sorted(SAMPLES))
    def test_a_read_only_session_refuses_and_writes_nothing(
        self, kind: str, tmp_path: Path
    ) -> None:
        session = read_only_session(tmp_path)

        assert not session.add_declaration(kind, SAMPLES[kind])
        assert session.declarations(kind) == []

    def test_an_unknown_kind_is_a_programming_error_not_a_silent_no_op(
        self, tmp_path: Path
    ) -> None:
        session, _ = live_session(tmp_path)

        with pytest.raises(ValueError, match="unknown declaration kind"):
            session.declarations("bezier")

    def test_replace_keeps_the_position(self, tmp_path: Path) -> None:
        session, applier = live_session(tmp_path)
        session.add_declaration("startup", StartupCommand("a"))
        session.add_declaration("startup", StartupCommand("b"))

        assert session.replace_declaration("startup", 0, StartupCommand("c"))

        assert [item.command for item in session.declarations("startup")] == ["c", "b"]
        assert applier.commits == 3

    def test_remove_takes_the_addressed_one(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        for name in ("a", "b", "c"):
            session.add_declaration("startup", StartupCommand(name))

        assert session.remove_declaration("startup", 1)

        assert [item.command for item in session.declarations("startup")] == ["a", "c"]

    def test_an_index_off_the_end_writes_nothing_of_substance(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("startup", StartupCommand("a"))

        session.remove_declaration("startup", 9)

        assert [item.command for item in session.declarations("startup")] == ["a"]


class TestIdentity:
    def test_a_second_curve_of_the_same_name_is_refused(self, tmp_path: Path) -> None:
        """`hl.curve` overwrites by name, so two rows would describe one curve."""
        session, applier = live_session(tmp_path)
        session.add_declaration("curves", Curve("easy", {"type": "bezier"}))

        assert not session.add_declaration("curves", Curve("easy", {"type": "spring"}))

        assert len(session.curves) == 1
        assert applier.commits == 1

    def test_a_second_animation_for_the_same_leaf_is_refused(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("animations", Animation("fade", {"enabled": True}))

        assert not session.add_declaration("animations", Animation("fade", {"enabled": False}))

        assert len(session.declarations("animations")) == 1

    def test_a_second_device_of_the_same_name_is_refused(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("devices", Device("mouse", {"sensitivity": 1}))

        assert not session.add_declaration("devices", Device("mouse", {"left_handed": True}))

    def test_a_second_variable_of_the_same_name_is_refused(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("env", EnvVar("XCURSOR_SIZE", "24"))

        assert not session.add_declaration("env", EnvVar("XCURSOR_SIZE", "32"))

    def test_editing_a_row_may_keep_its_own_identity(self, tmp_path: Path) -> None:
        """The obvious way to get this wrong: refusing a save because the row already is it."""
        session, _ = live_session(tmp_path)
        session.add_declaration("curves", Curve("easy", {"type": "bezier"}))

        assert session.replace_declaration("curves", 0, Curve("easy", {"type": "spring"}))

        assert session.curves[0].spec["type"] == "spring"

    def test_editing_a_row_onto_another_rows_identity_is_refused(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("curves", Curve("a", {"type": "bezier"}))
        session.add_declaration("curves", Curve("b", {"type": "bezier"}))

        assert not session.replace_declaration("curves", 1, Curve("a", {"type": "spring"}))

        assert [curve.name for curve in session.curves] == ["a", "b"]

    @pytest.mark.parametrize("kind", ["gestures", "permissions", "startup"])
    def test_the_unkeyed_kinds_keep_taking_duplicates(self, kind: str, tmp_path: Path) -> None:
        """Two identical autostart commands run twice, which is a thing people mean."""
        session, _ = live_session(tmp_path)

        assert session.add_declaration(kind, SAMPLES[kind])
        assert session.add_declaration(kind, SAMPLES[kind])

        assert len(session.declarations(kind)) == 2


class TestDeviceOverrideBadges:
    def test_a_device_override_names_the_option_it_shadows(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        session.add_declaration("devices", Device("epic-mouse-v1", {"sensitivity": -0.5}))

        assert session.device_overrides["input:sensitivity"] == ("epic-mouse-v1",)

    def test_removing_the_device_takes_the_badge_with_it(self, tmp_path: Path) -> None:
        """Derived rather than cached, so a stale badge cannot outlive its device."""
        session, _ = live_session(tmp_path)
        session.add_declaration("devices", Device("epic-mouse-v1", {"sensitivity": -0.5}))

        session.remove_declaration("devices", 0)

        assert session.device_overrides == {}

    def test_no_devices_badges_nothing(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)

        assert session.device_overrides == {}


class TestNamedAccessors:
    def test_curves_are_reachable_by_name_because_the_editor_needs_them(
        self, tmp_path: Path
    ) -> None:
        """The one kind with a caller that is not its own Page: the curve picker.

        Every other kind goes through `declarations(kind)`; a property apiece would be
        seven more names for what one parameterised call already answers.
        """
        session, _ = live_session(tmp_path)
        session.add_declaration("curves", SAMPLES["curves"])

        assert [curve.name for curve in session.curves] == ["easy"]

    def test_the_generic_accessor_answers_for_every_kind(self, tmp_path: Path) -> None:
        session, _ = live_session(tmp_path)
        for kind, entity in SAMPLES.items():
            session.add_declaration(kind, entity)

        assert all(session.declarations(kind) for kind in SAMPLES)
