"""Conflict detection and submap reachability (#66, ADR-0007).

Both are pure functions over the model, so they are settled here, headless. The UI's job
is only to show what these return; the semantics -- what counts as the same Trigger, what
counts as entering a Submap -- are all here.
"""

from __future__ import annotations

from typing import Any

from hyprtweaker.engine.binds_analysis import (
    find_conflicts,
    save_submap,
    unreachable_submaps,
)
from hyprtweaker.engine.model.entities import (
    Bind,
    BindOptions,
    DispatcherCall,
    EntitySet,
    Submap,
)


def exec_bind(keys: str, command: str = "x", **kwargs: Any) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="exec_cmd", positional=(command,)),
        **kwargs,
    )


def submap_bind(keys: str, target: str, **kwargs: Any) -> Bind:
    return Bind(
        keys=keys,
        dispatcher=DispatcherCall(path="submap", positional=(target,)),
        **kwargs,
    )


class TestConflicts:
    def test_no_binds_no_conflicts(self) -> None:
        assert find_conflicts([]) == {}

    def test_different_triggers_do_not_conflict(self) -> None:
        binds = [exec_bind("SUPER + Q"), exec_bind("SUPER + W")]
        assert find_conflicts(binds) == {}

    def test_same_trigger_same_submap_conflicts(self) -> None:
        binds = [exec_bind("SUPER + Q", "a"), exec_bind("SUPER + Q", "b")]
        assert find_conflicts(binds) == {0: (0, 1), 1: (0, 1)}

    def test_the_group_carries_fire_order(self) -> None:
        binds = [
            exec_bind("SUPER + Q", "a"),
            exec_bind("SUPER + W", "b"),
            exec_bind("SUPER + Q", "c"),
            exec_bind("SUPER + Q", "d"),
        ]
        conflicts = find_conflicts(binds)
        assert conflicts[0] == (0, 2, 3)
        assert conflicts[2] == (0, 2, 3)
        assert 1 not in conflicts

    def test_mod_order_and_key_case_do_not_matter(self) -> None:
        # Hyprland resolves keysym names case-insensitively and a modmask is a set, so
        # these two are the same Trigger even though the strings differ.
        binds = [exec_bind("SUPER + SHIFT + Q"), exec_bind("SHIFT + SUPER + q")]
        assert 0 in find_conflicts(binds)

    def test_different_submaps_do_not_conflict(self) -> None:
        binds = [
            exec_bind("SUPER + Q", submap="resize"),
            exec_bind("SUPER + Q", submap="move"),
            exec_bind("SUPER + Q"),  # root
        ]
        assert find_conflicts(binds) == {}

    def test_submap_universal_conflicts_against_every_submap(self) -> None:
        # ADR-0007: `submap_universal` conflicts against all submaps -- the bind fires
        # everywhere, so a same-trigger bind anywhere races it.
        universal = exec_bind("SUPER + Q", options=BindOptions(submap_universal=True))
        binds = [universal, exec_bind("SUPER + Q", submap="resize")]
        assert find_conflicts(binds) == {0: (0, 1), 1: (0, 1)}

    def test_disabled_binds_do_not_conflict(self) -> None:
        binds = [exec_bind("SUPER + Q"), exec_bind("SUPER + Q", enabled=False)]
        assert find_conflicts(binds) == {}

    def test_key_codes_conflict_by_exact_code(self) -> None:
        binds = [exec_bind("SUPER + code:10"), exec_bind("SUPER + code:10")]
        assert 0 in find_conflicts(binds)
        assert find_conflicts([exec_bind("code:10"), exec_bind("code:11")]) == {}

    def test_a_function_valued_action_still_fires_and_still_conflicts(self) -> None:
        # A bind with no dispatcher is read-only in the GUI, but the compositor runs it
        # all the same -- hiding it from conflict detection would hide a real race.
        binds = [Bind(keys="SUPER + Q"), exec_bind("SUPER + Q")]
        assert 0 in find_conflicts(binds)


class TestUnreachable:
    def test_an_entered_submap_is_reachable(self) -> None:
        entities = EntitySet(
            submaps=[Submap(name="resize")],
            binds=[submap_bind("SUPER + R", "resize")],
        )
        assert unreachable_submaps(entities) == set()

    def test_a_submap_nothing_enters_is_unreachable(self) -> None:
        entities = EntitySet(submaps=[Submap(name="resize")])
        assert unreachable_submaps(entities) == {"resize"}

    def test_an_implied_submap_counts_too(self) -> None:
        # A submap that only exists because binds name it -- the imported-config shape.
        entities = EntitySet(binds=[exec_bind("right", submap="resize")])
        assert unreachable_submaps(entities) == {"resize"}

    def test_entry_from_an_unreachable_submap_does_not_count(self) -> None:
        # The only way into "inner" is a bind inside "outer", and nothing enters "outer" --
        # so neither can ever be active.
        entities = EntitySet(
            submaps=[Submap(name="outer"), Submap(name="inner")],
            binds=[submap_bind("I", "inner", submap="outer")],
        )
        assert unreachable_submaps(entities) == {"outer", "inner"}

    def test_entry_from_a_reachable_submap_chains(self) -> None:
        entities = EntitySet(
            submaps=[Submap(name="outer"), Submap(name="inner")],
            binds=[
                submap_bind("SUPER + O", "outer"),
                submap_bind("I", "inner", submap="outer"),
            ],
        )
        assert unreachable_submaps(entities) == set()

    def test_a_disabled_entry_does_not_count(self) -> None:
        entities = EntitySet(
            submaps=[Submap(name="resize")],
            binds=[submap_bind("SUPER + R", "resize", enabled=False)],
        )
        assert unreachable_submaps(entities) == {"resize"}

    def test_a_reset_target_of_a_reachable_submap_is_reachable(self) -> None:
        # Leaving "outer" lands in "landing", so "landing" can be active even though no
        # bind names it.
        entities = EntitySet(
            submaps=[Submap(name="outer", reset_target="landing"), Submap(name="landing")],
            binds=[submap_bind("SUPER + O", "outer")],
        )
        assert unreachable_submaps(entities) == set()

    def test_reset_is_an_exit_not_an_entry(self) -> None:
        # `submap("reset")` returns to root; it enters nothing.
        entities = EntitySet(
            submaps=[Submap(name="resize")],
            binds=[submap_bind("escape", "reset", submap="resize")],
        )
        assert unreachable_submaps(entities) == {"resize"}

    def test_save_creates_a_declared_submap(self) -> None:
        entities = EntitySet()
        save_submap(entities, original=None, name="resize", reset_target="")
        assert [(s.name, s.reset_target) for s in entities.submaps] == [("resize", "")]

    def test_editing_an_implied_submap_declares_it(self) -> None:
        entities = EntitySet(binds=[exec_bind("right", submap="resize")])
        save_submap(entities, original="resize", name="resize", reset_target="landing")
        assert [(s.name, s.reset_target) for s in entities.submaps] == [
            ("resize", "landing")
        ]

    def test_a_rename_cascades_to_everything_the_name_meant(self) -> None:
        entities = EntitySet(
            submaps=[Submap(name="resize"), Submap(name="other", reset_target="resize")],
            binds=[
                submap_bind("SUPER + R", "resize"),
                submap_bind("SUPER + D", "resize", enabled=False),
                exec_bind("right", submap="resize"),
            ],
        )
        save_submap(entities, original="resize", name="grow", reset_target="")

        assert [(s.name, s.reset_target) for s in entities.submaps] == [
            ("grow", ""),
            ("other", "grow"),
        ]
        # Both entry binds retarget -- the disabled one too, or re-enabling it would
        # point at a name that no longer exists.
        assert entities.binds[0].dispatcher is not None
        assert entities.binds[0].dispatcher.positional == ("grow",)
        assert entities.binds[1].dispatcher is not None
        assert entities.binds[1].dispatcher.positional == ("grow",)
        assert entities.binds[2].submap == "grow"
        # And the rename changed no reachability: "grow" is entered as "resize" was,
        # and "other" was unreachable before the rename too (nothing ever entered it).
        assert unreachable_submaps(entities) == {"other"}

    def test_a_rename_does_not_touch_other_submaps_binds(self) -> None:
        entities = EntitySet(
            submaps=[Submap(name="resize"), Submap(name="move")],
            binds=[
                submap_bind("SUPER + R", "resize"),
                submap_bind("SUPER + M", "move"),
                exec_bind("left", submap="move"),
            ],
        )
        save_submap(entities, original="resize", name="grow", reset_target="")
        assert entities.binds[1].dispatcher is not None
        assert entities.binds[1].dispatcher.positional == ("move",)
        assert entities.binds[2].submap == "move"

    def test_a_universal_bind_enters_from_anywhere(self) -> None:
        # `submap_universal` fires in every submap, so its entry works even though the
        # submap that owns it is itself unreachable.
        entities = EntitySet(
            submaps=[Submap(name="island"), Submap(name="target")],
            binds=[
                submap_bind(
                    "SUPER + T",
                    "target",
                    submap="island",
                    options=BindOptions(submap_universal=True),
                ),
            ],
        )
        assert unreachable_submaps(entities) == {"island"}
