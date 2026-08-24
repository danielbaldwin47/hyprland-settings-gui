"""Conflict detection and Submap reachability, as pure functions over the model (#66).

**Conflict** (ADR-0007): same (submap, modmask, trigger) among enabled Binds, with
`submap_universal` conflicting against all submaps. Duplicates are legal Hyprland
semantics -- everything here is advisory, for the warn badges; nothing blocks.

**Unreachable** (ADR-0007): a Submap no bind enters is badged unreachable. "Enters" is
taken seriously rather than syntactically: an entry bind that lives inside a submap
nothing enters can itself never fire, so reachability is a fixpoint from root -- root
binds and `submap_universal` binds are always live, a submap is reachable when a live
enabled bind dispatches into it, and a reachable submap's reset target is reachable too
(leaving it lands there).

Engine, not UI: both questions are about what the compositor would do with the config,
and both are answerable headless, which is where they are tested.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .model.entities import Bind, EntitySet, Submap
from .triggers import parse_trigger

SUBMAP_DISPATCHER = "submap"
"""`hl.dsp.submap("name")` -- the one dispatcher that enters a submap."""

RESET = "reset"
"""`submap("reset")` returns to root: an exit, never an entry."""


def trigger_key(keys: str) -> tuple[tuple[str, ...], str] | None:
    """The canonical identity of a Trigger string, or `None` for an empty one.

    Two spellings collide exactly when the compositor would treat them as the same
    trigger: the modmask is a set (`parse_trigger` already orders and de-duplicates it)
    and keysym names resolve case-insensitively (`triggers.py`), so `SHIFT + SUPER + q`
    and `SUPER + SHIFT + Q` are one key. Prefixed forms (`code:10`, `mouse:272`) come
    through the same casefold unchanged in the ways that matter: their identity is the
    number, not letter case.
    """
    trigger = parse_trigger(keys)
    if not trigger.mods and not trigger.key:
        return None
    return trigger.mods, trigger.key.casefold()


def find_conflicts(binds: Sequence[Bind]) -> dict[int, tuple[int, ...]]:
    """Every conflicted bind index, mapped to its whole conflict group in fire order.

    The group includes the index itself, in list order, because list order *is* fire
    order (ADR-0007) and the badge states it ("fires 1st"). Groups are per-index rather
    than global: a `submap_universal` bind conflicts with same-trigger binds in every
    submap, but two binds in *different* submaps still do not conflict with each other,
    so the universal bind's group can be wider than its neighbours'.

    Disabled binds neither conflict nor are conflicted with -- they do not fire. A bind
    with no dispatcher (a function action, read-only here) still fires and still counts.
    """
    by_trigger: dict[tuple[tuple[str, ...], str], list[int]] = {}
    for index, bind in enumerate(binds):
        if not bind.enabled:
            continue
        if (key := trigger_key(bind.keys)) is not None:
            by_trigger.setdefault(key, []).append(index)

    conflicts: dict[int, tuple[int, ...]] = {}
    for group in by_trigger.values():
        if len(group) < 2:
            continue
        for index in group:
            mine = binds[index]
            rivals = [
                other
                for other in group
                if other != index
                and (
                    mine.options.submap_universal
                    or binds[other].options.submap_universal
                    or binds[other].submap == mine.submap
                )
            ]
            if rivals:
                conflicts[index] = tuple(sorted([index, *rivals]))
    return conflicts


def submap_names(entities: EntitySet) -> list[str]:
    """Every Submap the config has, declared or implied, in model order.

    Implied means a bind names it without a declaration -- the imported-config shape
    (`writer/binds.py` renders those too, for the same reason: dropping them would drop
    their binds).
    """
    seen: dict[str, None] = {name: None for name in (s.name for s in entities.submaps)}
    for bind in entities.binds:
        if bind.submap is not None:
            seen.setdefault(bind.submap, None)
    return list(seen)


def submap_target(bind: Bind) -> str | None:
    """The submap this bind's dispatcher names, enabled or not, or `None`.

    Enabledness deliberately not consulted: a rename must retarget disabled entry binds
    too, or re-enabling one would point it at a name that no longer exists.
    """
    if bind.dispatcher is None or bind.dispatcher.path != SUBMAP_DISPATCHER:
        return None
    if bind.dispatcher.positional:
        target = str(bind.dispatcher.positional[0])
    else:
        values = list(bind.dispatcher.args.values())
        target = str(values[0]) if values else ""
    return target if target and target != RESET else None


def entry_target(bind: Bind) -> str | None:
    """The submap this bind actually enters, or `None` -- disabled binds enter nothing."""
    return submap_target(bind) if bind.enabled else None


def unreachable_submaps(entities: EntitySet) -> set[str]:
    """The Submaps that can never be active -- ADR-0007's unreachable badge.

    A fixpoint rather than one pass, so that a chain of submaps whose only entrance is a
    dead first link comes out unreachable end to end: the badge's claim is "you cannot
    get here", not "no line of the file mentions it".
    """
    names = set(submap_names(entities))
    reachable: set[str] = set()

    entries = [
        (bind, target) for bind in entities.binds if (target := entry_target(bind)) is not None
    ]
    resets = {s.name: s.reset_target for s in entities.submaps if s.reset_target}

    changed = True
    while changed:
        changed = False
        for bind, target in entries:
            live = (
                bind.submap is None or bind.options.submap_universal or bind.submap in reachable
            )
            if live and target not in reachable:
                reachable.add(target)
                changed = True
        for name, target in resets.items():
            if name in reachable and target != RESET and target not in reachable:
                reachable.add(target)
                changed = True

    return names - reachable


def save_submap(
    entities: EntitySet, *, original: str | None, name: str, reset_target: str
) -> None:
    """Create a Submap, or rename one and retune its reset target, in place (#66).

    `original` is `None` for a creation and the current name for an edit. A rename
    cascades everywhere the old name is meaning rather than text: the binds the submap
    owns, the binds that dispatch into it (disabled ones included -- re-enabling must not
    point at a dead name), and any other submap resetting to it. A rename that stranded
    its entry binds would flip the submap to unreachable as a side effect of retitling it.

    An edit of an *implied* submap (named by binds, never declared) declares it: the
    moment a user touches one it has state of its own to keep.
    """
    from dataclasses import replace

    declared = {submap.name: position for position, submap in enumerate(entities.submaps)}

    if original is not None and original in declared:
        position = declared[original]
        entities.submaps[position] = replace(
            entities.submaps[position], name=name, reset_target=reset_target
        )
    else:
        entities.submaps.append(Submap(name=name, reset_target=reset_target))

    if original is None or original == name:
        return

    for position, submap in enumerate(entities.submaps):
        if submap.reset_target == original:
            entities.submaps[position] = replace(submap, reset_target=name)
    for position, bind in enumerate(entities.binds):
        changed = bind
        if changed.submap == original:
            changed = replace(changed, submap=name)
        if submap_target(changed) == original and changed.dispatcher is not None:
            changed = replace(
                changed,
                dispatcher=replace(
                    changed.dispatcher, path=SUBMAP_DISPATCHER, args={}, positional=(name,)
                ),
            )
        if changed is not bind:
            entities.binds[position] = changed
    for position, unbind in enumerate(entities.unbinds):
        if unbind.submap == original:
            entities.unbinds[position] = replace(unbind, submap=name)


def fire_order(group: Iterable[int], index: int) -> int:
    """1-based position of `index` in its conflict group -- the number the badge shows."""
    return list(group).index(index) + 1


__all__ = [
    "entry_target",
    "find_conflicts",
    "fire_order",
    "save_submap",
    "submap_names",
    "submap_target",
    "trigger_key",
    "unreachable_submaps",
]
