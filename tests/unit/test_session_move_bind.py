"""`Session.move_bind`: the drag reorder of the Binds page (ADR-0007, #161).

Identity is position, and only order *within* a group reaches `binds.lua`: the Writer
emits root binds first, then one `hl.define_submap` block per submap. So a move is
refused across groups, and the round trip is compared group by group.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from _fake_hyprland import FakeHyprland, run_with_fake
from _support import (
    Runner,
    SettlingApplier,
    entity_session,
    section_conversation,
    session_for,
)

from hyprtweaker.engine.apply import EntityStep
from hyprtweaker.engine.model.entities import Bind, DispatcherCall
from hyprtweaker.engine.writer.binds import parse_binds_module
from hyprtweaker.session import Session


def run(command: str, keys: str, **kwargs: object) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall("exec_cmd", positional=(command,)),
        **kwargs,  # type: ignore[arg-type]
    )


# Root and submap binds interleave in the flat list, as an imported config can leave them:
# a move inside one group must step over the other group's binds untouched.
BINDS = [
    run("a", "SUPER + Q"),
    run("grow", "right", submap="resize"),
    run("b", "SUPER + Q"),
    run("w", "SUPER + W", enabled=False),
    run("shrink", "left", submap="resize"),
    run("e", "SUPER + E"),
]


def commands(session: Session) -> list[str]:
    return [bind.dispatcher.positional[0] for bind in session.model.entities.binds]  # type: ignore[union-attr]


def seeded(root: Path) -> tuple[Session, SettlingApplier]:
    session, applier = entity_session(root)
    assert session.edit_binds(lambda binds: binds.extend(BINDS))
    applier.settle()
    return session, applier


class TestMoveBind:
    def test_forward_puts_the_bind_where_the_target_was(self, tmp_path: Path) -> None:
        session, _ = seeded(tmp_path)

        assert session.move_bind(0, 5) is True

        assert commands(session) == ["grow", "b", "w", "shrink", "e", "a"]

    def test_backward_puts_the_bind_before_the_target(self, tmp_path: Path) -> None:
        session, _ = seeded(tmp_path)

        assert session.move_bind(5, 0) is True

        assert commands(session) == ["e", "a", "grow", "b", "w", "shrink"]

    def test_within_a_submap(self, tmp_path: Path) -> None:
        session, _ = seeded(tmp_path)

        assert session.move_bind(4, 1) is True

        assert commands(session) == ["a", "shrink", "grow", "b", "w", "e"]

    @pytest.mark.parametrize(
        ("index", "to"),
        [(2, 2), (6, 0), (0, 6), (-1, 0), (0, -1)],
        ids=[
            "no-op",
            "origin-past-end",
            "target-past-end",
            "negative-origin",
            "negative-target",
        ],
    )
    def test_a_move_that_goes_nowhere_writes_nothing(
        self, tmp_path: Path, index: int, to: int
    ) -> None:
        session, applier = seeded(tmp_path)
        before = session.last_gesture

        assert session.move_bind(index, to) is False
        applier.settle()

        assert commands(session) == ["a", "grow", "b", "w", "shrink", "e"]
        assert applier.serial == 1, "only the seeding wrote"
        assert session.last_gesture is before

    @pytest.mark.parametrize(
        ("index", "to"),
        [(0, 1), (1, 0), (4, 5)],
        ids=["root-onto-submap", "submap-onto-root", "submap-onto-root-later"],
    )
    def test_a_move_across_groups_is_refused(self, tmp_path: Path, index: int, to: int) -> None:
        session, applier = seeded(tmp_path)

        assert session.move_bind(index, to) is False

        assert commands(session) == ["a", "grow", "b", "w", "shrink", "e"]
        assert applier.serial == 1

    def test_a_move_between_two_submaps_is_refused(self, tmp_path: Path) -> None:
        session, _ = entity_session(tmp_path)
        assert session.edit_binds(
            lambda binds: binds.extend(
                [run("x", "right", submap="resize"), run("y", "left", submap="move")]
            )
        )

        assert session.move_bind(0, 1) is False
        assert commands(session) == ["x", "y"]

    def test_a_stored_unloadable_bind_still_moves(self, tmp_path: Path) -> None:
        """A move changes order, not loadability: #199's refusal is for enabling."""
        session, _ = entity_session(tmp_path)
        multi_key = run("m", "SUPER + A&B", enabled=False)
        assert session.edit_binds(
            lambda binds: binds.extend([run("a", "SUPER + Q"), multi_key])
        )

        assert session.move_bind(1, 0) is True

        assert commands(session) == ["m", "a"]

    def test_a_move_is_one_undo_step_that_restores_the_order(self, tmp_path: Path) -> None:
        session, applier = seeded(tmp_path)
        assert session.move_bind(0, 5)
        applier.settle()

        step = session.last_gesture
        assert isinstance(step, EntityStep)
        assert step.title == "Binds reordered"

        assert session.undo() is True
        assert commands(session) == ["a", "grow", "b", "w", "shrink", "e"]


def binds_lua(root: Path) -> str:
    return (root / "hypr" / "hyprtweaker" / "binds.lua").read_text(encoding="utf-8")


def by_group(binds: list[Bind] | tuple[Bind, ...]) -> dict[str | None, list[Bind]]:
    """Each group's binds in order, without `origin` (the file line a read stamps)."""
    groups: dict[str | None, list[Bind]] = {}
    for bind in binds:
        groups.setdefault(bind.submap, []).append(replace(bind, origin=""))
    return groups


def test_a_move_survives_the_writer_round_trip(tmp_path: Path) -> None:
    """AC 1: move, write, re-parse: each group's order is the model's, duplicates and the
    disabled bind included, and the written file is the golden."""
    from _golden import assert_matches_golden

    written: list[str] = []

    async def scenario(fake: FakeHyprland) -> None:
        runner = Runner()
        session = session_for(fake, tmp_path, runner)
        session.start()
        await runner.settle()
        assert session.live

        assert session.save_submap(original=None, name="resize", reset_target="")
        assert session.edit_binds(lambda binds: binds.extend(BINDS))
        assert session.move_bind(0, 3)  # "a" past its duplicate "b" and the disabled "w"
        await session.drain()
        await runner.settle()

        model = session.model.entities.binds
        parsed = parse_binds_module(binds_lua(tmp_path))
        assert parsed.ok, parsed.errors
        assert by_group(parsed.binds) == by_group(model)
        assert [bind.keys for bind in by_group(parsed.binds)[None]] == [
            "SUPER + Q",
            "SUPER + W",
            "SUPER + Q",
            "SUPER + E",
        ]
        assert [bind.enabled for bind in by_group(parsed.binds)[None]] == [
            True,
            False,
            True,
            True,
        ]
        assert [submap.name for submap in parsed.submaps] == ["resize"]
        written.append(binds_lua(tmp_path))

    run_with_fake(scenario, FakeHyprland(section_conversation("general")))

    golden = Path(__file__).parent.parent / "golden" / "writer" / "binds-moved.lua"
    assert_matches_golden(written[0], golden, "binds.lua after a drag reorder")
