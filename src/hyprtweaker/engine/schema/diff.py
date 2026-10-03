"""What changed between two Generated schemas: layer 1 of the release check's diff.

`docs/agents/hyprland-release-check.md` step 2 has the release-check agent classify every
change between the previous newest schema and the new one, and the PR's reviewer read the
result. Nothing in the app reads it: New in grouping reads the `added_in` stamp in the
schema, and Retired detection reads the compositor's live names (ADR-0012). So this module
sits beside the schema code only because it owns the one rule both the stamp and the diff
need: which Options are *added*.

Renames are two classes, because the diff cannot know one. `renamed` is only what the
Overlay's `renamed_from` says, which a person curates in step 3, after reading this
output. `rename_candidates` is what step 2 can see before that: a removed and an added
Option with the same description and default. A candidate is also still listed under
`removed` and `added`, since until someone confirms it, that is what it is.

Deterministic: every collection is sorted by Option name, and the JSON carries no
timestamps and no paths, so the same two schemas always give the same bytes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .generated import GeneratedSchema
from .overlay import Overlay
from .types import GeneratedOption


@dataclass(frozen=True, slots=True)
class Rename:
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class Change:
    """One Option's field, as it was and as it is, in the JSON's own terms."""

    name: str
    before: Any
    after: Any


@dataclass(frozen=True, slots=True)
class SchemaDiff:
    hyprland_version: str
    predecessor: str | None
    """`None` when there was no predecessor: the diff is then empty, not everything-added."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    renamed: tuple[Rename, ...] = ()
    rename_candidates: tuple[Rename, ...] = ()
    retyped: tuple[Change, ...] = ()
    range_changed: tuple[Change, ...] = ()
    enum_map_changed: tuple[Change, ...] = ()
    default_changed: tuple[Change, ...] = ()
    new_sections: tuple[str, ...] = ()
    """Sections and subsections (`general:snap`) the predecessor had no Option under."""

    @property
    def counts(self) -> dict[str, int]:
        return {key: len(value) for key, value in _classes(self).items()}


def _classes(diff: SchemaDiff) -> dict[str, tuple[Any, ...]]:
    return {
        "added": diff.added,
        "removed": diff.removed,
        "renamed": diff.renamed,
        "rename_candidates": diff.rename_candidates,
        "retyped": diff.retyped,
        "range_changed": diff.range_changed,
        "enum_map_changed": diff.enum_map_changed,
        "default_changed": diff.default_changed,
        "new_sections": diff.new_sections,
    }


def added_names(schema: GeneratedSchema, predecessor: GeneratedSchema) -> frozenset[str]:
    """The Options `schema` has that `predecessor` lacks: the one rule for "added".

    `resolve.stamp_added_in` stamps by it and the diff reports by it, so the release-check
    PR and the app's `New in` group agree about what a release added, with one exception:
    a confirmed rename's new name is stamped `added_in` (the generator reads no Overlay) and
    the diff reports it under `renamed`, not `added`.
    """
    return frozenset(option.name for option in schema.options) - frozenset(
        option.name for option in predecessor.options
    )


def _sections(schema: GeneratedSchema) -> frozenset[str]:
    """Every Section and subsection an Option lives under: `a:b:c` gives `a` and `a:b`."""
    found: set[str] = set()
    for option in schema.options:
        parts = option.name.split(":")
        found.update(":".join(parts[:depth]) for depth in range(1, len(parts)))
    return frozenset(found)


def _range(option: GeneratedOption) -> dict[str, Any]:
    vec2 = option.vec2_range
    return {
        "min": option.min,
        "max": option.max,
        "vec2_range": None
        if vec2 is None
        else [vec2.min_x, vec2.min_y, vec2.max_x, vec2.max_y],
    }


def _enum_map(option: GeneratedOption) -> dict[str, Any]:
    return {
        "map": option.map,
        "choices": None if option.choices is None else list(option.choices),
    }


def _default(option: GeneratedOption) -> Any:
    return option.default_raw


def _differences(
    pairs: list[tuple[GeneratedOption, GeneratedOption]],
    view: Callable[[GeneratedOption], Any],
) -> tuple[Change, ...]:
    """The pairs whose `view` differs, named by the new Option and sorted by that name."""
    return tuple(
        sorted(
            (
                Change(new.name, view(old), view(new))
                for old, new in pairs
                if view(old) != view(new)
            ),
            key=lambda change: change.name,
        )
    )


def diff_schemas(
    schema: GeneratedSchema,
    predecessor: GeneratedSchema | None,
    overlay: Overlay | None = None,
) -> SchemaDiff:
    """Classify every change `schema` makes against `predecessor`.

    `overlay` supplies `renamed_from`; without one nothing is `renamed`. With no
    predecessor there is nothing to compare, so the diff is empty.
    """
    if predecessor is None:
        return SchemaDiff(hyprland_version=schema.hyprland_version, predecessor=None)

    current = {option.name: option for option in schema.options}
    earlier = {option.name: option for option in predecessor.options}
    added = added_names(schema, predecessor)
    removed = frozenset(earlier) - frozenset(current)

    renamed = sorted(
        (
            Rename(entry.renamed_from, name)
            for name in added
            if overlay is not None
            and (entry := overlay.entry(name)) is not None
            and entry.renamed_from in removed
        ),
        key=lambda pair: pair.new,
    )
    renamed_old = {pair.old for pair in renamed}
    renamed_new = {pair.new for pair in renamed}

    candidates = sorted(
        (
            Rename(old, new)
            for old in removed - renamed_old
            for new in added - renamed_new
            if earlier[old].description
            and earlier[old].description == current[new].description
            and earlier[old].default_raw == current[new].default_raw
        ),
        key=lambda pair: (pair.new, pair.old),
    )

    # A renamed Option is compared against the Option it was, under its new name.
    pairs = [(earlier[name], current[name]) for name in sorted(current.keys() & earlier.keys())]
    pairs += [(earlier[pair.old], current[pair.new]) for pair in renamed]

    return SchemaDiff(
        hyprland_version=schema.hyprland_version,
        predecessor=predecessor.hyprland_version,
        added=tuple(sorted(added - renamed_new)),
        removed=tuple(sorted(removed - renamed_old)),
        renamed=tuple(renamed),
        rename_candidates=tuple(candidates),
        retyped=_differences(pairs, lambda option: option.type.value),
        range_changed=_differences(pairs, _range),
        enum_map_changed=_differences(pairs, _enum_map),
        default_changed=_differences(pairs, _default),
        new_sections=tuple(sorted(_sections(schema) - _sections(predecessor))),
    )


def _jsonable(item: Rename | Change | str) -> Any:
    if isinstance(item, Rename):
        return {"from": item.old, "to": item.new}
    if isinstance(item, Change):
        return {"name": item.name, "before": item.before, "after": item.after}
    return item


def dumps(diff: SchemaDiff) -> str:
    """The `hyprland-<ver>.diff.json` text, in the same style as the schema file."""
    payload: dict[str, Any] = {
        "hyprland_version": diff.hyprland_version,
        "predecessor": diff.predecessor,
        "counts": diff.counts,
    }
    for key, items in _classes(diff).items():
        payload[key] = [_jsonable(item) for item in items]
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
